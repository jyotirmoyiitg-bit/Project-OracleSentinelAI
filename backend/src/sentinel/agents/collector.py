"""Collector: read fixed readings from one database and store the rows as a snapshot.

No model is called here. A reading is one of the fixed statements; nothing from an operator or a
model reaches Oracle. The database session is not held while Oracle is being read.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.agents.config import effective
from sentinel.agents.packs import ALL_KINDS, SENSITIVE_KINDS, is_restricted, kinds_for, pack_of
from sentinel.ai.activity import SYNTHETIC, TOOL_NAMES, ActivityReading
from sentinel.ai.safety import digest, sanitize_untrusted
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.persistence.models import DatabaseTarget, EvidenceSnapshot

logger = logging.getLogger(__name__)
_MAX_ROWS = 80
_collecting: set[uuid.UUID] = set()


class CollectionRefused(Exception):
    """A snapshot was not collected. The reason is safe to show."""

    def __init__(self, reason: str, code: str) -> None:
        self.reason = reason
        self.code = code
        super().__init__(reason)


@dataclass
class SnapshotItem:
    kind: str
    status: str
    row_count: int
    restricted: bool
    note: str = ""


@dataclass
class SnapshotBatch:
    batch_id: uuid.UUID
    target_id: uuid.UUID
    target_name: str
    taken_at: datetime
    items: list[SnapshotItem] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)

    @property
    def answered(self) -> int:
        return sum(1 for item in self.items if item.status == "ok")


async def read_kinds(settings: Settings, alias: str, kinds: list[str]) -> dict[str, ActivityReading]:
    """Run the fixed readings for one database. One readings server process serves the whole list."""
    if settings.synthetic_mode:
        return {
            kind: ActivityReading(kind, TOOL_NAMES[kind], "ok", [dict(row) for row in SYNTHETIC[kind]], "")
            for kind in kinds
        }
    from sentinel.mcp.client import ask_readings

    try:
        answers = await ask_readings(settings, [TOOL_NAMES[kind] for kind in kinds], alias)
    except Exception:  # noqa: BLE001 -- process and driver text must not reach a stored note
        answers = {}
    readings: dict[str, ActivityReading] = {}
    for kind in kinds:
        tool = TOOL_NAMES[kind]
        status, rows, note = answers.get(tool, ("unavailable", [], "The readings server could not be reached."))
        if status not in {"ok", "not_granted", "unavailable"}:
            status = "unavailable"
        readings[kind] = ActivityReading(kind, tool, status, rows, note)
    return readings


def last_collected(session: Session, target_id: uuid.UUID) -> datetime | None:
    return session.scalar(select(func.max(EvidenceSnapshot.taken_at)).where(EvidenceSnapshot.target_id == target_id))


async def collect_snapshot(
    settings: Settings,
    sessions: sessionmaker[Session],
    target_id: uuid.UUID,
    *,
    packs: tuple[str, ...] | None = None,
    kinds: list[str] | None = None,
    run_id: uuid.UUID | None = None,
    respect_gap: bool = True,
) -> SnapshotBatch:
    """Collect packs (or named readings) from one database and store them as one batch.

    Refused when agents are off for the database, when it is disabled, when another collection of
    it is running, or when the last one is more recent than the minimum gap.
    """
    with sessions() as session:
        target = session.get(DatabaseTarget, target_id)
        if target is None:
            raise CollectionRefused("The database was not found.", "not_found")
        if not target.enabled:
            raise CollectionRefused("This database is disabled.", "disabled")
        applied = effective(session, settings, target_id)
        if not applied.enabled:
            raise CollectionRefused("Agents are turned off for this database.", "agents_off")
        alias, name = target.tns_alias or "", target.display_name
        previous = last_collected(session, target_id)
    wanted = list(dict.fromkeys(kinds)) if kinds is not None else kinds_for(packs or applied.packs, applied.collect_sensitive)
    left_out = [kind for kind in wanted if kind in SENSITIVE_KINDS and not applied.collect_sensitive]
    wanted = [kind for kind in wanted if kind in ALL_KINDS and kind not in left_out]
    if not wanted:
        raise CollectionRefused("No reading is left to collect for this database.", "nothing")
    if respect_gap and previous is not None and applied.min_gap_seconds > 0:
        age = (utcnow() - previous).total_seconds()
        if age < applied.min_gap_seconds:
            wait = int(applied.min_gap_seconds - age)
            raise CollectionRefused(
                f"A snapshot of this database was collected {int(age)} seconds ago. Try again in {wait} seconds.",
                "too_soon",
            )
    if target_id in _collecting:
        raise CollectionRefused("A snapshot of this database is being collected now.", "busy")
    _collecting.add(target_id)
    try:
        readings = await read_kinds(settings, alias, wanted)
    finally:
        _collecting.discard(target_id)
    batch = SnapshotBatch(uuid.uuid4(), target_id, name, utcnow(), left_out=left_out)
    with sessions.begin() as session:
        for kind in wanted:
            reading = readings[kind]
            rows = [
                {str(key)[:64]: str(value)[:2000] for key, value in row.items()}
                for row in reading.rows[:_MAX_ROWS]
            ]
            note = sanitize_untrusted(reading.note, 300) if reading.note else ""
            session.add(EvidenceSnapshot(
                batch_id=batch.batch_id, run_id=run_id, target_id=target_id, pack=pack_of(kind), kind=kind,
                taken_at=batch.taken_at, status=reading.status, row_count=len(rows), rows=rows, note=note,
                digest=digest({"kind": kind, "rows": rows}), restricted=is_restricted(kind),
            ))
            batch.items.append(SnapshotItem(kind, reading.status, len(rows), is_restricted(kind), note))
    return batch


async def collect_fleet(
    settings: Settings, sessions: sessionmaker[Session], run_id: uuid.UUID | None = None,
) -> tuple[list[SnapshotBatch], list[tuple[str, str]]]:
    """Collect the default packs from every enabled database. Returns the batches and (database, reason) refusals."""
    with sessions() as session:
        targets = [
            (target.id, target.display_name)
            for target in session.scalars(
                select(DatabaseTarget).where(DatabaseTarget.enabled.is_(True)).order_by(DatabaseTarget.display_name).limit(500),
            )
        ]
    gate = asyncio.Semaphore(settings.ai_fleet_concurrency)
    batches: list[SnapshotBatch] = []
    refused: list[tuple[str, str]] = []

    async def one(target_id: uuid.UUID, name: str) -> None:
        async with gate:
            try:
                batches.append(await collect_snapshot(settings, sessions, target_id, run_id=run_id))
            except CollectionRefused as exc:
                refused.append((name, exc.reason))
            except Exception:  # noqa: BLE001 -- one database must not stop the others
                logger.warning("fleet snapshot failed for one database")
                refused.append((name, "The snapshot could not be collected."))

    await asyncio.gather(*(one(target_id, name) for target_id, name in targets))
    batches.sort(key=lambda batch: batch.target_name)
    return batches, sorted(refused)


def delete_expired(settings: Settings, sessions: sessionmaker[Session]) -> int:
    """Delete snapshots older than each database's retention. Runs and findings are kept."""
    removed = 0
    with sessions.begin() as session:
        target_ids = session.scalars(select(EvidenceSnapshot.target_id).distinct()).all()
        now = utcnow()
        for target_id in target_ids:
            days = effective(session, settings, target_id).retention_days
            result = session.execute(
                delete(EvidenceSnapshot).where(
                    EvidenceSnapshot.target_id == target_id, EvidenceSnapshot.taken_at < now - timedelta(days=days),
                ),
            )
            removed += result.rowcount or 0
    return removed
