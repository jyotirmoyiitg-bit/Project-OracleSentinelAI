"""Select one chat provider. A local failure never falls through to a hosted model."""

import httpx

from sentinel.ai.providers.disabled import DisabledChatProvider
from sentinel.ai.providers.hosted import HostedChatProvider
from sentinel.ai.providers.local import LocalChatProvider
from sentinel.ai.types import ModelReply, ProviderError
from sentinel.config.secrets import SecretResolver
from sentinel.config.settings import Settings

ChatProvider = DisabledChatProvider | LocalChatProvider | HostedChatProvider

LOCAL_DISCLOSURE = (
    "A local model may read the redacted evidence for this answer. "
    "The evidence is sent only to the configured local endpoint."
)
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
        return ChatGateway(
            LocalChatProvider(
                settings.ai_local_url, settings.ai_model_name, settings.ai_timeout_seconds,
                settings.ai_max_output_tokens, client,
            ),
            LOCAL_DISCLOSURE,
            True,
        )
    hosted = HostedChatProvider(settings, active, client)
    disclosure = settings.ai_hosted_privacy_disclosure.strip() or DISABLED_DISCLOSURE
    if not hosted.approved:
        return ChatGateway(hosted, disclosure, True)
    return ChatGateway(hosted, disclosure, True)


def hosted_is_configured(settings: Settings) -> bool:
    ref = settings.ai_hosted_api_key_secret_ref or ""
    return bool(settings.ai_hosted_approved and settings.ai_hosted_api_base.strip() and ref.startswith("secret://"))
