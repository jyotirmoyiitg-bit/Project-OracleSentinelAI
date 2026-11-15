"""Lock, blocking, parse, buffer-busy, and I/O readings for one database.

Rates and charts are computed later, on the page. A failed reading leaves the other sections.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel

from sentinel.adapters.oracle.activity import ActivityAccessError, ActivityUnavailable, read_rows
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.graph.waits import WaitEvent

Reader = Callable[[Settings, str, str], Awaitable[tuple[list[dict[str, str]], str]]]

_LOCK_CAPTION = "Gets, waits, and wait time are since instance startup."
_BLOCK_EMPTY = "No data"
_BLOCK_CAPTION = "These are current sessions waiting for a lock. At most 20 are shown."
_PARSE_CAPTION = "Counts and times are since instance startup."
_BUFFER_CAPTION = "Event totals and block class are since instance startup. Tablespace is sessions waiting now."
_MODES = {
    0: "None",
    1: "Null",
    2: "Row-S (SS)",
    3: "Row-X (SX)",
    4: "Share",
    5: "S/Row-X (SSX)",
    6: "Exclusive",
}
_KINDS = (
    "get_enqueue_stats",
    "get_blocking_locks",
    "get_parse_counts",
    "get_parse_time",
    "get_library_cache",
    "get_buffer_busy",
    "get_buffer_classes",
    "get_buffer_users",
    "get_buffer_tablespaces",
    "get_io_counters",
    "get_file_io",
)


class LockStat(BaseModel):
    name: str
    gets: int | None
    waits: int | None
    wait_time_ms: int | None


class LockPicture(BaseModel):
    locks: list[LockStat]
    limitation: str | None
    caption: str


class BlockingLock(BaseModel):
    sid: int | None
    serial_no: int | None
    username: str
    pid: str
    lock_type: str
    mode_wanted: str
    object_name: str


class BlockingPicture(BaseModel):
    locks: list[BlockingLock]
    limitation: str | None
    empty: str
    caption: str


class NamedValue(BaseModel):
    name: str
    value: int | None


class LibraryCache(BaseModel):
    namespace: str
    gets: int | None
    gethits: int | None
    reloads: int | None
    invalidations: int | None


class ParsePicture(BaseModel):
    counters: list[NamedValue]
    times: list[NamedValue]
    library: list[LibraryCache]
    limitations: list[str]
    caption: str


class BufferClass(BaseModel):
    name: str
    count: int | None
    time_cs: int | None


class BufferUser(BaseModel):
    username: str
    sid: int | None
    total_waits: int | None
    waited_ms: int | None


class BufferTablespace(BaseModel):
    name: str
    sessions: int | None


class BufferBusyPicture(BaseModel):
    events: list[WaitEvent]
    classes: list[BufferClass]
    users: list[BufferUser]
    tablespaces: list[BufferTablespace]
    limitations: list[str]
    caption: str


class FileIo(BaseModel):
    reads: int | None
    writes: int | None
    read_time_cs: int | None
    write_time_cs: int | None
    limitation: str | None


class IoPicture(BaseModel):
    counters: list[NamedValue]
    files: FileIo
    limitation: str | None


class WaitActivity(BaseModel):
    locking: LockPicture
    blocking: BlockingPicture
    parsing: ParsePicture
    buffer_busy: BufferBusyPicture
    io: IoPicture
    collected_at: datetime


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


def _locks(rows: list[dict[str, str]]) -> list[LockStat]:
    locks = []
    for row in rows:
        name = (row.get("eq_name") or "").strip()
        if not name:
            continue
        locks.append(LockStat(
            name=name,
            gets=_number(row.get("gets")),
            waits=_number(row.get("waits")),
            wait_time_ms=_number(row.get("wait_time")),
        ))
    return locks


def _mode(value: str | None) -> str:
    number = _number(value)
    if number is None:
        return "Not collected."
    return _MODES.get(number, str(number))


def _blocking(rows: list[dict[str, str]]) -> list[BlockingLock]:
    locks = []
    for row in rows:
        if _number(row.get("sid")) is None and not (row.get("lock_type") or "").strip():
            continue
        locks.append(BlockingLock(
            sid=_number(row.get("sid")),
            serial_no=_number(row.get("serial_no")),
            username=(row.get("username") or "").strip(),
            pid=(row.get("pid") or "").strip(),
            lock_type=(row.get("lock_type") or "").strip(),
            mode_wanted=_mode(row.get("mode_wanted")),
            object_name=(row.get("object_name") or "").strip(),
        ))
    return locks


def _named(rows: list[dict[str, str]], key: str) -> list[NamedValue]:
    values = []
    for row in rows:
        name = (row.get(key) or "").strip()
        if not name:
            continue
        values.append(NamedValue(name=name, value=_number(row.get("value"))))
    return values


def _library(rows: list[dict[str, str]]) -> list[LibraryCache]:
    items = []
    for row in rows:
        namespace = (row.get("namespace") or "").strip()
        if not namespace:
            continue
        items.append(LibraryCache(
            namespace=namespace,
            gets=_number(row.get("gets")),
            gethits=_number(row.get("gethits")),
            reloads=_number(row.get("reloads")),
            invalidations=_number(row.get("invalidations")),
        ))
    return items


def _events(rows: list[dict[str, str]]) -> list[WaitEvent]:
    events = []
    for row in rows:
        name = (row.get("event") or "").strip()
        if not name:
            continue
        events.append(WaitEvent(
            event=name,
            wait_class=(row.get("wait_class") or "").strip() or "Other",
            total_waits=_number(row.get("total_waits")),
            time_waited_micro=_number(row.get("time_waited_micro")),
        ))
    return events


def _classes(rows: list[dict[str, str]]) -> list[BufferClass]:
    classes = []
    for row in rows:
        name = (row.get("class_name") or "").strip()
        if not name:
            continue
        classes.append(BufferClass(
            name=name,
            count=_number(row.get("wait_count")),
            time_cs=_number(row.get("wait_time")),
        ))
    return classes


def _users(rows: list[dict[str, str]]) -> list[BufferUser]:
    users = []
    for row in rows:
        waited = _number(row.get("time_waited_micro"))
        users.append(BufferUser(
            username=(row.get("username") or "").strip(),
            sid=_number(row.get("sid")),
            total_waits=_number(row.get("total_waits")),
            waited_ms=None if waited is None else waited // 1000,
        ))
    return users


def _tablespaces(rows: list[dict[str, str]]) -> list[BufferTablespace]:
    items = []
    for row in rows:
        name = (row.get("tablespace_name") or "").strip()
        if not name:
            continue
        items.append(BufferTablespace(name=name, sessions=_number(row.get("session_count"))))
    return items


def _file_io(rows: list[dict[str, str]]) -> tuple[int | None, int | None, int | None, int | None]:
    if not rows:
        return None, None, None, None
    row = rows[0]
    return (
        _number(row.get("reads")),
        _number(row.get("writes")),
        _number(row.get("read_time")),
        _number(row.get("write_time")),
    )


def assemble(
    groups: dict[str, list[dict[str, str]]],
    notes: dict[str, str | None],
    collected_at: datetime | None = None,
) -> WaitActivity:
    parse_notes = [notes[kind] for kind in ("get_parse_counts", "get_parse_time", "get_library_cache") if notes.get(kind)]
    buffer_notes = [
        notes[kind]
        for kind in ("get_buffer_busy", "get_buffer_classes", "get_buffer_users", "get_buffer_tablespaces")
        if notes.get(kind)
    ]
    reads, writes, read_time, write_time = (None, None, None, None) if notes.get("get_file_io") else _file_io(groups.get("get_file_io", []))
    return WaitActivity(
        locking=LockPicture(
            locks=[] if notes.get("get_enqueue_stats") else _locks(groups.get("get_enqueue_stats", [])),
            limitation=notes.get("get_enqueue_stats"),
            caption=_LOCK_CAPTION,
        ),
        blocking=BlockingPicture(
            locks=[] if notes.get("get_blocking_locks") else _blocking(groups.get("get_blocking_locks", [])),
            limitation=notes.get("get_blocking_locks"),
            empty=_BLOCK_EMPTY,
            caption=_BLOCK_CAPTION,
        ),
        parsing=ParsePicture(
            counters=[] if notes.get("get_parse_counts") else _named(groups.get("get_parse_counts", []), "name"),
            times=[] if notes.get("get_parse_time") else _named(groups.get("get_parse_time", []), "stat_name"),
            library=[] if notes.get("get_library_cache") else _library(groups.get("get_library_cache", [])),
            limitations=parse_notes,
            caption=_PARSE_CAPTION,
        ),
        buffer_busy=BufferBusyPicture(
            events=[] if notes.get("get_buffer_busy") else _events(groups.get("get_buffer_busy", [])),
            classes=[] if notes.get("get_buffer_classes") else _classes(groups.get("get_buffer_classes", [])),
            users=[] if notes.get("get_buffer_users") else _users(groups.get("get_buffer_users", [])),
            tablespaces=[] if notes.get("get_buffer_tablespaces") else _tablespaces(groups.get("get_buffer_tablespaces", [])),
            limitations=buffer_notes,
            caption=_BUFFER_CAPTION,
        ),
        io=IoPicture(
            counters=[] if notes.get("get_io_counters") else _named(groups.get("get_io_counters", []), "name"),
            files=FileIo(
                reads=reads,
                writes=writes,
                read_time_cs=read_time,
                write_time_cs=write_time,
                limitation=notes.get("get_file_io"),
            ),
            limitation=notes.get("get_io_counters"),
        ),
        collected_at=collected_at or utcnow(),
    )


_SYNTHETIC: dict[str, tuple[list[dict[str, str]], str]] = {
    "get_enqueue_stats": ([
        {"eq_name": "Process Startup", "gets": "1950", "waits": "3", "wait_time": "73"},
        {"eq_name": "File Object", "gets": "25", "waits": "0", "wait_time": "0"},
    ], ""),
    "get_blocking_locks": ([{
        "sid": "12", "serial_no": "3", "username": "APPUSER", "pid": "1234",
        "lock_type": "TX", "mode_wanted": "6", "object_name": "",
    }], ""),
    "get_parse_counts": ([
        {"name": "parse count (total)", "value": "1000"},
        {"name": "parse count (hard)", "value": "100"},
        {"name": "parse count (failures)", "value": "2"},
        {"name": "session cursor cache hits", "value": "800"},
    ], ""),
    "get_parse_time": ([
        {"stat_name": "parse time elapsed", "value": "5000000"},
        {"stat_name": "hard parse elapsed time", "value": "1000000"},
        {"stat_name": "failed parse elapsed time", "value": "0"},
    ], ""),
    "get_library_cache": ([{
        "namespace": "SQL AREA", "gets": "1000", "gethits": "900", "reloads": "1", "invalidations": "0",
    }], ""),
    "get_buffer_busy": ([{
        "event": "buffer busy waits", "wait_class": "Concurrency",
        "total_waits": "40", "time_waited_micro": "800000",
    }], ""),
    "get_buffer_classes": ([
        {"class_name": "data block", "wait_count": "100", "wait_time": "50"},
        {"class_name": "undo header", "wait_count": "5", "wait_time": "2"},
    ], ""),
    "get_buffer_users": ([{
        "username": "APPUSER", "sid": "12", "total_waits": "10", "time_waited_micro": "0",
    }], ""),
    "get_buffer_tablespaces": ([
        {"tablespace_name": "SYSTEM", "session_count": "1"},
        {"tablespace_name": "USERS", "session_count": "2"},
    ], ""),
    "get_io_counters": ([
        {"name": "db block changes", "value": "1000"},
        {"name": "physical reads", "value": "400"},
        {"name": "physical reads direct", "value": "20"},
        {"name": "consistent gets", "value": "8000"},
        {"name": "physical writes", "value": "80"},
        {"name": "physical writes direct", "value": "10"},
    ], ""),
    "get_file_io": ([{"reads": "100", "writes": "20", "read_time": "40", "write_time": "10"}], ""),
}


async def _synthetic_reader(_settings: Settings, _alias: str, kind: str) -> tuple[list[dict[str, str]], str]:
    rows, note = _SYNTHETIC[kind]
    return [dict(row) for row in rows], note


async def load_wait_activity(settings: Settings, alias: str, reader: Reader | None = None) -> WaitActivity:
    """Read the WaitDB sections. One failed reading does not clear the others."""
    source = reader or (_synthetic_reader if settings.synthetic_mode else read_rows)
    groups: dict[str, list[dict[str, str]]] = {}
    notes: dict[str, str | None] = {}
    for kind in _KINDS:
        rows, note = await _read(settings, alias, kind, source)
        groups[kind] = rows
        notes[kind] = note
    return assemble(groups, notes)
