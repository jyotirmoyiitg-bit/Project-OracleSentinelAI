"""Load and validate YAML rule definitions. Expressions are parsed with a safe regex
grammar and `ast.literal_eval` — never `eval()` or `exec()` — so a malicious or malformed
rule file can only fail validation, not execute arbitrary code or SQL."""

import ast
import re
from pathlib import Path
from typing import Any

import yaml

from sentinel.rules.definitions import SUPPORTED_OPERATORS, RuleCondition, RuleDefinition

# `<namespace.path.segments> <operator> <literal>` — e.g. `storage.tablespace.used_pct > 85`
# or `diagnostic_events.code == 'ORA-00257'`.
_EXPRESSION_PATTERN = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\s*"
    r"(>=|<=|==|!=|>|<)\s*(.+?)\s*$",
)


def parse_expression(expression: str) -> RuleCondition:
    """Parse a rule expression into a safe `RuleCondition`.

    Raises ValueError for anything that is not exactly `<metric path> <op> <literal>`.
    """
    match = _EXPRESSION_PATTERN.match(expression or "")
    if not match:
        raise ValueError(f"Rule expression is not a safe comparison: {expression!r}")
    metric, operator, literal = match.groups()
    if operator not in SUPPORTED_OPERATORS:
        raise ValueError(f"Unsupported operator in rule expression: {operator!r}")
    try:
        value = ast.literal_eval(literal)
    except (ValueError, SyntaxError) as exc:
        raise ValueError(f"Rule expression literal is not a safe constant: {literal!r}") from exc
    if not isinstance(value, (int, float, str, bool)):
        raise ValueError(f"Rule expression literal must be a number, string, or bool: {literal!r}")
    return RuleCondition(metric=metric, operator=operator, value=value)


def _build_rule(entry: dict[str, Any]) -> RuleDefinition:
    rule_id = entry.get("id")
    expression = entry.get("expression")
    if not rule_id or not isinstance(rule_id, str):
        raise ValueError(f"Rule entry is missing a valid 'id': {entry!r}")
    if not expression or not isinstance(expression, str):
        raise ValueError(f"Rule {rule_id!r} is missing a valid 'expression'")
    condition = parse_expression(expression)
    hysteresis = int(entry.get("hysteresis", 1))
    cooldown_minutes = int(entry.get("cooldown_minutes", 30))
    if hysteresis < 1:
        raise ValueError(f"Rule {rule_id!r} hysteresis must be >= 1")
    if cooldown_minutes < 0:
        raise ValueError(f"Rule {rule_id!r} cooldown_minutes must be >= 0")
    return RuleDefinition(
        id=rule_id,
        enabled=bool(entry.get("enabled", True)),
        scope=str(entry.get("scope", "all")),
        expression=expression,
        condition=condition,
        hysteresis=hysteresis,
        cooldown_minutes=cooldown_minutes,
        severity=str(entry.get("severity", "warning")),
        message=str(entry.get("message", "")),
    )


def load_rules_from_text(text: str) -> list[RuleDefinition]:
    """Parse one YAML document's `rules:` list into validated `RuleDefinition`s."""
    document = yaml.safe_load(text) or {}
    entries = document.get("rules", []) if isinstance(document, dict) else []
    rules: list[RuleDefinition] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"Rule entry must be a mapping: {entry!r}")
        rule = _build_rule(entry)
        if rule.id in seen_ids:
            raise ValueError(f"Duplicate rule id: {rule.id}")
        seen_ids.add(rule.id)
        rules.append(rule)
    return rules


def load_rules(path: str | Path) -> list[RuleDefinition]:
    """Load every `*.yaml`/`*.yml` rule file under `path` (file or directory)."""
    root = Path(path)
    if root.is_file():
        files = [root]
    elif root.is_dir():
        files = sorted([*root.glob("*.yaml"), *root.glob("*.yml")])
    else:
        return []
    rules: list[RuleDefinition] = []
    seen_ids: set[str] = set()
    for file in files:
        for rule in load_rules_from_text(file.read_text(encoding="utf-8")):
            if rule.id in seen_ids:
                raise ValueError(f"Duplicate rule id across rule files: {rule.id}")
            seen_ids.add(rule.id)
            rules.append(rule)
    return rules
