"""Approved hosted chat provider. The API key is resolved at call time and is never logged."""

import os

import httpx

from sentinel.ai.providers.common import client_timeout, reject_native_tools
from sentinel.ai.types import ModelReply, ProviderError
from sentinel.config.secrets import SecretResolver
from sentinel.config.settings import Settings


class HostedChatProvider:
    def __init__(self, settings: Settings, resolver: SecretResolver, client: httpx.AsyncClient | None = None) -> None:
        self.name = "hosted"
        self.model = settings.ai_hosted_model.strip()
        self.api_base = settings.ai_hosted_api_base.rstrip("/")
        self.timeout_seconds = settings.ai_timeout_seconds
        self.max_output_tokens = settings.ai_max_output_tokens
        self._secret_ref = settings.ai_hosted_api_key_secret_ref or ""
        self._resolver = resolver
        self._client = client
        self.approved = bool(
            settings.ai_hosted_approved
            and self.api_base
            and self.model
            and self._secret_ref.startswith("secret://")
            and settings.ai_hosted_privacy_disclosure.strip()
        )

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        tools: list[dict[str, object]] | None,
        structured_schema: dict[str, object] | None,
        correlation_id: str,
    ) -> ModelReply:
        del structured_schema, correlation_id
        reject_native_tools(tools)
        if not self.approved:
            raise ProviderError("hosted_not_approved")
        key = self._api_key()
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_output_tokens,
            "temperature": 0,
        }
        response = await self._send("POST", self._completions_url(), payload, {"Authorization": f"Bearer {key}"})
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("invalid_model_response") from exc
        if not isinstance(body, dict):
            raise ProviderError("invalid_model_response")
        choices = body.get("choices")
        content = ""
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                content = message["content"]
        if not content.strip():
            raise ProviderError("empty_model_response")
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return ModelReply(
            content=content,
            prompt_tokens=_count(usage.get("prompt_tokens") if isinstance(usage, dict) else None),
            completion_tokens=_count(usage.get("completion_tokens") if isinstance(usage, dict) else None),
            provider=self.name,
            model=self.model,
        )

    async def health(self) -> dict[str, str]:
        if not self.approved:
            return {"status": "not_approved"}
        try:
            await self._send("GET", self.api_base, None, None)
        except ProviderError as exc:
            if exc.code == "hosted_provider_unreachable":
                return {"status": "unreachable"}
        return {"status": "reachable"}

    def _api_key(self) -> str:
        if not self._secret_ref.startswith("secret://"):
            raise ProviderError("hosted_secret_ref_required")
        name = self._secret_ref.removeprefix("secret://").rstrip("/").split("/")[-1]
        if not name.isidentifier():
            raise ProviderError("hosted_secret_ref_invalid")
        raw = os.environ.get(name)
        if not raw:
            raise ProviderError("hosted_secret_unavailable")
        resolved = self._resolver.resolve(raw)
        if not resolved:
            raise ProviderError("hosted_secret_unavailable")
        return resolved

    def _completions_url(self) -> str:
        if self.api_base.endswith("/chat/completions"):
            return self.api_base
        if self.api_base.endswith("/v1"):
            return f"{self.api_base}/chat/completions"
        return f"{self.api_base}/v1/chat/completions"

    async def _send(
        self,
        method: str,
        url: str,
        payload: dict[str, object] | None,
        headers: dict[str, str] | None,
    ) -> httpx.Response:
        try:
            if self._client is not None:
                response = await self._client.request(method, url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=client_timeout(self.timeout_seconds), trust_env=False, follow_redirects=False) as client:
                    response = await client.request(method, url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError("hosted_provider_unreachable") from exc
        if response.status_code >= 400:
            raise ProviderError("hosted_provider_rejected")
        return response


def _count(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0
