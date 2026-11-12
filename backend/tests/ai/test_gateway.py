"""Provider selection, the shared prompt contract, and the hosted-egress guard."""

import json
from pathlib import Path

import httpx
import pytest

from sentinel.ai.gateway import build_gateway
from sentinel.ai.orchestrator import _load_model_json
from sentinel.ai.types import ProviderError
from sentinel.config.secrets import SecretResolver
from sentinel.config.settings import Settings

ASSESSMENT = {
    "assessment": "The fast recovery area is filling.",
    "confidence": "medium",
    "limitations": "Only collected readings are available.",
    "next_steps": ["Check archive log destination space"],
}


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"_env_file": None, "ai_enabled": True, "ai_provider": "local", "ai_model_name": "test-local"}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


@pytest.mark.asyncio
async def test_same_prompt_works_for_local_and_hosted_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["messages"][-1]["content"] == "why is the FRA filling?"
        assert "execute_sql" not in request.content.decode()
        seen.append((request.url.host, request.url.path))
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": json.dumps(ASSESSMENT)}, "prompt_eval_count": 3, "eval_count": 4})
        assert request.headers["authorization"] == "Bearer local-test-secret"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(ASSESSMENT)}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        })

    monkeypatch.setenv("llm_api_key", "local-test-secret")
    transport = httpx.MockTransport(handler)
    async with _client(transport) as client:
        local = build_gateway(_settings(ai_local_url="http://ollama.local"), client=client)
        hosted = build_gateway(_settings(
            ai_provider="hosted", ai_hosted_model="test-hosted", ai_hosted_api_base="http://hosted.local/v1",
            ai_hosted_approved=True, ai_hosted_api_key_secret_ref="secret://sentinel/llm_api_key",
            ai_hosted_privacy_disclosure="Telemetry leaves the network only for this approved hosted model.",
        ), SecretResolver("not-a-fernet-key"), client=client)
        local_reply = await local.generate([{"role": "user", "content": "why is the FRA filling?"}], tools=None, correlation_id="c1")
        hosted_reply = await hosted.generate([{"role": "user", "content": "why is the FRA filling?"}], tools=None, correlation_id="c2")
    assert local_reply.content == hosted_reply.content
    assert local_reply.provider == "local"
    assert hosted_reply.provider == "hosted"
    assert seen == [("ollama.local", "/api/chat"), ("hosted.local", "/v1/chat/completions")]


@pytest.mark.asyncio
async def test_local_outage_does_not_call_a_hosted_provider() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "ollama.local":
            raise AssertionError(request.url.host)
        raise httpx.ConnectError("offline")

    async with _client(httpx.MockTransport(handler)) as client:
        gateway = build_gateway(_settings(ai_local_url="http://ollama.local"), client=client)
        with pytest.raises(ProviderError) as caught:
            await gateway.generate([{"role": "user", "content": "status"}], tools=None, correlation_id="c3")
    assert caught.value.code == "local_provider_unreachable"


@pytest.mark.asyncio
async def test_hosted_provider_fail_closed_without_approval() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"hosted request was sent to {request.url}")

    async with _client(httpx.MockTransport(handler)) as client:
        gateway = build_gateway(_settings(ai_provider="hosted", ai_hosted_api_base="http://hosted.local/v1"), client=client)
        with pytest.raises(ProviderError) as caught:
            await gateway.generate([{"role": "user", "content": "status"}], tools=None, correlation_id="c4")
    assert caught.value.code == "hosted_not_approved"


@pytest.mark.asyncio
async def test_disabled_mode_and_native_tools_do_not_call_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    async with _client(httpx.MockTransport(handler)) as client:
        gateway = build_gateway(_settings(ai_enabled=False, ai_provider="local"), client=client)
        with pytest.raises(ProviderError) as caught:
            await gateway.generate([{"role": "user", "content": "status"}], tools=None, correlation_id="c5")
        assert caught.value.code == "model_disabled"
        local = build_gateway(_settings(), client=client)
        with pytest.raises(ProviderError) as tools:
            await local.generate(
                [{"role": "user", "content": "status"}],
                tools=[{"name": "execute_sql"}],
                correlation_id="c6",
            )
    assert tools.value.code == "native_tools_disabled"


