"""Ollama-compatible local chat provider. The Ollama Python package is not required."""

import httpx

from sentinel.ai.providers.common import client_timeout, reject_native_tools
from sentinel.ai.providers.hosted import resolve_secret
from sentinel.ai.types import ModelReply, ProviderError
from sentinel.config.secrets import SecretResolver


class LocalChatProvider:
    def __init__(
        self,
        api_base: str,
        model: str,
        timeout_seconds: int,
        max_output_tokens: int,
        client: httpx.AsyncClient | None = None,
        *,
        secret_ref: str = "",
        resolver: SecretResolver | None = None,
    ) -> None:
        self.name = "local"
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self._client = client
        self._secret_ref = secret_ref.strip()
        self._resolver = resolver

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        tools: list[dict[str, object]] | None,
        structured_schema: dict[str, object] | None,
        correlation_id: str,
    ) -> ModelReply:
        del structured_schema
        reject_native_tools(tools)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {"num_predict": self.max_output_tokens},
        }
        body = await self._post("/api/chat", payload, correlation_id)
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("empty_model_response")
        return ModelReply(
            content=content,
            prompt_tokens=_count(body.get("prompt_eval_count")),
            completion_tokens=_count(body.get("eval_count")),
            provider=self.name,
            model=self.model,
        )

    async def health(self) -> dict[str, str]:
        try:
            response = await self._send("GET", "/api/tags", None)
        except ProviderError:
            return {"status": "unreachable"}
        return {"status": "reachable", "http_status": str(response.status_code)}

    async def _post(self, path: str, payload: dict[str, object], correlation_id: str) -> dict[str, object]:
        del correlation_id
        response = await self._send("POST", path, payload)
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("invalid_model_response") from exc
        if not isinstance(body, dict):
            raise ProviderError("invalid_model_response")
        return body

    async def _send(self, method: str, path: str, payload: dict[str, object] | None) -> httpx.Response:
        headers = self._headers()
        try:
            if self._client is not None:
                response = await self._client.request(method, f"{self.api_base}{path}", json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=client_timeout(self.timeout_seconds), trust_env=False, follow_redirects=False) as client:
                    response = await client.request(method, f"{self.api_base}{path}", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError("local_provider_unreachable") from exc
        if response.status_code >= 400:
            raise ProviderError("local_provider_rejected")
        return response

    def _headers(self) -> dict[str, str] | None:
        if not self._secret_ref:
            return None
        if self._resolver is None:
            raise ProviderError("local_secret_unavailable")
        try:
            key = resolve_secret(self._secret_ref, self._resolver)
        except ProviderError as exc:
            raise ProviderError("local_secret_unavailable") from exc
        return {"Authorization": f"Bearer {key}"}


def _count(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0
