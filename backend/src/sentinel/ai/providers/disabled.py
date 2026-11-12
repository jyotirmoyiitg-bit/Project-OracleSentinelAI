"""No-op provider used when the copilot must not call a model."""

from sentinel.ai.providers.common import reject_native_tools
from sentinel.ai.types import ModelReply, ProviderError


class DisabledChatProvider:
    name = "disabled"
    model = ""

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        tools: list[dict[str, object]] | None,
        structured_schema: dict[str, object] | None,
        correlation_id: str,
    ) -> ModelReply:
        del messages, structured_schema, correlation_id
        reject_native_tools(tools)
        raise ProviderError("model_disabled")

    async def health(self) -> dict[str, str]:
        return {"status": "disabled"}
