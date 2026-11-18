"""Agent settings: a system default from the configuration table, and an optional value per database."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.agents.packs import parse_packs
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.persistence.models import AgentDatabaseSetting

# Settings a database may override, with the label the portal shows.
OVERRIDABLE: dict[str, str] = {
    "agents_enabled": "Agents enabled",
    "agent_snapshot_packs": "Packs collected",
    "agent_collect_sensitive": "Collect SQL text and session user names",
    "agent_snapshot_retention_days": "Snapshot retention (days)",
    "agent_snapshot_min_gap_seconds": "Minimum gap between collections (seconds)",
    "agent_auto_investigate": "Investigate a new critical incident automatically",
}


class SettingError(Exception):
    """A database override cannot be stored."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class AgentSettings:
    enabled: bool
    packs: tuple[str, ...]
    collect_sensitive: bool
    retention_days: int
    min_gap_seconds: int
    auto_investigate: bool


def _overrides(session: Session, target_id: uuid.UUID) -> dict[str, Any]:
    rows = session.scalars(select(AgentDatabaseSetting).where(AgentDatabaseSetting.target_id == target_id)).all()
    return {row.key: row.value for row in rows if row.key in OVERRIDABLE}


def _values(settings: Settings, overrides: dict[str, Any]) -> dict[str, Any]:
    values = {key: getattr(settings, key) for key in OVERRIDABLE}
    for key, value in overrides.items():
        try:
            values[key] = _checked(settings, key, value)
        except SettingError:
            continue  # a stored value that is no longer valid falls back to the system default
    # The system switch is the master. A database can opt out; it cannot opt in while the system is off.
    values["agents_enabled"] = bool(settings.agents_enabled) and bool(values["agents_enabled"])
    return values


def effective(session: Session, settings: Settings, target_id: uuid.UUID) -> AgentSettings:
    """The settings that apply to one database: its own value where set, otherwise the system default."""
    values = _values(settings, _overrides(session, target_id))
    return AgentSettings(
        enabled=bool(values["agents_enabled"]),
        packs=parse_packs(values["agent_snapshot_packs"]),
        collect_sensitive=bool(values["agent_collect_sensitive"]),
        retention_days=int(values["agent_snapshot_retention_days"]),
        min_gap_seconds=int(values["agent_snapshot_min_gap_seconds"]),
        auto_investigate=bool(values["agent_auto_investigate"]),
    )


def describe(session: Session, settings: Settings, target_id: uuid.UUID) -> list[dict[str, Any]]:
    """Each overridable setting with its system default, the database's own value, and what applies."""
    overrides = _overrides(session, target_id)
    values = _values(settings, overrides)
    return [
        {
            "key": key,
            "label": label,
            "system_value": getattr(settings, key),
            "database_value": overrides.get(key),
            "overridden": key in overrides,
            "value": values[key],
        }
        for key, label in OVERRIDABLE.items()
    ]


def _checked(settings: Settings, key: str, value: Any) -> Any:
    """Validate one value with the same rules the system setting has."""
    if key not in OVERRIDABLE:
        raise SettingError("That setting cannot be set for one database.")
    expected = type(getattr(settings, key))
    if expected is bool and not isinstance(value, bool):
        raise SettingError("That setting takes true or false.")
    if expected is int and (isinstance(value, bool) or not isinstance(value, int)):
        raise SettingError("That setting takes a whole number.")
    if expected is str and not isinstance(value, str):
        raise SettingError("That setting takes text.")
    merged = settings.model_dump()
    merged[key] = value
    try:
        checked = Settings.model_validate(merged)
    except ValueError as exc:
        raise SettingError("That value is not valid for this setting.") from exc
    return getattr(checked, key)


def set_override(
    session: Session, settings: Settings, target_id: uuid.UUID, key: str, value: Any, principal: str,
) -> Any:
    """Store one database value, or remove it when value is None so the system default applies again."""
    if key not in OVERRIDABLE:
        raise SettingError("That setting cannot be set for one database.")
    row = session.get(AgentDatabaseSetting, (target_id, key))
    if value is None:
        if row is not None:
            session.delete(row)
            session.flush()
        return None
    checked = _checked(settings, key, value)
    if row is None:
        session.add(AgentDatabaseSetting(
            target_id=target_id, key=key, value=checked, changed_by=principal[:200], changed_at=utcnow(),
        ))
    else:
        row.value = checked
        row.changed_by = principal[:200]
        row.changed_at = utcnow()
    session.flush()
    return checked
