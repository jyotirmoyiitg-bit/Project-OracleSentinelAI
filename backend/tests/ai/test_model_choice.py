"""Model switching is limited to the sentinel admin outside production."""

import json

from sentinel.ai.gateway import choice_allowed, model_choice_payload, resolve_gateway
from sentinel.ai.providers.hosted import HostedChatProvider
from sentinel.knowledge.acl import Caller


def _hosted(settings, **updates):
    return settings.model_copy(update={
        "app_env": "dev",
        "ai_enabled": True,
        "ai_provider": "hosted",
        "ai_hosted_vendor": "anthropic",
        "ai_hosted_model": "claude-sonnet-4-5",
        "ai_hosted_api_base": "https://api.anthropic.com",
        "ai_hosted_api_key_secret_ref": "secret://ANTHROPIC_API_KEY",
        "ai_hosted_approved": True,
        "ai_hosted_privacy_disclosure": "Questions and redacted evidence are sent to the selected model.",
        "ai_openai_model": "gpt-4o",
        "ai_openai_api_base": "",
        "ai_openai_api_key_secret_ref": "secret://OPENAI_API_KEY",
        **updates,
    })


def test_only_the_sentinel_admin_can_switch_outside_production(settings) -> None:
    dev = _hosted(settings)
    assert choice_allowed(dev, Caller("admin", "sentinel")) is True
    assert choice_allowed(dev, Caller("platform_admin", "sentinel")) is True
    assert choice_allowed(dev, Caller("admin", "someone-else")) is False
    assert choice_allowed(dev, Caller("operator", "sentinel")) is False
    assert choice_allowed(_hosted(settings, app_env="test"), Caller("admin", "sentinel")) is True
    assert choice_allowed(_hosted(settings, app_env="production"), Caller("admin", "sentinel")) is False


def test_openai_choice_does_not_reuse_the_claude_endpoint(settings) -> None:
    gateway = resolve_gateway(_hosted(settings), Caller("admin", "sentinel"), "openai")
    provider = gateway.provider
    assert isinstance(provider, HostedChatProvider)
    assert provider.vendor == "openai"
    assert provider.model == "gpt-4o"
    assert provider.api_base == "https://api.openai.com/v1"
    configured = resolve_gateway(_hosted(settings), Caller("operator", "sentinel"), "openai")
    assert configured.model == "claude-sonnet-4-5"


def test_each_model_keeps_its_own_disclosure(settings) -> None:
    configured = _hosted(
        settings,
        ai_hosted_privacy_disclosure="Questions and redacted evidence are sent to Anthropic.",
    )
    openai = resolve_gateway(configured, Caller("admin", "sentinel"), "openai")
    claude = resolve_gateway(configured, Caller("admin", "sentinel"), "anthropic")
    assert openai.disclosure == "Questions and redacted evidence are sent to OpenAI."
    assert "Anthropic" not in openai.disclosure
    assert isinstance(openai.provider, HostedChatProvider)
    assert openai.provider.approved is True
    assert claude.disclosure == "Questions and redacted evidence are sent to Anthropic."
    custom = resolve_gateway(
        _hosted(settings, ai_openai_privacy_disclosure="OpenAI lab disclosure."),
        Caller("admin", "sentinel"),
        "openai",
    )
    assert custom.disclosure == "OpenAI lab disclosure."


def test_portal_choice_uses_its_own_connection(settings) -> None:
    configured = _hosted(settings).model_copy(update={
        "ai_timeout_seconds": 30,
        "ai_max_output_tokens": 600,
        "ai_anthropic_model": "claude-haiku",
        "ai_anthropic_api_base": "https://anthropic.example",
        "ai_anthropic_api_key_secret_ref": "secret://ANTHROPIC_API_KEY",
        "ai_anthropic_privacy_disclosure": "Sent only to Anthropic.",
        "ai_anthropic_timeout_seconds": 40,
        "ai_anthropic_max_output_tokens": 800,
        "ai_openai_model": "gpt-4o-mini",
        "ai_openai_api_base": "https://openai.example/v1",
        "ai_openai_api_key_secret_ref": "secret://OPENAI_API_KEY",
        "ai_openai_privacy_disclosure": "Sent only to OpenAI.",
        "ai_openai_timeout_seconds": 25,
        "ai_openai_max_output_tokens": 500,
        "ai_local_url": "http://127.0.0.1:11434",
        "ai_local_model": "qwen",
        "ai_local_api_key_secret_ref": "",
        "ai_local_privacy_disclosure": "Stays on the local endpoint.",
        "ai_local_timeout_seconds": 90,
        "ai_local_max_output_tokens": 200,
    })
    caller = Caller("admin", "sentinel")
    default = resolve_gateway(configured, caller, None)
    claude = resolve_gateway(configured, caller, "anthropic")
    openai = resolve_gateway(configured, caller, "openai")
    local = resolve_gateway(configured, caller, "local")
    assert default.model == "claude-sonnet-4-5"
    assert isinstance(claude.provider, HostedChatProvider)
    assert claude.provider.model == "claude-haiku"
    assert claude.provider.api_base == "https://anthropic.example"
    assert claude.provider.timeout_seconds == 40
    assert claude.provider.max_output_tokens == 800
    assert claude.provider._secret_ref == "secret://ANTHROPIC_API_KEY"
    assert claude.disclosure == "Sent only to Anthropic."
    assert isinstance(openai.provider, HostedChatProvider)
    assert openai.provider.model == "gpt-4o-mini"
    assert openai.provider.api_base == "https://openai.example/v1"
    assert openai.provider.timeout_seconds == 25
    assert openai.provider.max_output_tokens == 500
    assert openai.provider._secret_ref == "secret://OPENAI_API_KEY"
    assert openai.disclosure == "Sent only to OpenAI."
    assert local.name == "local"
    assert local.model == "qwen"
    assert local.provider.api_base == "http://127.0.0.1:11434"
    assert local.provider.timeout_seconds == 90
    assert local.provider.max_output_tokens == 200
    assert local.provider._secret_ref == ""
    assert local.disclosure == "Stays on the local endpoint."


def test_local_choice_stays_on_the_local_endpoint(settings) -> None:
    gateway = resolve_gateway(_hosted(settings), Caller("admin", "sentinel"), "local")
    assert gateway.name == "local"
    assert gateway.model == settings.ai_model_name
    assert "hosted" not in gateway.disclosure.lower()


def test_choice_payload_names_models_without_secrets(settings, monkeypatch) -> None:
    monkeypatch.setattr("sentinel.ai.gateway.secret_is_present", lambda ref: ref.endswith("ANTHROPIC_API_KEY"))
    payload = model_choice_payload(_hosted(settings), Caller("admin", "sentinel"))
    encoded = json.dumps(payload)
    assert "sk-" not in encoded
    assert "secret://" not in encoded
    assert "OPENAI_API_KEY=" not in encoded
    choices = {item["id"]: item for item in payload["choices"]}
    assert choices["anthropic"]["ready"] is True
    assert choices["openai"]["ready"] is False
    assert choices["local"]["ready"] is True
    assert "Anthropic" not in choices["openai"]["disclosure"]
    assert "OpenAI" in choices["openai"]["disclosure"]
    assert "local endpoint" in choices["local"]["disclosure"]
    hidden = model_choice_payload(_hosted(settings, app_env="production"), Caller("admin", "sentinel"))
    assert hidden == {"allowed": False, "choices": []}
