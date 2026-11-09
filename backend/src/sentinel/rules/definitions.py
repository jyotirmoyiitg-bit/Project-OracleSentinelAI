"""Rule definition models — safe predicates only, never `eval()` or arbitrary SQL."""

from dataclasses import dataclass
from typing import Any

SUPPORTED_OPERATORS = (">=", "<=", "==", "!=", ">", "<")


@dataclass(frozen=True)
class RuleCondition:
    """A single safe comparison: `<namespace.path> <operator> <literal>`."""

    metric: str
    operator: str
    value: Any

    def __post_init__(self) -> None:
        if self.operator not in SUPPORTED_OPERATORS:
            raise ValueError(f"Unsupported rule operator: {self.operator}")

    def matches(self, observed: Any) -> bool:
        if observed is None:
            return False
        try:
            if self.operator == ">":
                return observed > self.value
            if self.operator == ">=":
                return observed >= self.value
            if self.operator == "<":
                return observed < self.value
            if self.operator == "<=":
                return observed <= self.value
            if self.operator == "==":
                return observed == self.value
            return observed != self.value
        except TypeError:
            # Mismatched types (e.g. comparing a string metric with a number) never match.
            return False


@dataclass(frozen=True)
class RuleDefinition:
    """One monitoring rule: a safe condition plus hysteresis/cooldown/notification metadata."""

    id: str
    enabled: bool
    scope: str
    expression: str
    condition: RuleCondition
    hysteresis: int = 1
    cooldown_minutes: int = 30
    severity: str = "warning"
    message: str = ""

    @property
    def is_event_rule(self) -> bool:
        """Event-sourced rules key off `diagnostic_events.*` fields rather than observed metrics."""
        return self.condition.metric.startswith("diagnostic_events.")
