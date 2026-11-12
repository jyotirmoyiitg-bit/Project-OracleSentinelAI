"""Copilot contracts shared by the gateway, tools, and API."""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ProviderError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ModelReply:
    content: str
    prompt_tokens: int
    completion_tokens: int
    provider: str
    model: str


class Assessment(BaseModel):
    assessment: str = Field(max_length=4000)
    confidence: Literal["low", "medium", "high"] = "low"
    limitations: str = Field(default="", max_length=1000)
    next_steps: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("confidence", mode="before")
    @classmethod
    def confidence_from_model(cls, value: object) -> str:
        number: float | None = None
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"low", "medium", "high"}:
                return text
            try:
                number = float(text)
            except ValueError:
                return "low"
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            return "low"
        else:
            number = float(value)
        if number > 1:
            number = min(number / 100, 1)
        if number >= 0.67:
            return "high"
        if number >= 0.34:
            return "medium"
        return "low"

    @field_validator("limitations", mode="before")
    @classmethod
    def limitations_from_model(cls, value: object) -> str:
        if not isinstance(value, str):
            return ""
        text = value.strip()
        if text.lower() in {"none", "n/a", "null"}:
            return ""
        return text

    @field_validator("next_steps", mode="before")
    @classmethod
    def steps_from_model(cls, value: object) -> list[object]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            if not text or text.lower() in {"none", "n/a", "null"}:
                return []
            return [line.strip(" -") for line in text.splitlines() if line.strip()]
        if isinstance(value, list):
            return value
        return []

    @field_validator("assessment", "limitations")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("next_steps")
    @classmethod
    def trim_steps(cls, value: list[str]) -> list[str]:
        return [str(item).strip()[:200] for item in value if str(item).strip()][:6]


@dataclass
class EvidenceCitation:
    kind: str
    label: str
    source: str
    detail: str
    target_id: str | None = None
    document_path: str | None = None
    heading: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "source": self.source,
            "detail": self.detail,
            "target_id": self.target_id,
            "document_path": self.document_path,
            "heading": self.heading,
        }


@dataclass
class ToolOutcome:
    name: str
    decision: Literal["allowed", "denied"]
    summary: str
    citations: list[EvidenceCitation] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    duration_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.name,
            "decision": self.decision,
            "summary": self.summary,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
        }


@dataclass
class Answer:
    content: str
    provider: str
    model: str
    llm_used: bool
    provider_status: str
    disclosure: str
    intent: str
    citations: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    guided_commands: list[dict[str, str]]
    prompt_tokens: int
    completion_tokens: int
    correlation_id: str
    message_id: str
