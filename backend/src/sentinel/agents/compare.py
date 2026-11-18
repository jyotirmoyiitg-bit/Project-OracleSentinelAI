"""Compare two stored readings of the same kind and say what changed. Plain code; no model."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.agents.packs import is_restricted, label
from sentinel.ai.safety import sanitize_untrusted
from sentinel.persistence.models import EvidenceSnapshot

# Columns that name a row. A row is matched to its earlier self by the values of these columns.
_IDENTITY = (
    "source", "tablespace_name", "program", "group_no", "thread_no", "name", "pool", "stat_name", "sql_id",
    "owner", "job_name", "segment_name", "file_name", "instance_name", "input_type", "resource", "opname",
    "username", "sid", "occurred_at", "message_text",
)
# Columns that change on every reading and say nothing on their own.
_VOLATILE = frozenset({
    "seconds_in_wait", "last_call_et", "elapsed_time", "uptime_hours", "sofar", "time_remaining",
    "serial_no", "session_id", "cpu_time", "buffer_gets", "disk_reads", "executions",
})
# Readings that are lists of events or moving sessions. Only arrivals and departures are reported.
_LISTS = frozenset({"ora_errors", "sessions", "blocking", "top_sql", "sql_workload", "jobs", "failed_jobs", "backups"})
# Columns that rotate in normal running for one reading.
_IGNORED = {"redo_logs": frozenset({"status"})}
_MAX_LINES = 10


@dataclass
class KindChanges:
    kind: str
    label: str
    restricted: bool
    taken_at: datetime
    previous_at: datetime | None
    lines: list[str] = field(default_factory=list)


def _key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(f"{name}={row.get(name, '')}" for name in _IDENTITY if row.get(name))


def _number(value: str) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _name(row: dict[str, str]) -> str:
    for column in _IDENTITY[1:]:
        value = (row.get(column) or "").strip()
        if value:
            return value[:80]
    return (row.get("source") or "row")[:80]


def _text(value: float) -> str:
    return f"{value:.1f}".removesuffix(".0") if abs(value) < 1_000_000 else f"{value:.0f}"


def _significant(column: str, before: float, after: float) -> bool:
    if before == after:
        return False
    if column.endswith(("pct", "percent")):
        return abs(after - before) >= 2
    base = max(abs(before), abs(after))
    return base > 0 and abs(after - before) / base >= 0.10


def row_changes(kind: str, previous: list[dict[str, str]], current: list[dict[str, str]]) -> list[str]:
    """Lines that say what is new, gone, or different between two readings of one kind."""
    before = {_key(row): row for row in previous}
    after = {_key(row): row for row in current}
    lines: list[str] = []
    added = [after[key] for key in after if key not in before]
    removed = [before[key] for key in before if key not in after]
    if kind in _LISTS:
        if added:
            names = ", ".join(_name(row) for row in added[:4])
            lines.append(f"{len(added)} new: {names}" + (", and more" if len(added) > 4 else ""))
        if removed:
            lines.append(f"{len(removed)} no longer present.")
        if len(current) != len(previous):
            lines.append(f"Row count went from {len(previous)} to {len(current)}.")
        return [sanitize_untrusted(line, 240) for line in lines[:_MAX_LINES]]
    for row in added[:4]:
        lines.append(f"New: {_name(row)}")
    for row in removed[:4]:
        lines.append(f"Gone: {_name(row)}")
    for key, row in after.items():
        earlier = before.get(key)
        if earlier is None:
            continue
        for column, value in row.items():
            if column in _IDENTITY or column in _VOLATILE or column in _IGNORED.get(kind, ()):
                continue
            old = earlier.get(column, "")
            if old == value:
                continue
            new_number, old_number = _number(value), _number(old)
            if new_number is not None and old_number is not None:
                if _significant(column, old_number, new_number):
                    lines.append(f"{_name(row)} {column} went from {_text(old_number)} to {_text(new_number)}")
            else:
                lines.append(f"{_name(row)} {column} changed from {old or 'empty'} to {value or 'empty'}")
            if len(lines) >= _MAX_LINES:
                break
        if len(lines) >= _MAX_LINES:
            break
    return [sanitize_untrusted(line, 240) for line in lines[:_MAX_LINES]]


def latest_pair(
    session: Session, target_id: uuid.UUID, kind: str, before: datetime | None = None,
) -> tuple[EvidenceSnapshot | None, EvidenceSnapshot | None]:
    """The newest good reading of a kind and the good reading before it."""
    query = select(EvidenceSnapshot).where(
        EvidenceSnapshot.target_id == target_id, EvidenceSnapshot.kind == kind, EvidenceSnapshot.status == "ok",
    )
    if before is not None:
        query = query.where(EvidenceSnapshot.taken_at <= before)
    rows = session.scalars(query.order_by(EvidenceSnapshot.taken_at.desc()).limit(2)).all()
    newest = rows[0] if rows else None
    earlier = rows[1] if len(rows) > 1 else None
    return newest, earlier


def changes_for(session: Session, target_id: uuid.UUID, kinds: list[str], admin: bool) -> list[KindChanges]:
    """What changed for each reading between its two newest snapshots. Restricted readings need an admin operator."""
    result: list[KindChanges] = []
    for kind in kinds:
        if is_restricted(kind) and not admin:
            continue
        newest, earlier = latest_pair(session, target_id, kind)
        if newest is None:
            continue
        item = KindChanges(kind, label(kind), is_restricted(kind), newest.taken_at, earlier.taken_at if earlier else None)
        if earlier is not None:
            item.lines = row_changes(kind, list(earlier.rows or []), list(newest.rows or []))
        result.append(item)
    return result
