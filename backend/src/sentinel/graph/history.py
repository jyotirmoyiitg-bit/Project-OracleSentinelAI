"""Stored counter samples for GraphDB and WaitDB.

The scheduler reads five fixed readings on one Oracle logon and stores the counters as one
row in ``observations``. A page that opens replays the stored samples through the same rate
code it uses for live samples, so the charts and meters are filled before the first refresh.
Nothing here builds a statement: only the readings already in the catalog are run.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.adapters.oracle.activity import ActivityAccessError, ActivityUnavailable, shared_reader
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.graph import activity as activity_readings
from sentinel.graph import wait_activity as wait_activity_readings
from sentinel.graph import waits as wait_readings
from sentinel.graph.activity import RateCounter, Reader
from sentinel.graph.wait_activity import NamedValue
from sentinel.graph.waits import WaitEvent
from sentinel.persistence.models import DatabaseTarget, Observation
from sentinel.persistence.telemetry import GRAPH_SAMPLE_METRIC

logger = logging.getLogger(__name__)

METRIC = GRAPH_SAMPLE_METRIC
SOURCE = "graph_history"
KINDS = ("get_rate_counters", "get_wait_events", "get_wait_cpu", "get_io_counters", "get_file_io")
MAX_SAMPLES = 120
_DATABASE_TIMEOUT_SECONDS = 45
_CONCURRENCY = 4
_DELETE_BATCH = 10000


class StoredFileIo(BaseModel):
    reads: int | None = None
    writes: int | None = None
    read_time_cs: int | None = None
    write_time_cs: int | None = None


class HistorySample(BaseModel):
    """One stored sample, in the shapes the live pictures use."""

    taken_at: datetime
    age_seconds: float
    counters: list[RateCounter]
    events: list[WaitEvent]
    cpu_micro: int | None
    io: list[NamedValue]
    files: StoredFileIo


class GraphHistory(BaseModel):
    samples: list[HistorySample]
    enabled: bool
    interval_seconds: int
    caption: str


def _number(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


def build_payload(groups: dict[str, list[dict[str, str]]], failed: set[str]) -> dict[str, object] | None:
    """Turn reading rows into the stored payload. Returns None when no counter was read."""
    counters = {} if "get_rate_counters" in failed else {
        (row.get("name") or "").strip().casefold(): _number(row.get("value"))
        for row in groups.get("get_rate_counters", [])
    }
    io = {} if "get_io_counters" in failed else {
        (row.get("name") or "").strip().casefold(): _number(row.get("value"))
        for row in groups.get("get_io_counters", [])
    }
    events = [] if "get_wait_events" in failed else [
        {
            "event": (row.get("event") or "").strip(),
            "wait_class": (row.get("wait_class") or "").strip() or "Other",
            "total_waits": _number(row.get("total_waits")),
            "time_waited_micro": _number(row.get("time_waited_micro")),
        }
        for row in groups.get("get_wait_events", []) if (row.get("event") or "").strip()
    ]
    cpu = None
    if "get_wait_cpu" not in failed:
        for row in groups.get("get_wait_cpu", []):
            if (row.get("stat_name") or "").strip() == "DB CPU":
                cpu = _number(row.get("value"))
    files: dict[str, int | None] = {}
    if "get_file_io" not in failed and groups.get("get_file_io"):
        row = groups["get_file_io"][0]
        files = {
            "reads": _number(row.get("reads")),
            "writes": _number(row.get("writes")),
            "read_time_cs": _number(row.get("read_time")),
            "write_time_cs": _number(row.get("write_time")),
        }
    counters = {name: value for name, value in counters.items() if name and value is not None}
    io = {name: value for name, value in io.items() if name and value is not None}
    files = {name: value for name, value in files.items() if value is not None}
    if not counters and not io and not events and cpu is None and not files:
        return None
    return {"counters": counters, "io": io, "events": events, "cpu_micro": cpu, "files": files}


async def _synthetic_reader(settings: Settings, alias: str, kind: str) -> tuple[list[dict[str, str]], str]:
    for module in (activity_readings, wait_readings, wait_activity_readings):
        if kind in module._SYNTHETIC:
            return await module._synthetic_reader(settings, alias, kind)
    raise ActivityUnavailable(None)


async def _read_all(settings: Settings, alias: str, reader: Reader) -> dict[str, object] | None:
    groups: dict[str, list[dict[str, str]]] = {}
    failed: set[str] = set()
    for kind in KINDS:
        try:
            rows, _note = await reader(settings, alias, kind)
        except (ActivityAccessError, ActivityUnavailable):
            failed.add(kind)
            continue
        groups[kind] = rows
    return build_payload(groups, failed)


async def take_sample(settings: Settings, alias: str, reader: Reader | None = None) -> dict[str, object] | None:
    """Read the counters of one database on one logon. A reading that fails is left out of the sample."""
    if reader is not None:
        return await _read_all(settings, alias, reader)
    if settings.synthetic_mode:
        return await _read_all(settings, alias, _synthetic_reader)
    async with shared_reader(settings, alias) as shared:
        return await _read_all(settings, alias, shared)


def store_sample(session: Session, target_id: uuid.UUID, payload: dict[str, object], taken_at: datetime) -> None:
    session.add(Observation(
        target_id=target_id, metric_name=METRIC, timestamp=taken_at, value=None,
        value_str=json.dumps(payload, separators=(",", ":"), sort_keys=True),
        unit="json", labels={}, quality="measured", source=SOURCE,
    ))


def _sample(row: Observation, now: datetime) -> HistorySample | None:
    try:
        payload = json.loads(row.value_str or "")
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    counters = payload.get("counters") if isinstance(payload.get("counters"), dict) else {}
    io = payload.get("io") if isinstance(payload.get("io"), dict) else {}
    files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    try:
        return HistorySample(
            taken_at=row.timestamp,
            age_seconds=max(0.0, (now - row.timestamp).total_seconds()),
            counters=[RateCounter(name=str(name), value=_number(value)) for name, value in counters.items()],
            events=[
                WaitEvent(
                    event=str(item.get("event") or ""),
                    wait_class=str(item.get("wait_class") or "Other"),
                    total_waits=_number(item.get("total_waits")),
                    time_waited_micro=_number(item.get("time_waited_micro")),
                )
                for item in events if isinstance(item, dict) and item.get("event")
            ],
            cpu_micro=_number(payload.get("cpu_micro")),
            io=[NamedValue(name=str(name), value=_number(value)) for name, value in io.items()],
            files=StoredFileIo(**{name: _number(value) for name, value in files.items() if name in StoredFileIo.model_fields}),
        )
    except (TypeError, ValueError):
        return None


def read_history(session: Session, target_id: uuid.UUID, minutes: int, now: datetime | None = None) -> list[HistorySample]:
    """Stored samples of the last minutes, oldest first. PostgreSQL only; Oracle is not read."""
    moment = now or utcnow()
    rows = session.scalars(
        select(Observation).where(
            Observation.target_id == target_id, Observation.metric_name == METRIC,
            Observation.timestamp >= moment - timedelta(minutes=minutes),
        ).order_by(Observation.timestamp.desc()).limit(MAX_SAMPLES),
    ).all()
    samples = [sample for sample in (_sample(row, moment) for row in reversed(rows)) if sample is not None]
    return samples


def caption(settings: Settings, count: int) -> str:
    if not settings.scheduler_enabled or not settings.graph_history_enabled:
        return "Stored history is off. Charts start with the first two samples on this page."
    minutes = max(1, round(settings.graph_history_interval_seconds / 60))
    every = "every minute" if minutes == 1 else f"every {minutes} minutes"
    if count == 0:
        return f"No stored sample yet. One is taken {every} while the scheduler runs."
    return f"Earlier points are stored samples taken {every}. Later points are live samples on this page."


def delete_expired(settings: Settings, sessions: sessionmaker[Session], now: datetime | None = None) -> int:
    cutoff = (now or utcnow()) - timedelta(hours=settings.graph_history_retention_hours)
    with sessions.begin() as session:
        ids = select(Observation.id).where(
            Observation.metric_name == METRIC, Observation.timestamp < cutoff,
        ).limit(_DELETE_BATCH)
        result = session.execute(delete(Observation).where(Observation.id.in_(ids)))
        return int(result.rowcount or 0)


async def sample_fleet(settings: Settings, sessions: sessionmaker[Session]) -> int:
    """Take and store one sample for every enabled database. Returns how many were stored."""
    with sessions() as session:
        targets = [
            (target.id, (target.tns_alias or "").strip())
            for target in session.scalars(select(DatabaseTarget).where(DatabaseTarget.enabled.is_(True)))
        ]
    gate = asyncio.Semaphore(_CONCURRENCY)

    async def one(target_id: uuid.UUID, alias: str) -> tuple[uuid.UUID, dict[str, object] | None, datetime]:
        if not alias:
            return target_id, None, utcnow()
        async with gate:
            try:
                payload = await asyncio.wait_for(take_sample(settings, alias), timeout=_DATABASE_TIMEOUT_SECONDS)
            except TimeoutError:
                payload = None
            except Exception as exc:  # noqa: BLE001 -- one database must not stop the others
                logger.warning("graph history sample failed: %s", type(exc).__name__)
                payload = None
        return target_id, payload, utcnow()

    results = await asyncio.gather(*(one(target_id, alias) for target_id, alias in targets))
    stored = 0
    with sessions.begin() as session:
        for target_id, payload, taken_at in results:
            if payload is None:
                continue
            target = session.get(DatabaseTarget, target_id)
            if target is None or not target.enabled:
                continue
            store_sample(session, target_id, payload, taken_at)
            stored += 1
    return stored
