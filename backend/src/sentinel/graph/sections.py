"""MemoryDB, StorageDB, ParameterDB, and AlertDB: four views of one database beside GraphDB and WaitDB.

Each loader runs fixed readings from the catalog on the reader it is given and never builds a statement.
A reading that is not granted or fails names its limitation and leaves the rest of the view.
File paths and network addresses are shown to an administrator only.
"""

import re
from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel

from sentinel.adapters.oracle.activity import ActivityAccessError, ActivityUnavailable, read_rows
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.graph.memory import format_memory

Reader = Callable[[Settings, str, str], Awaitable[tuple[list[dict[str, str]], str]]]

_NO_POOL = "-"
_FREE = "free memory"
# How the unnamed entries of V$SGASTAT are grouped for the picture.
_UNPOOLED = {"buffer_cache": "buffer cache", "shared_io_pool": "buffer cache"}
_POOL_ORDER = ("buffer cache", "shared pool", "large pool", "java pool", "streams pool", "in-memory", "other")
_PATH = re.compile(r"(?:[A-Za-z]:\\|\\\\|/|\+[A-Za-z0-9_]+/)[^\s,;'\")\]]+")
_ADDRESS = re.compile(r"(?i)\((?:ADDRESS|DESCRIPTION|HOST)\s*=")
_ORA = re.compile(r"\b(?:ORA|TNS|RMAN|PLS)-\d{4,5}\b")
_ADMIN_VALUE = "Shown to an administrator."
_PATH_MASK = "(path shown to an administrator)"
PARAMETER_NOTE = "Values that hold a file path or a network address are shown to an administrator."
ALERT_NOTE = "File paths in the alert log are shown to an administrator."
ALERT_WINDOW = "The 300 most recent lines of the last 7 days."


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


def _by_source(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row.get("source", ""), []).append(row)
    return grouped


# ------------------------------------------------------------------ MemoryDB


class SgaComponent(BaseModel):
    pool: str
    name: str
    bytes: int
    display: str


class SgaPool(BaseModel):
    name: str
    bytes: int
    display: str
    used_bytes: int
    free_bytes: int
    used_percent: float | None


class SgaBreakdown(BaseModel):
    pools: list[SgaPool]
    components: list[SgaComponent]
    shared_pool: list[SgaComponent]
    total_bytes: int
    total_display: str
    collected_at: datetime
    limitation: str | None = None
    caption: str = "Current allocation by pool. The table lists the 60 largest components."


def _pool_name(pool: str, name: str) -> str:
    cleaned = pool.strip()
    if cleaned and cleaned != _NO_POOL:
        return "in-memory" if "in-memory" in cleaned.lower() or "in memory" in cleaned.lower() else cleaned
    return _UNPOOLED.get(name.strip().lower(), "other")


def assemble_sga(rows: list[dict[str, str]], limitation: str | None, collected_at: datetime | None = None) -> SgaBreakdown:
    grouped = _by_source(rows)
    totals: dict[str, list[int]] = {}
    for row in grouped.get("v$sgastat_pools", []):
        amount = _number(row.get("bytes"))
        if amount is None or amount <= 0:
            continue
        name = _pool_name(row.get("pool") or "", row.get("name") or "")
        free = _number(row.get("free_bytes")) or 0
        bucket = totals.setdefault(name, [0, 0])
        bucket[0] += amount
        bucket[1] += min(free, amount)
    order = {name: index for index, name in enumerate(_POOL_ORDER)}
    pools = [
        SgaPool(
            name=name, bytes=total, display=format_memory(total), used_bytes=total - free, free_bytes=free,
            used_percent=round((total - free) * 100 / total, 1) if total > 0 else None,
        )
        for name, (total, free) in sorted(totals.items(), key=lambda item: (order.get(item[0], len(order)), item[0]))
    ]

    def components(source: str, pool: str | None = None) -> list[SgaComponent]:
        items: list[SgaComponent] = []
        for row in grouped.get(source, []):
            amount = _number(row.get("bytes"))
            name = (row.get("name") or "").strip()
            if amount is None or amount <= 0 or not name:
                continue
            items.append(SgaComponent(
                pool=pool or _pool_name(row.get("pool") or "", name), name=name, bytes=amount, display=format_memory(amount),
            ))
        return sorted(items, key=lambda item: (-item.bytes, item.name))

    total = sum(pool.bytes for pool in pools)
    return SgaBreakdown(
        pools=[] if limitation else pools,
        components=[] if limitation else components("v$sgastat"),
        shared_pool=[] if limitation else components("v$sgastat_shared", "shared pool"),
        total_bytes=0 if limitation else total,
        total_display="" if limitation or total <= 0 else format_memory(total),
        collected_at=collected_at or utcnow(),
        limitation=limitation,
    )


