"""Shared chat-provider contract. Native tool calling stays disabled."""

from typing import Protocol

import httpx

from sentinel.ai.types import ModelReply, ProviderError


class ChatModelProvider(Protocol):
    name: str
    model: str

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        tools: list[dict[str, object]] | None,
        structured_schema: dict[str, object] | None,
        correlation_id: str,
    ) -> ModelReply: ...

    async def health(self) -> dict[str, str]: ...


def reject_native_tools(tools: list[dict[str, object]] | None) -> None:
    if tools:
        raise ProviderError("native_tools_disabled")


def client_timeout(seconds: int) -> httpx.Timeout:
    return httpx.Timeout(seconds)
