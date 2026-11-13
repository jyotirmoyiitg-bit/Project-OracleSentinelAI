"""Approved hosted chat provider. The API key is resolved at call time and is never logged."""

import os
from pathlib import Path

import httpx

from sentinel.ai.providers.common import client_timeout, reject_native_tools
from sentinel.ai.types import ModelReply, ProviderError
from sentinel.config.secrets import SecretResolver
from sentinel.config.settings import Settings

OPENAI_API_BASE = "https://api.openai.com/v1"
ANTHROPIC_API_BASE = "https://api.anthropic.com"
_ENV_FILE = Path(str(Settings.model_config["env_file"]))


class HostedChatProvider:
    def __init__(self, settings: Settings, resolver: SecretResolver, client: httpx.AsyncClient | None = None) -> None:
        self.name = "hosted"
        self.vendor = settings.ai_hosted_vendor
        self.model = settings.ai_hosted_model.strip()
        self.api_base = _api_base(settings)
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
        if self.vendor == "anthropic":
            payload = _anthropic_payload(messages, self.model, self.max_output_tokens)
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
            url = _messages_url(self.api_base)
        else:
            payload = {
                "model": self.model,
                "messages": messages,
                "max_tokens": self.max_output_tokens,
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }
            headers = {"Authorization": f"Bearer {key}"}
            url = _completions_url(self.api_base)
        response = await self._send("POST", url, payload, headers)
        return _reply(self.vendor, self.model, response)

    async def health(self) -> dict[str, str]:
        if not self.approved:
            return {"status": "not_approved"}
        try:
            await self._send("GET", self.api_base, None, None)
        except ProviderError:
            return {"status": "unreachable"}
        return {"status": "reachable"}

    def _api_key(self) -> str:
        return resolve_secret(self._secret_ref, self._resolver)

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
                timeout = client_timeout(self.timeout_seconds)
                async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
                    response = await client.request(method, url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError("hosted_provider_unreachable") from exc
        if response.status_code >= 400:
            raise ProviderError("hosted_provider_rejected")
        return response


def _api_base(settings: Settings) -> str:
    explicit = settings.ai_hosted_api_base.strip().rstrip("/")
    if explicit:
        return explicit
    if settings.ai_hosted_vendor == "anthropic":
        return ANTHROPIC_API_BASE
    return OPENAI_API_BASE


def _completions_url(api_base: str) -> str:
    if api_base.endswith("/chat/completions"):
        return api_base
    if api_base.endswith("/v1"):
        return f"{api_base}/chat/completions"
    return f"{api_base}/v1/chat/completions"


def _messages_url(api_base: str) -> str:
    if api_base.endswith("/messages"):
        return api_base
    if api_base.endswith("/v1"):
        return f"{api_base}/messages"
    return f"{api_base}/v1/messages"


def _anthropic_payload(messages: list[dict[str, str]], model: str, max_tokens: int) -> dict[str, object]:
    system = [item["content"] for item in messages if item.get("role") == "system" and item.get("content")]
    dialogue = [_anthropic_message(item) for item in messages if item.get("role") != "system"]
    if not dialogue:
        dialogue = [{"role": "user", "content": ""}]
    payload: dict[str, object] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": dialogue,
    }
    if system:
        payload["system"] = "\n".join(system)
    return payload


def _anthropic_message(item: dict[str, str]) -> dict[str, str]:
    role = item.get("role", "user")
    if role not in {"user", "assistant"}:
        role = "user"
    return {"role": role, "content": item.get("content", "")}


def _reply(vendor: str, model: str, response: httpx.Response) -> ModelReply:
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderError("invalid_model_response") from exc
    if not isinstance(body, dict):
        raise ProviderError("invalid_model_response")
    if vendor == "anthropic":
        content = _anthropic_text(body.get("content"))
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        prompt_tokens = _count(usage.get("input_tokens") if isinstance(usage, dict) else None)
        completion_tokens = _count(usage.get("output_tokens") if isinstance(usage, dict) else None)
    else:
        content = _openai_text(body.get("choices"))
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        prompt_tokens = _count(usage.get("prompt_tokens") if isinstance(usage, dict) else None)
        completion_tokens = _count(usage.get("completion_tokens") if isinstance(usage, dict) else None)
    if not content.strip():
        raise ProviderError("empty_model_response")
    return ModelReply(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        provider="hosted",
        model=model,
    )


def _openai_text(choices: object) -> str:
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return content
    return ""


def _anthropic_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def resolve_secret(secret_ref: str, resolver: SecretResolver) -> str:
    """Return one named secret. Callers must not log the result."""
    if not secret_ref.startswith("secret://"):
        raise ProviderError("hosted_secret_ref_required")
    name = secret_ref.removeprefix("secret://").rstrip("/").split("/")[-1]
    if not name.isidentifier():
        raise ProviderError("hosted_secret_ref_invalid")
    raw = os.environ.get(name) or _env_file_value(name)
    if not raw.strip():
        raise ProviderError("hosted_secret_unavailable")
    resolved = resolver.resolve(raw)
    if not resolved:
        raise ProviderError("hosted_secret_unavailable")
    return resolved


def secret_is_present(secret_ref: str) -> bool:
    """True when the named secret has a value. The value is never returned."""
    if not secret_ref.startswith("secret://"):
        return False
    name = secret_ref.removeprefix("secret://").rstrip("/").split("/")[-1]
    if not name.isidentifier():
        return False
    raw = os.environ.get(name) or _env_file_value(name)
    return bool(raw.strip())


def _env_file_value(name: str) -> str:
    """Read one named value from the project .env. Other lines are ignored."""
    if not _ENV_FILE.is_file():
        return ""
    prefix = f"{name}="
    try:
        lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").strip()
        if stripped.startswith(prefix):
            return _unquote(stripped[len(prefix):].strip())
    return ""


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _count(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0
