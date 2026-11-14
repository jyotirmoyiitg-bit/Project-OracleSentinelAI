"""Service, host, and server-process snapshot for one database. Each reading fails on its own."""

from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel

from sentinel.adapters.oracle.activity import ActivityAccessError, ActivityUnavailable, read_rows
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.graph.memory import format_memory

Reader = Callable[[Settings, str, str], Awaitable[tuple[list[dict[str, str]], str]]]

_NOT_COLLECTED = "Not collected."
_NO_STARTUP = "The startup time was not returned."
_NO_DATABASE = "The database name was not returned."
_NOT_PDB = "Not a pluggable database."
_NO_HOST = "The host did not return that statistic."
_CPU_CAPTION = "Since instance startup."


class ServiceLine(BaseModel):
    label: str
    value: str


class HostPicture(BaseModel):
    cpu_percent: float | None
    cpu_caption: str
    run_queue: str | None
    free_memory: str | None
    limitation: str | None = None


class ProcessCount(BaseModel):
    name: str
    count: int | None
    limitation: str | None = None


class RateCounter(BaseModel):
    name: str
    value: int | None


class ActivityPicture(BaseModel):
    service: list[ServiceLine]
    host: HostPicture
    server_processes: list[ProcessCount]
    counters: list[RateCounter]
    collected_at: datetime
    limitations: list[str]


_COUNTERS = ("physical reads", "redo entries", "parse count (total)", "execute count")


