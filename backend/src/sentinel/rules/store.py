"""Reconciles YAML rule files with the `rule_definitions` table.

YAML remains the source of truth for new rules and their expressions; the database only
stores operator-controlled overrides (enabled/severity/cooldown/hysteresis) so the API can
let a DBA disable or retune a rule without editing files.
"""

from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.persistence.models import RuleDefinition as RuleDefinitionRow
from sentinel.rules.definitions import RuleDefinition


def sync_rule_definitions(session: Session, rules: list[RuleDefinition]) -> list[RuleDefinition]:
    """Upsert YAML rules into `rule_definitions` and return specs with DB overrides applied."""
    rows = {row.id: row for row in session.scalars(select(RuleDefinitionRow))}
    merged: list[RuleDefinition] = []
    for rule in rules:
        row = rows.get(rule.id)
        if row is None:
            session.add(RuleDefinitionRow(
                id=rule.id, enabled=rule.enabled, scope=rule.scope, expression=rule.expression,
                hysteresis=rule.hysteresis, cooldown_minutes=rule.cooldown_minutes,
                severity=rule.severity, message=rule.message, source="yaml",
            ))
            merged.append(rule)
            continue
        if row.source == "override":
            merged.append(replace(
                rule, enabled=row.enabled, severity=row.severity,
                cooldown_minutes=row.cooldown_minutes, hysteresis=row.hysteresis,
            ))
        else:
            row.scope = rule.scope
            row.expression = rule.expression
            row.message = rule.message
            merged.append(rule)
    return merged
