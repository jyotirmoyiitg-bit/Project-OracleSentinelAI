"""Memory-structure snapshot for one database. Each reading fails on its own."""

from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel

from sentinel.adapters.oracle.activity import ActivityAccessError, ActivityUnavailable, read_rows
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings

Reader = Callable[[Settings, str, str], Awaitable[tuple[list[dict[str, str]], str]]]

_SKIPPED_SGA = frozenset({
    "maximum sga size",
    "free sga memory available",
    "granule size",
    "startup overhead in shared pool",
})
_MB = 1024 * 1024
_GB = 1024 * _MB
_MISSING_TARGET = "The target parameter was not returned."
_UNNAMED_PROCESS = "The process name was not returned."
_NOT_COLLECTED = "Not collected."
_SINCE_STARTUP = "Since instance startup."
_SGASTAT_DENIED = "V$SGASTAT is not granted."
_NOT_IN_MEMORY = "The in-memory area is not in use."
_NOT_DATA_GUARD = "Not a Data Guard database."
_IN_MEMORY_NAME = "in-memory area size"
_ASMM_ON = "Automatic Shared Memory Management is on."
_ASMM_OFF = "Automatic Shared Memory Management is off."
_AMM_ON = "Automatic Memory Management is on."
_AMM_OFF = "Automatic Memory Management is off."


class MemoryItem(BaseModel):
    name: str
    bytes: int | None
    display: str


class SgaPicture(BaseModel):
    items: list[MemoryItem]
    caption: str
    limitation: str | None = None


class PgaPicture(BaseModel):
    items: list[MemoryItem]
    high: bool
    limitation: str | None = None


class BackgroundProcess(BaseModel):
    program: str
    status: str
    state: str


class Meter(BaseModel):
    percent: float | None
    caption: str
    limitation: str | None = None


class InMemory(BaseModel):
    display: str
    in_use: bool
    limitation: str | None = None


class MemoryManagement(BaseModel):
    lines: list[str]
    limitation: str | None = None


class DataGuardStat(BaseModel):
    name: str
    value: str


class DataGuard(BaseModel):
    stats: list[DataGuardStat]
    limitation: str | None = None


class MemoryPicture(BaseModel):
    sga: SgaPicture
    pga: PgaPicture
    background_processes: list[BackgroundProcess]
    hit_ratio: Meter
    shared_pool_used: Meter
    in_memory: InMemory
    memory_management: MemoryManagement
    data_guard: DataGuard
    result_cache: str = "Not collected."
    collected_at: datetime
    limitations: list[str]
    snapshot: str = "Current instance snapshot."


def format_memory(value: int) -> str:
    """Show a byte count as MB or GB. The raw byte count is not the label."""
    size = abs(value)
    if size >= _GB:
        amount = value / _GB
        unit = "GB"
    else:
        amount = value / _MB
        unit = "MB"
    text = f"{amount:.1f}".removesuffix(".0")
    return f"{text} {unit}"


