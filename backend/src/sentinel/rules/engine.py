"""Threshold evaluation with hysteresis, cooldown, and maintenance suppression.

This module is intentionally persistence-agnostic: it accepts and returns plain
dataclasses so unit tests never need a database, and callers (the collector job)
own loading/saving `RuleState` rows.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sentinel.rules.definitions import RuleDefinition

TARGET_SCOPE = ""  # scope_key used for target-level (non-dimensional) rules


@dataclass
class RuleContext:
    """Everything a rule evaluation pass needs for one target at one point in time."""

    target_id: str
    now: datetime
    metrics: dict[str, float] = field(default_factory=dict)
    scoped_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    event_codes: dict[str, int] = field(default_factory=dict)
    maintenance: bool = False


@dataclass
class RuleStateView:
    """Hysteresis/cooldown memory for one (rule, scope_key) pair on one target."""

    consecutive_matches: int = 0
    last_fired_at: datetime | None = None


@dataclass
class RuleFiring:
    rule: RuleDefinition
    scope_key: str
    observed: Any
    message: str


class RulesEngine:
    def __init__(self, rules: list[RuleDefinition]):
        self.rules = [rule for rule in rules if rule.enabled]

    def _observations_for(self, rule: RuleDefinition, context: RuleContext) -> dict[str, Any]:
        if rule.is_event_rule:
            field_name = rule.condition.metric.split(".", 1)[1]
            if field_name != "code":
                return {}
            return {code: code for code, count in context.event_codes.items() if count > 0}
        if rule.condition.metric in context.scoped_metrics:
            return dict(context.scoped_metrics[rule.condition.metric])
        if rule.condition.metric in context.metrics:
            return {TARGET_SCOPE: context.metrics[rule.condition.metric]}
        return {}

    def evaluate(
        self, context: RuleContext, states: dict[tuple[str, str], RuleStateView],
    ) -> tuple[list[RuleFiring], dict[tuple[str, str], RuleStateView]]:
        """Return firings plus the full updated state map (including non-matching keys reset to zero)."""
        firings: list[RuleFiring] = []
        updated: dict[tuple[str, str], RuleStateView] = dict(states)
        if context.maintenance:
            return [], updated
        for rule in self.rules:
            observations = self._observations_for(rule, context)
            for scope_key, observed in observations.items():
                key = (rule.id, scope_key)
                state = updated.get(key, RuleStateView())
                if rule.condition.matches(observed):
                    consecutive = state.consecutive_matches + 1
                    cooldown_elapsed = (
                        state.last_fired_at is None
                        or context.now - state.last_fired_at >= timedelta(minutes=rule.cooldown_minutes)
                    )
                    if consecutive >= rule.hysteresis and cooldown_elapsed:
                        firings.append(RuleFiring(
                            rule=rule, scope_key=scope_key, observed=observed,
                            message=_render(rule.message, scope_key, observed),
                        ))
                        updated[key] = RuleStateView(consecutive_matches=consecutive, last_fired_at=context.now)
                    else:
                        updated[key] = RuleStateView(consecutive_matches=consecutive, last_fired_at=state.last_fired_at)
                else:
                    updated[key] = RuleStateView(consecutive_matches=0, last_fired_at=state.last_fired_at)
        return firings, updated


def _render(template: str, scope_key: str, value: Any) -> str:
    try:
        return template.format(tablespace_name=scope_key, value=value)
    except (KeyError, IndexError):
        return template
