"""Select one chat provider. A local failure never falls through to a hosted model."""

import httpx

from sentinel.ai.providers.disabled import DisabledChatProvider
from sentinel.ai.providers.hosted import HostedChatProvider, secret_is_present
from sentinel.ai.providers.local import LocalChatProvider
from sentinel.ai.types import ModelReply, ProviderError
from sentinel.config.secrets import SecretResolver
from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller, is_admin_operator

ChatProvider = DisabledChatProvider | LocalChatProvider | HostedChatProvider

LOCAL_DISCLOSURE = (
    "A local model may read the redacted evidence for this answer. "
    "The evidence is sent only to the configured local endpoint."
)
ANTHROPIC_DISCLOSURE = "Questions and redacted evidence are sent to Anthropic."
OPENAI_DISCLOSURE = "Questions and redacted evidence are sent to OpenAI."
DISABLED_DISCLOSURE = (
    "The language model is disabled. This answer uses collected metrics, incidents, and document search."
)


class ChatGateway:
    def __init__(self, provider: ChatProvider, disclosure: str, enabled: bool) -> None:
        self.provider = provider
        self.disclosure = disclosure
        self.enabled = enabled

    @property
    def name(self) -> str:
        return self.provider.name

    @property
    def model(self) -> str:
        return self.provider.model

    @property
    def available(self) -> bool:
        return self.enabled and not isinstance(self.provider, DisabledChatProvider)

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        tools: list[dict[str, object]] | None,
        correlation_id: str,
    ) -> ModelReply:
        if not self.available:
            raise ProviderError("model_disabled")
        return await self.provider.generate(
            messages, tools=tools, structured_schema=None, correlation_id=correlation_id,
        )

    async def health(self) -> dict[str, object]:
        status = await self.provider.health()
        return {
            "enabled": self.enabled,
            "selected_provider": self.name,
            "model": self.model,
            "fallback_provider": "disabled",
            "disclosure": self.disclosure,
            "health": status,
        }


