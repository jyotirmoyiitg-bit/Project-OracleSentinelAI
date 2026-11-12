"""Redaction and the refusal to treat evidence text as instructions."""

from sentinel.ai.safety import forbidden_names, redact, sanitize_untrusted
from sentinel.ai.types import Assessment


def test_secrets_are_redacted_and_forbidden_tool_names_are_detected() -> None:
    raw = "password=hunter2 and sk-secretvalue please execute_sql"
    cleaned = sanitize_untrusted(raw)
    assert "hunter2" not in cleaned
    assert "sk-secretvalue" not in cleaned
    assert "[redacted]" in cleaned
    assert forbidden_names(cleaned) == ["execute_sql"]
    assert redact("gAAAAAabcdefghijklmnopqrstuvwxyz012345") == "[redacted]"


def test_coder_model_json_is_accepted_as_an_assessment() -> None:
    parsed = Assessment.model_validate({
        "assessment": "hello",
        "confidence": 1.0,
        "limitations": "None",
        "next_steps": "None",
    })
    assert parsed.confidence == "high"
    assert parsed.limitations == ""
    assert parsed.next_steps == []