def _number(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


def _item(name: str, amount: int | None) -> MemoryItem | None:
    if amount is None:
        return None
    return MemoryItem(name=name, bytes=amount, display=format_memory(amount))


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


def _sga(rows: list[dict[str, str]], limitation: str | None) -> SgaPicture:
    pools: list[MemoryItem] = []
    targets: list[str] = []
    for row in rows:
        source = row.get("source") or ""
        if source == "v$parameter":
            amount = _number(row.get("value"))
            if amount is None:
                continue
            targets.append(f"{row.get('name') or 'parameter'} {format_memory(amount)}")
            continue
        name = (row.get("name") or "").strip()
        if not name or name.casefold() in _SKIPPED_SGA or name.casefold() == _IN_MEMORY_NAME:
            continue
        item = _item(name, _number(row.get("bytes")))
        if item is not None:
            pools.append(item)
    caption = ". ".join(targets) + "." if targets else _MISSING_TARGET
    if not pools and limitation is None:
        limitation = "No SGA pool with a byte value was returned."
    return SgaPicture(items=pools, caption=caption, limitation=limitation)


def _pga(rows: list[dict[str, str]], limitation: str | None) -> PgaPicture:
    values = {(row.get("name") or "").casefold(): _number(row.get("value")) for row in rows}
    allocated = values.get("total pga allocated")
    used = values.get("total pga inuse")
    target = values.get("aggregate pga target parameter")
    if target is None:
        target = values.get("pga_aggregate_target")
    items = [
        item for item in (
            _item("Allocated", allocated),
            _item("Used", used),
            _item("Target", target),
        ) if item is not None
    ]
    notes = [limitation] if limitation else []
    if target is None and limitation is None:
        notes.append(_MISSING_TARGET)
    high = target is not None and target > 0 and used is not None and used > target
    return PgaPicture(items=items, high=high, limitation=" ".join(notes) or None)


def _processes(rows: list[dict[str, str]], limitation: str | None) -> tuple[list[BackgroundProcess], str | None]:
    cards = [
        BackgroundProcess(
            program=(row.get("program") or "").strip() or _UNNAMED_PROCESS,
            status=(row.get("status") or "").strip(),
            state=(row.get("state") or "").strip(),
        )
        for row in rows
    ]
    notes = [limitation] if limitation else []
    if len(cards) >= 80:
        notes.append("The process list shows the first 80 background sessions.")
    return cards, " ".join(notes) or None


def _hit_ratio(rows: list[dict[str, str]], limitation: str | None) -> Meter:
    if limitation:
        return Meter(percent=None, caption=_NOT_COLLECTED, limitation=limitation)
    values = {(row.get("name") or "").casefold(): _number(row.get("value")) for row in rows}
    consistent = values.get("consistent gets")
    gets = values.get("db block gets")
    physical = values.get("physical reads")
    denominator = None if consistent is None or gets is None else consistent + gets
    if denominator is None or denominator <= 0 or physical is None or physical > denominator:
        return Meter(percent=None, caption=_NOT_COLLECTED, limitation=None)
    return Meter(percent=round((denominator - physical) * 100 / denominator, 1), caption=_SINCE_STARTUP, limitation=None)


def _shared_pool(sga_rows: list[dict[str, str]], free_rows: list[dict[str, str]], limitation: str | None) -> Meter:
    if limitation:
        caption = _SGASTAT_DENIED if "V_$SGASTAT" in limitation or "V$SGASTAT" in limitation else limitation
        return Meter(percent=None, caption=caption, limitation=limitation)
    size = next(
        (_number(row.get("bytes")) for row in sga_rows if (row.get("name") or "").casefold() == "shared pool size"),
        None,
    )
    free = _number(free_rows[0].get("bytes")) if free_rows else None
    if size is None or size <= 0 or free is None or free > size:
        return Meter(percent=None, caption=_NOT_COLLECTED, limitation=None)
    return Meter(percent=round((size - free) * 100 / size, 1), caption="Shared pool used.", limitation=None)


def _in_memory(rows: list[dict[str, str]], limitation: str | None) -> InMemory:
    if limitation:
        return InMemory(display="", in_use=False, limitation=limitation)
    amount = next(
        (_number(row.get("bytes")) for row in rows if (row.get("name") or "").casefold() == _IN_MEMORY_NAME),
        None,
    )
    if amount is None or amount <= 0:
        return InMemory(display=_NOT_IN_MEMORY, in_use=False, limitation=None)
    return InMemory(display=format_memory(amount), in_use=True, limitation=None)


def _management(rows: list[dict[str, str]], limitation: str | None) -> MemoryManagement:
    if limitation:
        return MemoryManagement(lines=[], limitation=limitation)
    values = {(row.get("name") or "").casefold(): _number(row.get("value")) for row in rows}
    lines: list[str] = []
    missing = False
    for name, enabled, disabled in (
        ("sga_target", _ASMM_ON, _ASMM_OFF),
        ("memory_target", _AMM_ON, _AMM_OFF),
    ):
        amount = values.get(name)
        if name not in values or amount is None:
            missing = True
        elif amount > 0:
            lines.append(enabled)
        else:
            lines.append(disabled)
    return MemoryManagement(lines=lines, limitation=_MISSING_TARGET if missing else None)


def _data_guard(rows: list[dict[str, str]], limitation: str | None) -> DataGuard:
    if limitation:
        return DataGuard(stats=[], limitation=limitation)
    stats = [
        DataGuardStat(name=(row.get("name") or "").strip(), value=(row.get("value") or "").strip())
        for row in rows
        if (row.get("name") or "").strip()
    ]
    if not stats:
        return DataGuard(stats=[], limitation=_NOT_DATA_GUARD)
    return DataGuard(stats=stats, limitation=None)


def assemble(
    sga_rows: list[dict[str, str]],
    pga_rows: list[dict[str, str]],
    process_rows: list[dict[str, str]],
    hit_rows: list[dict[str, str]],
    free_rows: list[dict[str, str]],
    parameter_rows: list[dict[str, str]],
    guard_rows: list[dict[str, str]],
    notes: list[str | None],
    collected_at: datetime | None = None,
) -> MemoryPicture:
    sga_note, pga_note, process_note, hit_note, free_note, parameter_note, guard_note = notes
    processes, process_limitation = _processes(process_rows, process_note)
    sga = _sga(sga_rows, sga_note)
    pga = _pga(pga_rows, pga_note)
    hit_ratio = _hit_ratio(hit_rows, hit_note)
    shared_pool = _shared_pool(sga_rows, free_rows, free_note)
    in_memory = _in_memory(sga_rows, sga_note)
    management = _management(parameter_rows, parameter_note)
    data_guard = _data_guard(guard_rows, guard_note)
    limitations: list[str] = []
    for text in (
        sga.limitation, pga.limitation, process_limitation, hit_ratio.limitation,
        shared_pool.limitation, in_memory.limitation, management.limitation, data_guard.limitation,
    ):
        if text and text not in limitations:
            limitations.append(text)
    return MemoryPicture(
        sga=sga,
        pga=pga,
        background_processes=processes,
        hit_ratio=hit_ratio,
        shared_pool_used=shared_pool,
        in_memory=in_memory,
        memory_management=management,
        data_guard=data_guard,
        collected_at=collected_at or utcnow(),
        limitations=limitations,
    )


_SYNTHETIC: dict[str, tuple[list[dict[str, str]], str]] = {
    "get_sga_components": ([
        {"source": "v$sgainfo", "name": "Buffer Cache Size", "bytes": "6442450944"},
        {"source": "v$sgainfo", "name": "Shared Pool Size", "bytes": "2147483648"},
        {"source": "v$sgainfo", "name": "In-Memory Area Size", "bytes": "0"},
        {"source": "v$sgainfo", "name": "Maximum SGA Size", "bytes": "8589934592"},
        {"source": "v$parameter", "name": "sga_max_size", "value": "8589934592"},
        {"source": "v$parameter", "name": "sga_target", "value": "8589934592"},
    ], ""),
    "get_buffer_hit_ratio": ([
        {"name": "consistent gets", "value": "80"},
        {"name": "db block gets", "value": "20"},
        {"name": "physical reads", "value": "10"},
    ], ""),
    "get_shared_pool_free": ([{"bytes": "1073741824"}], ""),
    "get_memory_management": ([
        {"name": "memory_target", "value": "0"},
        {"name": "sga_target", "value": "8589934592"},
    ], ""),
    "get_data_guard": ([], ""),
    "get_pga_status": ([
        {"source": "v$pgastat", "name": "aggregate PGA target parameter", "value": "2147483648"},
        {"source": "v$pgastat", "name": "total PGA allocated", "value": "1288490188"},
        {"source": "v$pgastat", "name": "total PGA inuse", "value": "419430400"},
    ], ""),
    "get_background_processes": ([
        {"source": "v$session", "program": "oracle (PMON)", "status": "ACTIVE", "state": "WAITING"},
    ], ""),
}


async def _synthetic_reader(_settings: Settings, _alias: str, kind: str) -> tuple[list[dict[str, str]], str]:
    rows, note = _SYNTHETIC[kind]
    return [dict(row) for row in rows], note


async def load_memory(settings: Settings, alias: str, reader: Reader | None = None) -> MemoryPicture:
    """Read SGA, PGA, and background sessions. One failed reading does not clear the others."""
    source = reader or (_synthetic_reader if settings.synthetic_mode else read_rows)
    groups = []
    notes: list[str | None] = []
    for kind in (
        "get_sga_components", "get_pga_status", "get_background_processes",
        "get_buffer_hit_ratio", "get_shared_pool_free", "get_memory_management", "get_data_guard",
    ):
        rows, note = await _read(settings, alias, kind, source)
        groups.append(rows)
        notes.append(note)
    return assemble(groups[0], groups[1], groups[2], groups[3], groups[4], groups[5], groups[6], notes)