def test_fallback_provider_cannot_be_hosted() -> None:
    with pytest.raises(ValueError, match="ai_fallback_provider"):
        _settings(ai_fallback_provider="hosted")


def test_hosted_vendor_must_be_openai_or_anthropic() -> None:
    with pytest.raises(ValueError, match="ai_hosted_vendor"):
        _settings(ai_hosted_vendor="azure")


@pytest.mark.asyncio
async def test_anthropic_uses_the_messages_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-secret")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-api-key")
        captured["version"] = request.headers.get("anthropic-version")
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "anthropic answer"}],
            "usage": {"input_tokens": 4, "output_tokens": 2},
        })

    settings = _settings(
        ai_provider="hosted", ai_hosted_vendor="anthropic", ai_hosted_model="claude-test",
        ai_hosted_approved=True, ai_hosted_api_key_secret_ref="secret://ANTHROPIC_API_KEY",
        ai_hosted_privacy_disclosure="Sent to Anthropic.",
    )
    async with _client(httpx.MockTransport(handler)) as client:
        gateway = build_gateway(settings, SecretResolver("not-a-fernet-key"), client=client)
        reply = await gateway.generate([
            {"role": "system", "content": "stay grounded"},
            {"role": "user", "content": "question"},
        ], tools=None, correlation_id="anthropic")
    body = captured["body"]
    assert isinstance(body, dict)
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["api_key"] == "anthropic-test-secret"
    assert captured["version"] == "2023-06-01"
    assert captured["authorization"] is None
    assert body["system"] == "stay grounded"
    assert body["messages"] == [{"role": "user", "content": "question"}]
    assert reply.content == "anthropic answer"
    assert reply.prompt_tokens == 4
    assert reply.completion_tokens == 2


@pytest.mark.asyncio
async def test_openai_uses_the_default_chat_completions_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-secret")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "openai answer"}}], "usage": {}})

    settings = _settings(
        ai_provider="hosted", ai_hosted_vendor="openai", ai_hosted_model="gpt-test",
        ai_hosted_approved=True, ai_hosted_api_key_secret_ref="secret://OPENAI_API_KEY",
        ai_hosted_privacy_disclosure="Sent to OpenAI.",
    )
    async with _client(httpx.MockTransport(handler)) as client:
        gateway = build_gateway(settings, SecretResolver("not-a-fernet-key"), client=client)
        reply = await gateway.generate(
            [{"role": "user", "content": "question"}], tools=None, correlation_id="openai",
        )
    body = captured["body"]
    assert isinstance(body, dict)
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["authorization"] == "Bearer openai-test-secret"
    assert body["response_format"] == {"type": "json_object"}
    assert reply.content == "openai answer"
    assert reply.provider == "hosted"


@pytest.mark.asyncio
async def test_hosted_key_is_read_from_the_env_file_without_using_other_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('OPENAI_API_KEY="file-secret"\nORACLE_PASSWORD=leave-this\n', encoding="utf-8")
    monkeypatch.setattr("sentinel.ai.providers.hosted._ENV_FILE", env_file)
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "from file"}}], "usage": {}})

    settings = _settings(
        ai_provider="hosted", ai_hosted_vendor="openai", ai_hosted_model="gpt-test",
        ai_hosted_approved=True, ai_hosted_api_key_secret_ref="secret://OPENAI_API_KEY",
        ai_hosted_privacy_disclosure="Sent to OpenAI.",
    )
    async with _client(httpx.MockTransport(handler)) as client:
        gateway = build_gateway(settings, SecretResolver("not-a-fernet-key"), client=client)
        reply = await gateway.generate(
            [{"role": "user", "content": "question"}], tools=None, correlation_id="file-key",
        )
    assert captured["authorization"] == "Bearer file-secret"
    assert "leave-this" not in captured["authorization"]
    assert reply.content == "from file"


def test_hosted_json_can_be_read_when_the_model_adds_surrounding_text() -> None:
    parsed = _load_model_json('Here is the result:\n{"assessment": "USERS is 5 MB", "confidence": "high"}')
    assert isinstance(parsed, dict)
    assert parsed["assessment"] == "USERS is 5 MB"