def build_gateway(
    settings: Settings,
    resolver: SecretResolver | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> ChatGateway:
    """Build the configured provider. Hosted is never installed as a fallback."""
    active = SecretResolver(settings.secret_key) if resolver is None else resolver
    if not settings.ai_enabled or settings.ai_provider == "disabled":
        return ChatGateway(DisabledChatProvider(), DISABLED_DISCLOSURE, settings.ai_enabled)
    if settings.ai_provider == "local":
        profile = local_profile(settings)
        return ChatGateway(
            LocalChatProvider(
                str(profile["base"]), str(profile["model"]), int(profile["timeout"]),
                int(profile["max_output_tokens"]), client,
                secret_ref=str(profile["secret_ref"]), resolver=active,
            ),
            str(profile["disclosure"]),
            True,
        )
    hosted = HostedChatProvider(settings, active, client)
    disclosure = settings.ai_hosted_privacy_disclosure.strip() or DISABLED_DISCLOSURE
    if not hosted.approved:
        return ChatGateway(hosted, disclosure, True)
    return ChatGateway(hosted, disclosure, True)


MODEL_CHOICES = ("local", "anthropic", "openai")


def choice_allowed(settings: Settings, caller: Caller) -> bool:
    """Only the sentinel admin may switch models, and only outside production."""
    return (
        settings.app_env in {"dev", "test"}
        and is_admin_operator(caller.role)
        and caller.principal.strip().lower() == "sentinel"
    )


def model_choice_payload(settings: Settings, caller: Caller) -> dict[str, object]:
    if not choice_allowed(settings, caller):
        return {"allowed": False, "choices": []}
    local = local_profile(settings)
    anthropic = hosted_profile(settings, "anthropic")
    openai = hosted_profile(settings, "openai")
    return {
        "allowed": True,
        "choices": [
            {
                "id": "local", "label": "Local model", "detail": local["model"],
                "ready": _choice_ready(str(local["secret_ref"])),
                "disclosure": local["disclosure"],
            },
            {
                "id": "anthropic", "label": "Hosted Claude", "detail": anthropic["model"],
                "ready": secret_is_present(str(anthropic["secret_ref"])),
                "disclosure": anthropic["disclosure"],
            },
            {
                "id": "openai", "label": "Hosted OpenAI", "detail": openai["model"],
                "ready": secret_is_present(str(openai["secret_ref"])),
                "disclosure": openai["disclosure"],
            },
        ],
    }


def resolve_gateway(
    settings: Settings,
    caller: Caller,
    choice: str | None,
    resolver: SecretResolver | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> ChatGateway:
    """Use the selected model for one answer. Everyone else keeps the configured provider."""
    if choice not in MODEL_CHOICES or not choice_allowed(settings, caller):
        return build_gateway(settings, resolver, client=client)
    active = SecretResolver(settings.secret_key) if resolver is None else resolver
    if choice == "local":
        profile = local_profile(settings)
        return ChatGateway(
            LocalChatProvider(
                str(profile["base"]), str(profile["model"]), int(profile["timeout"]),
                int(profile["max_output_tokens"]), client,
                secret_ref=str(profile["secret_ref"]), resolver=active,
            ),
            str(profile["disclosure"]),
            True,
        )
    profile = hosted_profile(settings, choice)
    selected = _hosted_settings(settings, choice, profile)
    return ChatGateway(HostedChatProvider(selected, active, client), str(profile["disclosure"]), True)


def _choice_ready(secret_ref: str) -> bool:
    """A local endpoint with no key reference is ready. A named key must be present."""
    return not secret_ref or secret_is_present(secret_ref)


def _or_shared(specific: int, shared: int) -> int:
    return specific if specific > 0 else shared


def local_profile(settings: Settings) -> dict[str, object]:
    return {
        "model": settings.ai_local_model.strip() or settings.ai_model_name,
        "base": settings.ai_local_url,
        "secret_ref": settings.ai_local_api_key_secret_ref.strip(),
        "disclosure": settings.ai_local_privacy_disclosure.strip() or LOCAL_DISCLOSURE,
        "timeout": _or_shared(settings.ai_local_timeout_seconds, settings.ai_timeout_seconds),
        "max_output_tokens": _or_shared(settings.ai_local_max_output_tokens, settings.ai_max_output_tokens),
    }


def hosted_profile(settings: Settings, vendor: str) -> dict[str, object]:
    """Connection settings for one portal choice. Empty Anthropic fields keep the configured Claude setup."""
    if vendor == "openai":
        return {
            "model": settings.ai_openai_model.strip() or "gpt-4o",
            "base": settings.ai_openai_api_base.strip(),
            "secret_ref": settings.ai_openai_api_key_secret_ref.strip() or "secret://OPENAI_API_KEY",
            "disclosure": settings.ai_openai_privacy_disclosure.strip() or OPENAI_DISCLOSURE,
            "timeout": _or_shared(settings.ai_openai_timeout_seconds, settings.ai_timeout_seconds),
            "max_output_tokens": _or_shared(settings.ai_openai_max_output_tokens, settings.ai_max_output_tokens),
        }
    model = settings.ai_anthropic_model.strip()
    base = settings.ai_anthropic_api_base.strip()
    ref = settings.ai_anthropic_api_key_secret_ref.strip()
    disclosure = settings.ai_anthropic_privacy_disclosure.strip()
    if settings.ai_hosted_vendor == "anthropic":
        model = model or settings.ai_hosted_model.strip()
        base = base or settings.ai_hosted_api_base.strip()
        hosted_ref = settings.ai_hosted_api_key_secret_ref or ""
        if not ref and hosted_ref.startswith("secret://"):
            ref = hosted_ref
        disclosure = disclosure or settings.ai_hosted_privacy_disclosure.strip()
    return {
        "model": model or "claude-sonnet-4-5",
        "base": base,
        "secret_ref": ref or "secret://ANTHROPIC_API_KEY",
        "disclosure": disclosure or ANTHROPIC_DISCLOSURE,
        "timeout": _or_shared(settings.ai_anthropic_timeout_seconds, settings.ai_timeout_seconds),
        "max_output_tokens": _or_shared(settings.ai_anthropic_max_output_tokens, settings.ai_max_output_tokens),
    }


def _hosted_settings(settings: Settings, vendor: str, profile: dict[str, object]) -> Settings:
    return settings.model_copy(update={
        "ai_enabled": True,
        "ai_provider": "hosted",
        "ai_hosted_vendor": vendor,
        "ai_hosted_model": str(profile["model"]),
        "ai_hosted_api_base": str(profile["base"]),
        "ai_hosted_api_key_secret_ref": str(profile["secret_ref"]),
        "ai_hosted_approved": True,
        "ai_hosted_privacy_disclosure": str(profile["disclosure"]),
        "ai_timeout_seconds": int(profile["timeout"]),
        "ai_max_output_tokens": int(profile["max_output_tokens"]),
    })


def hosted_is_configured(settings: Settings) -> bool:
    ref = settings.ai_hosted_api_key_secret_ref or ""
    return bool(settings.ai_hosted_approved and settings.ai_hosted_model.strip() and ref.startswith("secret://"))