def _number(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


def _permission(exc: ActivityAccessError) -> str:
    return f"Grant SELECT on {exc.object_name} to the monitoring account, then ask again."


def _unavailable(exc: ActivityUnavailable) -> str:
    text = "The database could not be read just now."
    if exc.code is not None:
        return f"{text} Oracle returned error {exc.code}."
    return text


async def _read(settings: Settings, alias: str, kind: str, reader: Reader) -> tuple[list[dict[str, str]], str | None]:
    try:
        rows, note = await reader(settings, alias, kind)
    except ActivityAccessError as exc:
        return [], _permission(exc)
    except ActivityUnavailable as exc:
        return [], _unavailable(exc)
    return rows, note or None


def _first(rows: list[dict[str, str]]) -> dict[str, str]:
    return rows[0] if rows else {}


def _uptime(rows: list[dict[str, str]], limitation: str | None) -> str:
    if limitation:
        return limitation
    row = _first(rows)
    days, hours, minutes = _number(row.get("up_days")), _number(row.get("up_hours")), _number(row.get("up_minutes"))
    if days is None or hours is None or minutes is None:
        return _NO_STARTUP
    return f"{days}d {hours}h {minutes}m"


def _database(rows: list[dict[str, str]], limitation: str | None) -> str:
    if limitation:
        return limitation
    name = (_first(rows).get("name") or "").strip()
    return name or _NO_DATABASE


def _pdb(rows: list[dict[str, str]], limitation: str | None) -> str:
    if limitation:
        return limitation
    name = (_first(rows).get("name") or "").strip()
    return name or _NOT_PDB


def _sessions(rows: list[dict[str, str]]) -> dict[str, int | None]:
    row = _first(rows)
    return {key: _number(row.get(key)) for key in (
        "total_sessions", "total_users", "active_users", "dedicated_sessions", "shared_sessions", "dispatchers", "parallel_query",
    )}


def _count_text(value: int | None, limitation: str | None) -> str:
    if limitation:
        return limitation
    if value is None:
        return _NOT_COLLECTED
    return str(value)


def _host(rows: list[dict[str, str]], limitation: str | None) -> HostPicture:
    values = {(row.get("stat_name") or "").upper(): (row.get("value") or "").strip() for row in rows}
    busy, idle = _number(values.get("BUSY_TIME")), _number(values.get("IDLE_TIME"))
    total = (busy or 0) + (idle or 0)
    percent = round(busy * 100 / total, 1) if busy is not None and idle is not None and total > 0 else None
    load = values.get("LOAD")
    free = _number(values.get("FREE_MEMORY_BYTES"))
    return HostPicture(
        cpu_percent=None if limitation else percent,
        cpu_caption=limitation or (_CPU_CAPTION if percent is not None else _NO_HOST),
        run_queue=None if limitation or not load else load,
        free_memory=None if limitation or free is None else format_memory(free),
        limitation=limitation,
    )


def _oval(name: str, count: int | None, limitation: str | None) -> ProcessCount:
    return ProcessCount(name=name, count=None if limitation else count, limitation=limitation)


def _counters(rows: list[dict[str, str]], limitation: str | None) -> list[RateCounter]:
    values = {(row.get("name") or "").casefold(): _number(row.get("value")) for row in rows}
    return [RateCounter(name=name, value=None if limitation else values.get(name)) for name in _COUNTERS]


def assemble(
    uptime_rows: list[dict[str, str]],
    database_rows: list[dict[str, str]],
    pdb_rows: list[dict[str, str]],
    session_rows: list[dict[str, str]],
    host_rows: list[dict[str, str]],
    job_rows: list[dict[str, str]],
    counter_rows: list[dict[str, str]],
    notes: list[str | None],
    collected_at: datetime | None = None,
) -> ActivityPicture:
    uptime_note, database_note, pdb_note, session_note, host_note, job_note, counter_note = notes
    counts = _sessions(session_rows)
    jobs = _number(_first(job_rows).get("running_jobs"))
    service = [
        ServiceLine(label="Uptime", value=_uptime(uptime_rows, uptime_note)),
        ServiceLine(label="Container database", value=_database(database_rows, database_note)),
        ServiceLine(label="Pluggable database", value=_pdb(pdb_rows, pdb_note)),
        ServiceLine(label="Response", value=_NOT_COLLECTED),
        ServiceLine(label="Total users", value=_count_text(counts["total_users"], session_note)),
        ServiceLine(label="Active users", value=_count_text(counts["active_users"], session_note)),
        ServiceLine(label="Average active", value=_NOT_COLLECTED),
    ]
    processes = [
        _oval("Dedicated", counts["dedicated_sessions"], session_note),
        _oval("Shared", counts["shared_sessions"], session_note),
        _oval("Dispatchers", counts["dispatchers"], session_note),
        _oval("Parallel query", counts["parallel_query"], session_note),
        _oval("Job queue", jobs, job_note),
        _oval("Total", counts["total_sessions"], session_note),
    ]
    host = _host(host_rows, host_note)
    counters = _counters(counter_rows, counter_note)
    limitations = [note for note in (uptime_note, database_note, pdb_note, session_note, host_note, job_note, counter_note) if note]
    return ActivityPicture(
        service=service,
        host=host,
        server_processes=processes,
        counters=counters,
        collected_at=collected_at or utcnow(),
        limitations=limitations,
    )


_SYNTHETIC: dict[str, tuple[list[dict[str, str]], str]] = {
    "get_service_uptime": ([{"up_days": "2", "up_hours": "11", "up_minutes": "0"}], ""),
    "get_service_database": ([{"name": "ORCL12C"}], ""),
    "get_service_pdb": ([{"name": "PDBORCL"}], ""),
    "get_server_sessions": ([{
        "total_sessions": "101", "total_users": "3", "active_users": "1",
        "dedicated_sessions": "3", "shared_sessions": "1", "dispatchers": "1", "parallel_query": "96",
    }], ""),
    "get_host_stats": ([
        {"stat_name": "BUSY_TIME", "value": "12"},
        {"stat_name": "IDLE_TIME", "value": "88"},
        {"stat_name": "LOAD", "value": "0.16"},
        {"stat_name": "FREE_MEMORY_BYTES", "value": "4294967296"},
    ], ""),
    "get_job_queue": ([{"running_jobs": "0"}], ""),
    "get_rate_counters": ([
        {"name": "physical reads", "value": "100"},
        {"name": "redo entries", "value": "40"},
        {"name": "parse count (total)", "value": "20"},
        {"name": "execute count", "value": "80"},
    ], ""),
}


async def _synthetic_reader(_settings: Settings, _alias: str, kind: str) -> tuple[list[dict[str, str]], str]:
    rows, note = _SYNTHETIC[kind]
    return [dict(row) for row in rows], note


async def load_activity(settings: Settings, alias: str, reader: Reader | None = None) -> ActivityPicture:
    """Read service, host, and server-process counts. One failed reading does not clear the others."""
    source = reader or (_synthetic_reader if settings.synthetic_mode else read_rows)
    groups = []
    notes: list[str | None] = []
    for kind in (
        "get_service_uptime", "get_service_database", "get_service_pdb",
        "get_server_sessions", "get_host_stats", "get_job_queue", "get_rate_counters",
    ):
        rows, note = await _read(settings, alias, kind, source)
        groups.append(rows)
        notes.append(note)
    return assemble(groups[0], groups[1], groups[2], groups[3], groups[4], groups[5], groups[6], notes)