_SYNTHETIC_SGA: list[dict[str, str]] = [
    {"source": "v$sgastat_pools", "pool": "-", "name": "buffer_cache", "bytes": "956301312", "free_bytes": "0"},
    {"source": "v$sgastat_pools", "pool": "-", "name": "shared_io_pool", "bytes": "83886080", "free_bytes": "0"},
    {"source": "v$sgastat_pools", "pool": "-", "name": "fixed_sga", "bytes": "5032960", "free_bytes": "0"},
    {"source": "v$sgastat_pools", "pool": "-", "name": "log_buffer", "bytes": "2265088", "free_bytes": "0"},
    {"source": "v$sgastat_pools", "pool": "shared pool", "name": "", "bytes": "157286400", "free_bytes": "49183744"},
    {"source": "v$sgastat_pools", "pool": "large pool", "name": "", "bytes": "16777216", "free_bytes": "16285696"},
    {"source": "v$sgastat", "pool": "-", "name": "buffer_cache", "bytes": "956301312"},
    {"source": "v$sgastat", "pool": "-", "name": "shared_io_pool", "bytes": "83886080"},
    {"source": "v$sgastat", "pool": "shared pool", "name": "free memory", "bytes": "49183744"},
    {"source": "v$sgastat", "pool": "shared pool", "name": "SQLA", "bytes": "41052160"},
    {"source": "v$sgastat", "pool": "shared pool", "name": "KGLH0", "bytes": "24792064"},
    {"source": "v$sgastat", "pool": "large pool", "name": "free memory", "bytes": "16285696"},
    {"source": "v$sgastat", "pool": "-", "name": "fixed_sga", "bytes": "5032960"},
    {"source": "v$sgastat", "pool": "-", "name": "log_buffer", "bytes": "2265088"},
    {"source": "v$sgastat_shared", "name": "free memory", "bytes": "49183744"},
    {"source": "v$sgastat_shared", "name": "SQLA", "bytes": "41052160"},
    {"source": "v$sgastat_shared", "name": "KGLH0", "bytes": "24792064"},
    {"source": "v$sgastat_shared", "name": "KGLS", "bytes": "8517632"},
]


async def load_sga_breakdown(settings: Settings, alias: str, reader: Reader | None = None) -> SgaBreakdown:
    """Read how the SGA is divided between its pools and their largest components."""
    if reader is None and settings.synthetic_mode:
        return assemble_sga([dict(row) for row in _SYNTHETIC_SGA], None)
    rows, note = await _read(settings, alias, "get_sga_breakdown", reader or read_rows)
    return assemble_sga(rows, note)


# ------------------------------------------------------------------ StorageDB


class TablespaceRow(BaseModel):
    name: str
    contents: str
    status: str
    total_bytes: int | None
    free_bytes: int | None
    used_bytes: int | None
    used_percent: float | None
    fragments: int | None


class TablespaceUtilization(BaseModel):
    tablespaces: list[TablespaceRow]
    total_bytes: int
    used_bytes: int
    total_display: str
    used_display: str
    collected_at: datetime
    limitation: str | None = None
    caption: str = "Allocated size and free space of every tablespace. Fragments are the free extents of a tablespace."


def assemble_tablespaces(
    rows: list[dict[str, str]], limitation: str | None, collected_at: datetime | None = None,
) -> TablespaceUtilization:
    items: list[TablespaceRow] = []
    for row in rows:
        name = (row.get("tablespace_name") or "").strip()
        if not name:
            continue
        total = _number(row.get("bytes_total"))
        free = _number(row.get("bytes_free"))
        if total is not None and free is not None:
            free = max(0, min(free, total))
        used = total - free if total is not None and free is not None else None
        contents = (row.get("contents") or "").strip().upper()
        items.append(TablespaceRow(
            name=name, contents=contents, status=(row.get("status") or "").strip().upper(),
            total_bytes=total, free_bytes=free, used_bytes=used,
            used_percent=round(used * 100 / total, 1) if used is not None and total else None,
            # A temporary tablespace has no free-extent list, so it has no fragment count.
            fragments=None if contents == "TEMPORARY" else _number(row.get("fragments")),
        ))
    items.sort(key=lambda item: item.name)
    total_bytes = sum(item.total_bytes or 0 for item in items)
    used_bytes = sum(item.used_bytes or 0 for item in items)
    return TablespaceUtilization(
        tablespaces=[] if limitation else items,
        total_bytes=0 if limitation else total_bytes,
        used_bytes=0 if limitation else used_bytes,
        total_display="" if limitation or total_bytes <= 0 else format_memory(total_bytes),
        used_display="" if limitation or total_bytes <= 0 else format_memory(used_bytes),
        collected_at=collected_at or utcnow(),
        limitation=limitation,
    )


_SYNTHETIC_TABLESPACES: list[dict[str, str]] = [
    {"tablespace_name": "SYSAUX", "contents": "PERMANENT", "status": "ONLINE", "bytes_total": "643825664", "bytes_free": "39321600", "fragments": "1"},
    {"tablespace_name": "SYSTEM", "contents": "PERMANENT", "status": "ONLINE", "bytes_total": "334495744", "bytes_free": "9175040", "fragments": "1"},
    {"tablespace_name": "TEMP", "contents": "TEMPORARY", "status": "ONLINE", "bytes_total": "19922944", "bytes_free": "19922944", "fragments": "0"},
    {"tablespace_name": "UNDOTBS1", "contents": "UNDO", "status": "ONLINE", "bytes_total": "103809024", "bytes_free": "92211610", "fragments": "52"},
    {"tablespace_name": "USERS", "contents": "PERMANENT", "status": "ONLINE", "bytes_total": "137331343", "bytes_free": "58982400", "fragments": "2"},
]


async def load_tablespaces(settings: Settings, alias: str, reader: Reader | None = None) -> TablespaceUtilization:
    """Read the size, free space, and fragmentation of every tablespace."""
    if reader is None and settings.synthetic_mode:
        return assemble_tablespaces([dict(row) for row in _SYNTHETIC_TABLESPACES], None)
    rows, note = await _read(settings, alias, "get_tablespace_utilization", reader or read_rows)
    return assemble_tablespaces(rows, note)


# ------------------------------------------------------------------ ParameterDB


class Parameter(BaseModel):
    name: str
    value: str
    is_default: bool
    sys_modifiable: str
    description: str
    restricted: bool = False


class ParameterList(BaseModel):
    version: str
    parameters: list[Parameter]
    total: int
    changed: int
    collected_at: datetime
    limitation: str | None = None
    detail: bool = True
    detail_note: str | None = None
    caption: str = "Initialization parameters in effect for this instance. This page does not change a parameter."


def holds_location(value: str) -> bool:
    """True when a parameter value is a file path or a network address."""
    return bool(value) and bool(_PATH.search(value) or _ADDRESS.search(value))


def assemble_parameters(
    rows: list[dict[str, str]], limitation: str | None, collected_at: datetime | None = None,
) -> ParameterList:
    grouped = _by_source(rows)
    version = next(((row.get("banner") or "").strip() for row in grouped.get("v$version", []) if row.get("banner")), "")
    items: list[Parameter] = []
    for row in grouped.get("v$parameter", []):
        name = (row.get("name") or "").strip()
        if not name:
            continue
        items.append(Parameter(
            name=name,
            value=(row.get("value") or "").strip(),
            is_default=(row.get("isdefault") or "").strip().upper() == "TRUE",
            sys_modifiable=(row.get("issys_modifiable") or "").strip().upper() or "FALSE",
            description=(row.get("description") or "").strip(),
        ))
    items.sort(key=lambda item: item.name)
    return ParameterList(
        version=version,
        parameters=[] if limitation else items,
        total=0 if limitation else len(items),
        changed=0 if limitation else sum(1 for item in items if not item.is_default),
        collected_at=collected_at or utcnow(),
        limitation=limitation,
    )


def parameters_for_role(picture: ParameterList, admin: bool) -> ParameterList:
    """A non-administrator sees every parameter name, without values that hold a path or an address."""
    if admin:
        return picture
    trimmed = [
        item.model_copy(update={"value": _ADMIN_VALUE, "restricted": True}) if holds_location(item.value) else item
        for item in picture.parameters
    ]
    return picture.model_copy(update={"parameters": trimmed, "detail": False, "detail_note": PARAMETER_NOTE})


_SYNTHETIC_PARAMETERS: list[dict[str, str]] = [
    {"source": "v$version", "banner": "Oracle Database 19c Enterprise Edition Release 19.0.0.0.0 - Production"},
    {"source": "v$parameter", "name": "archive_lag_target", "value": "0", "isdefault": "TRUE", "issys_modifiable": "IMMEDIATE",
     "description": "Maximum number of seconds of redos the standby could lose"},
    {"source": "v$parameter", "name": "audit_file_dest", "value": "/u01/app/oracle/admin/ORCL/adump", "isdefault": "FALSE",
     "issys_modifiable": "DEFERRED", "description": "Directory in which auditing files are to reside"},
    {"source": "v$parameter", "name": "local_listener", "value": "(ADDRESS=(PROTOCOL=TCP)(HOST=dbhost01)(PORT=1521))", "isdefault": "FALSE",
     "issys_modifiable": "IMMEDIATE", "description": "local listener"},
    {"source": "v$parameter", "name": "open_cursors", "value": "300", "isdefault": "FALSE", "issys_modifiable": "IMMEDIATE",
     "description": "max # cursors per session"},
    {"source": "v$parameter", "name": "processes", "value": "300", "isdefault": "FALSE", "issys_modifiable": "FALSE",
     "description": "user processes"},
    {"source": "v$parameter", "name": "sga_target", "value": "8G", "isdefault": "FALSE", "issys_modifiable": "IMMEDIATE",
     "description": "Target size of SGA"},
    {"source": "v$parameter", "name": "undo_retention", "value": "900", "isdefault": "TRUE", "issys_modifiable": "IMMEDIATE",
     "description": "undo retention in seconds"},
]


async def load_parameters(settings: Settings, alias: str, reader: Reader | None = None) -> ParameterList:
    """Read the initialization parameters in effect and the version banner."""
    if reader is None and settings.synthetic_mode:
        return assemble_parameters([dict(row) for row in _SYNTHETIC_PARAMETERS], None)
    rows, note = await _read(settings, alias, "get_parameters", reader or read_rows)
    return assemble_parameters(rows, note)


# ------------------------------------------------------------------ AlertDB


class AlertLine(BaseModel):
    at: str
    text: str
    severity: str
    codes: list[str]


class AlertTail(BaseModel):
    lines: list[AlertLine]
    errors: int
    warnings: int
    collected_at: datetime
    limitation: str | None = None
    detail: bool = True
    detail_note: str | None = None
    window: str = ALERT_WINDOW
    caption: str = "Database alert log, oldest line first. Listener and client traces are left out."


def _severity(message_type: str | None, text: str) -> str:
    # MESSAGE_TYPE: 1 unknown (most lines), 2 incident error, 3 error, 4 warning, 5 notification, 6 trace.
    kind = _number(message_type)
    if _ORA.search(text) or kind in {2, 3}:
        return "error"
    if kind == 4 or re.search(r"(?i)\b(?:warning|cannot|failed|failure|corrupt|terminat)", text):
        return "warning"
    return "info"


def assemble_alert_tail(rows: list[dict[str, str]], limitation: str | None, collected_at: datetime | None = None) -> AlertTail:
    lines: list[AlertLine] = []
    for row in rows:
        text = " ".join((row.get("message_text") or "").split())
        if not text:
            continue
        lines.append(AlertLine(
            at=(row.get("occurred_at") or "").strip(), text=text,
            severity=_severity(row.get("message_type"), text),
            codes=list(dict.fromkeys(_ORA.findall(text)))[:4],
        ))
    # The statement returns the newest line first. The page reads like the file: oldest first.
    lines.reverse()
    return AlertTail(
        lines=[] if limitation else lines,
        errors=0 if limitation else sum(1 for line in lines if line.severity == "error"),
        warnings=0 if limitation else sum(1 for line in lines if line.severity == "warning"),
        collected_at=collected_at or utcnow(),
        limitation=limitation,
    )


def alert_tail_for_role(picture: AlertTail, admin: bool) -> AlertTail:
    """A non-administrator reads the same lines with file paths left out."""
    if admin:
        return picture
    masked = [line.model_copy(update={"text": _PATH.sub(_PATH_MASK, line.text)}) for line in picture.lines]
    return picture.model_copy(update={"lines": masked, "detail": False, "detail_note": ALERT_NOTE})


_SYNTHETIC_ALERT: list[dict[str, str]] = [
    {"occurred_at": "2026-11-15 06:11:24", "message_type": "5",
     "message_text": "Resize operation completed for file# 3, fname /u01/oradata/ORCL/sysaux01.dbf, old size 870400K, new size 880640K"},
    {"occurred_at": "2026-11-15 06:05:21", "message_type": "5",
     "message_text": "ARC1 (PID:187): Archived Log entry 18 added for B-1237845575.T-1.S-18"},
    {"occurred_at": "2026-11-15 06:04:52", "message_type": "5",
     "message_text": "Thread 1 advanced to log sequence 19 (LGWR switch),  current SCN: 5645932"},
    {"occurred_at": "2026-11-15 05:40:10", "message_type": "4",
     "message_text": "WARNING: Heavy swapping observed on system in last 5 mins."},
    {"occurred_at": "2026-11-15 01:10:00", "message_type": "2",
     "message_text": "ORA-01578: ORACLE data block corrupted (file # 7, block # 1234)"},
]


async def load_alert_tail(settings: Settings, alias: str, reader: Reader | None = None) -> AlertTail:
    """Read the most recent lines of the database alert log."""
    if reader is None and settings.synthetic_mode:
        return assemble_alert_tail([dict(row) for row in _SYNTHETIC_ALERT], None)
    rows, note = await _read(settings, alias, "get_alert_tail", reader or read_rows)
    return assemble_alert_tail(rows, note)
