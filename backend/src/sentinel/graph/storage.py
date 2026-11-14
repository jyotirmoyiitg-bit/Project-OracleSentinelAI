"""Disk-storage snapshot for one database. Each reading fails on its own."""

from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel

from sentinel.adapters.oracle.activity import ActivityAccessError, ActivityUnavailable, read_rows
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.graph.memory import format_memory

Reader = Callable[[Settings, str, str], Awaitable[tuple[list[dict[str, str]], str]]]

_FLASH_CACHE = "Not collected."
_NO_DATA_FILE = "No data file was returned."
_NO_CONTROL_FILE = "No control file was returned."
_NO_REDO = "No redo log was returned."
_NO_SEGMENT = "No segment was returned."
_NO_RECOVERY = "No recovery area was returned."
_NO_LOG_MODE = "The log mode was not returned."
_NO_BACKUP = "No RMAN job was returned."
_NOT_COLLECTED = "Not collected."
_SEGMENT_UNAVAILABLE = "The segment inventory is not available."
_NO_PERCENT = "The used percent was not returned."
_NO_STATUS = "The status was not returned."
_NO_PATH = "The member path was not returned."
_DATA_CAP = "The file list shows the first 80 data files."
_TEMP_CAP = "The file list shows the first 40 temp files."
_ONLINE = frozenset({"online", "system"})
_FILL = frozenset({"current", "active"})


class StorageFile(BaseModel):
    name: str
    tablespace: str
    bytes: int | None
    display: str
    status: str
    online: bool


class DataFiles(BaseModel):
    online_files: int
    online_tablespaces: int
    used_bytes: int | None
    used_display: str
    used_percent: float | None
    files: list[StorageFile]
    flash_cache: str = _FLASH_CACHE
    limitation: str | None = None


class TempFiles(BaseModel):
    files: list[StorageFile]
    limitation: str | None = None


class ControlFile(BaseModel):
    name: str
    status: str


class ControlFiles(BaseModel):
    files: list[ControlFile]
    count: int = 0
    limitation: str | None = None


class RedoMember(BaseModel):
    group: int
    thread: int
    path: str
    bytes: int | None
    display: str
    status: str
    current: bool


class RedoLogs(BaseModel):
    groups: int
    total_bytes: int | None
    total_display: str
    active_percent: float | None
    members: list[RedoMember]
    limitation: str | None = None


class SegmentRow(BaseModel):
    owner: str
    name: str
    segment_type: str
    tablespace: str
    bytes: int | None
    display: str


class Segments(BaseModel):
    rows: list[SegmentRow]
    limitation: str | None = None


class RecoveryUsage(BaseModel):
    file_type: str
    percent_space_used: float | None
    number_of_files: int | None


class RecoveryArea(BaseModel):
    present: bool
    name: str
    used_percent: float | None
    used_display: str
    limit_display: str
    usage: list[RecoveryUsage]
    limitation: str | None = None


class ArchiveDestination(BaseModel):
    destination: str
    status: str


class ArchivePicture(BaseModel):
    log_mode: str
    destinations: list[ArchiveDestination]
    latest: str
    free_minutes: str = "Not collected."
    time_to_failure: str = "Not collected."
    limitation: str | None = None


class BackupPicture(BaseModel):
    input_type: str
    status: str
    started: str
    ended: str
    failed: bool
    limitation: str | None = None


class StoragePicture(BaseModel):
    data_files: DataFiles
    temp_files: TempFiles
    control_files: ControlFiles
    redo_logs: RedoLogs
    segments: Segments
    recovery_area: RecoveryArea
    archive: ArchivePicture
    backup: BackupPicture
    collected_at: datetime
    limitations: list[str]
    detail: bool = True
    detail_note: str | None = None


_SUMMARY_NOTE = "File, control-file, redo-member, and segment listings are shown to an administrator."


def summarize(picture: StoragePicture) -> StoragePicture:
    """Keep the cylinders and counts. Drop every path and object name for a non-administrator."""
    return picture.model_copy(update={
        "data_files": picture.data_files.model_copy(update={"files": []}),
        "temp_files": picture.temp_files.model_copy(update={"files": []}),
        "control_files": picture.control_files.model_copy(update={"files": []}),
        "redo_logs": picture.redo_logs.model_copy(update={"members": []}),
        "segments": Segments(rows=[], limitation=None),
        "detail": False,
        "detail_note": _SUMMARY_NOTE,
    })


def _number(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


def _decimal(value: str | None) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        return float(str(value).strip())
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
        grouped.setdefault(row.get("source") or "", []).append(row)
    return grouped


def _join(notes: list[str | None]) -> str | None:
    text = " ".join(note for note in notes if note)
    return text or None


def _file(name: str, tablespace: str, amount: int | None, status: str, online: bool) -> StorageFile:
    return StorageFile(
        name=name or "The file name was not returned.",
        tablespace=tablespace,
        bytes=amount,
        display=format_memory(amount) if amount is not None else "",
        status=status or _NO_STATUS,
        online=online,
    )


def _data(rows: list[dict[str, str]], limitation: str | None) -> tuple[DataFiles, int]:
    grouped = _by_source(rows)
    summary = grouped.get("datafile_summary", [{}])[0]
    file_count = _number(summary.get("file_count")) or 0
    online_files = _number(summary.get("online_files")) or 0
    online_tablespaces = _number(summary.get("online_tablespaces")) or 0
    total = _number(summary.get("bytes_total"))
    free_rows = grouped.get("dba_free_space", [])
    free = _number(free_rows[0].get("bytes_free")) if free_rows else None
    used: int | None = None
    percent: float | None = None
    notes = [limitation]
    if total is not None and free is not None and total >= free:
        used = total - free
        percent = round(used * 100 / total, 1) if total else None
    elif file_count and limitation is None:
        notes.append(_NO_PERCENT)
    files = [
        _file(
            row.get("file_name") or "",
            row.get("tablespace_name") or "",
            _number(row.get("bytes")),
            row.get("online_status") or "",
            (row.get("online_status") or "").casefold() in _ONLINE,
        )
        for row in grouped.get("dba_data_files", [])
    ]
    if file_count == 0 and limitation is None:
        notes.append(_NO_DATA_FILE)
    if file_count > 80:
        notes.append(_DATA_CAP)
    picture = DataFiles(
        online_files=online_files,
        online_tablespaces=online_tablespaces,
        used_bytes=used,
        used_display=format_memory(used) if used is not None else "",
        used_percent=percent,
        files=files,
        limitation=_join(notes),
    )
    return picture, file_count


def _temp(rows: list[dict[str, str]], limitation: str | None) -> tuple[TempFiles, int, int]:
    grouped = _by_source(rows)
    summary = grouped.get("tempfile_summary", [{}])[0]
    file_count = _number(summary.get("file_count")) or 0
    online_files = _number(summary.get("online_files")) or 0
    online_tablespaces = _number(summary.get("online_tablespaces")) or 0
    files = [
        _file(
            row.get("file_name") or "",
            row.get("tablespace_name") or "",
            _number(row.get("bytes")),
            row.get("status") or "",
            (row.get("status") or "").casefold() == "online",
        )
        for row in grouped.get("dba_temp_files", [])
    ]
    notes = [limitation]
    if file_count > 40:
        notes.append(_TEMP_CAP)
    return TempFiles(files=files, limitation=_join(notes)), online_files, online_tablespaces


def _control(rows: list[dict[str, str]], limitation: str | None) -> ControlFiles:
    files = [
        ControlFile(
            name=(row.get("name") or "").strip() or "The file name was not returned.",
            status=(row.get("status") or "").strip() or _NO_STATUS,
        )
        for row in rows
    ]
    notes = [limitation]
    if not files and limitation is None:
        notes.append(_NO_CONTROL_FILE)
    return ControlFiles(files=files, count=len(files), limitation=_join(notes))


def _redo(rows: list[dict[str, str]], limitation: str | None) -> RedoLogs:
    grouped = _by_source(rows)
    groups = []
    for row in grouped.get("v$log", []):
        group = _number(row.get("group_no"))
        thread = _number(row.get("thread_no"))
        if group is None or thread is None:
            continue
        groups.append((group, thread, _number(row.get("bytes")), (row.get("status") or "").strip()))
    members: dict[int, list[str]] = {}
    for row in grouped.get("v$logfile", []):
        group = _number(row.get("group_no"))
        if group is None:
            continue
        members.setdefault(group, []).append((row.get("member") or "").strip())
    cards: list[RedoMember] = []
    for group, thread, amount, status in groups:
        paths = members.get(group) or [_NO_PATH]
        current = status.casefold() == "current"
        for path in paths:
            cards.append(RedoMember(
                group=group,
                thread=thread,
                path=path or _NO_PATH,
                bytes=amount,
                display=format_memory(amount) if amount is not None else "",
                status=status or _NO_STATUS,
                current=current,
            ))
    total = sum(amount for _, _, amount, _ in groups if amount is not None)
    counted = [amount for _, _, amount, _ in groups if amount is not None]
    active = sum(1 for _, _, _, status in groups if status.casefold() in _FILL)
    notes = [limitation]
    if not groups and limitation is None:
        notes.append(_NO_REDO)
    return RedoLogs(
        groups=len(groups),
        total_bytes=total if counted else None,
        total_display=format_memory(total) if counted else "",
        active_percent=round(active * 100 / len(groups), 1) if groups else None,
        members=cards,
        limitation=_join(notes),
    )


def _segments(rows: list[dict[str, str]], limitation: str | None) -> Segments:
    items = []
    for row in rows[:20]:
        amount = _number(row.get("bytes"))
        items.append(SegmentRow(
            owner=(row.get("owner") or "").strip(),
            name=(row.get("segment_name") or "").strip() or "The segment name was not returned.",
            segment_type=(row.get("segment_type") or "").strip(),
            tablespace=(row.get("tablespace_name") or "").strip(),
            bytes=amount,
            display=format_memory(amount) if amount is not None else "",
        ))
    note = f"{_SEGMENT_UNAVAILABLE} {limitation}" if limitation else None
    if not items and limitation is None:
        note = _NO_SEGMENT
    return Segments(rows=items, limitation=note)


def _recovery(rows: list[dict[str, str]], limitation: str | None) -> RecoveryArea:
    grouped = _by_source(rows)
    destination = next((row for row in grouped.get("v$recovery_file_dest", []) if row.get("name")), None)
    usage = [
        RecoveryUsage(
            file_type=(row.get("file_type") or "").strip(),
            percent_space_used=_decimal(row.get("percent_space_used")),
            number_of_files=_number(row.get("number_of_files")),
        )
        for row in grouped.get("v$flash_recovery_area_usage", [])
        if (row.get("file_type") or "").strip()
    ]
    if limitation and destination is None:
        return RecoveryArea(
            present=False, name="", used_percent=None, used_display="", limit_display="",
            usage=usage, limitation=limitation,
        )
    if destination is None:
        return RecoveryArea(
            present=False, name="", used_percent=None, used_display="", limit_display="",
            usage=usage, limitation=_NO_RECOVERY,
        )
    used = _number(destination.get("space_used"))
    limit = _number(destination.get("space_limit"))
    return RecoveryArea(
        present=True,
        name=(destination.get("name") or "").strip(),
        used_percent=_decimal(destination.get("used_pct")),
        used_display=format_memory(used) if used is not None else "",
        limit_display=format_memory(limit) if limit is not None else "",
        usage=usage,
        limitation=limitation,
    )


def _archive(rows: list[dict[str, str]], limitation: str | None) -> ArchivePicture:
    grouped = _by_source(rows)
    mode = next(((row.get("log_mode") or "").strip() for row in grouped.get("v$database", [])), "")
    destinations = [
        ArchiveDestination(
            destination=(row.get("destination") or "").strip() or "The destination was not returned.",
            status=(row.get("status") or "").strip() or _NO_STATUS,
        )
        for row in grouped.get("v$archive_dest_status", [])
    ]
    latest_row = next(iter(grouped.get("v$archived_log", [])), None)
    latest = ""
    if latest_row is not None:
        sequence = (latest_row.get("sequence_no") or "").strip()
        completed = (latest_row.get("completion_time") or "").strip()
        latest = " ".join(part for part in (f"sequence {sequence}" if sequence else "", completed) if part)
    notes = [limitation]
    if not mode and limitation is None:
        notes.append(_NO_LOG_MODE)
    return ArchivePicture(
        log_mode=mode,
        destinations=destinations,
        latest=latest,
        free_minutes=_NOT_COLLECTED,
        time_to_failure=_NOT_COLLECTED,
        limitation=_join(notes),
    )


def _backup(rows: list[dict[str, str]], limitation: str | None) -> BackupPicture:
    if limitation:
        return BackupPicture(input_type="", status="", started="", ended="", failed=False, limitation=limitation)
    row = next((item for item in rows if (item.get("status") or item.get("input_type"))), None)
    if row is None:
        return BackupPicture(input_type="", status="", started="", ended="", failed=False, limitation=_NO_BACKUP)
    status = (row.get("status") or "").strip()
    folded = status.casefold()
    failed = bool(status) and not folded.startswith("completed") and folded != "running"
    return BackupPicture(
        input_type=(row.get("input_type") or "").strip(),
        status=status or _NO_STATUS,
        started=(row.get("started") or "").strip(),
        ended=(row.get("ended") or "").strip(),
        failed=failed,
        limitation=None,
    )


def assemble(
    data_rows: list[dict[str, str]],
    temp_rows: list[dict[str, str]],
    control_rows: list[dict[str, str]],
    redo_rows: list[dict[str, str]],
    segment_rows: list[dict[str, str]],
    recovery_rows: list[dict[str, str]],
    archive_rows: list[dict[str, str]],
    backup_rows: list[dict[str, str]],
    notes: list[str | None],
    collected_at: datetime | None = None,
) -> StoragePicture:
    data_note, temp_note, control_note, redo_note, segment_note, recovery_note, archive_note, backup_note = notes
    data, _file_count = _data(data_rows, data_note)
    temp, temp_online, temp_tablespaces = _temp(temp_rows, temp_note)
    if temp.limitation is None:
        data = data.model_copy(update={
            "online_files": data.online_files + temp_online,
            "online_tablespaces": data.online_tablespaces + temp_tablespaces,
        })
    control = _control(control_rows, control_note)
    redo = _redo(redo_rows, redo_note)
    segments = _segments(segment_rows, segment_note)
    recovery = _recovery(recovery_rows, recovery_note)
    archive = _archive(archive_rows, archive_note)
    backup = _backup(backup_rows, backup_note)
    limitations = [
        item.limitation for item in (data, temp, control, redo, segments, recovery, archive, backup) if item.limitation
    ]
    return StoragePicture(
        data_files=data,
        temp_files=temp,
        control_files=control,
        redo_logs=redo,
        segments=segments,
        recovery_area=recovery,
        archive=archive,
        backup=backup,
        collected_at=collected_at or utcnow(),
        limitations=limitations,
    )


_SYNTHETIC: dict[str, tuple[list[dict[str, str]], str]] = {
    "get_data_files": ([
        {"source": "datafile_summary", "file_count": "2", "online_files": "1", "online_tablespaces": "1", "bytes_total": "10485760"},
        {"source": "dba_free_space", "bytes_free": "1048576"},
        {"source": "dba_data_files", "file_name": r"D:\ORACLE\SYSTEM01.DBF", "tablespace_name": "SYSTEM", "bytes": "9437184", "online_status": "SYSTEM"},
        {"source": "dba_data_files", "file_name": r"D:\ORACLE\USERS01.DBF", "tablespace_name": "USERS", "bytes": "1048576", "online_status": "OFFLINE"},
    ], ""),
    "get_temp_files": ([
        {"source": "tempfile_summary", "file_count": "1", "online_files": "1", "online_tablespaces": "1"},
        {"source": "dba_temp_files", "file_name": r"D:\ORACLE\TEMP01.DBF", "tablespace_name": "TEMP", "bytes": "2097152", "status": "ONLINE"},
    ], ""),
    "get_control_files": ([
        {"source": "v$controlfile", "name": r"D:\ORACLE\CONTROL01.CTL", "status": ""},
    ], ""),
    "get_redo_logs": ([
        {"source": "v$log", "group_no": "1", "thread_no": "1", "bytes": "52428800", "status": "CURRENT"},
        {"source": "v$log", "group_no": "2", "thread_no": "1", "bytes": "52428800", "status": "INACTIVE"},
        {"source": "v$logfile", "group_no": "1", "member": r"D:\ORACLE\REDO01.LOG"},
        {"source": "v$logfile", "group_no": "2", "member": r"D:\ORACLE\REDO02.LOG"},
    ], ""),
    "get_largest_segments": ([
        {"owner": "APP", "segment_name": "ORDERS", "segment_type": "TABLE", "tablespace_name": "USERS", "bytes": "1048576"},
    ], ""),
    "get_recovery_area": ([
        {"source": "v$recovery_file_dest", "name": "USE_DB_RECOVERY_FILE_DEST", "space_limit": "10737418240", "space_used": "2147483648", "used_pct": "20"},
        {"source": "v$flash_recovery_area_usage", "file_type": "ARCHIVELOG", "percent_space_used": "15", "number_of_files": "4"},
    ], ""),
    "get_archive": ([
        {"source": "v$database", "log_mode": "ARCHIVELOG"},
        {"source": "v$archive_dest_status", "destination": "USE_DB_RECOVERY_FILE_DEST", "status": "VALID"},
        {"source": "v$archived_log", "sequence_no": "42", "completion_time": "2026-11-13 08:00:00"},
    ], ""),
    "get_backup": ([
        {"input_type": "DB FULL", "status": "COMPLETED", "started": "2026-11-12 02:00:00", "ended": "2026-11-12 03:00:00"},
    ], ""),
}


async def _synthetic_reader(_settings: Settings, _alias: str, kind: str) -> tuple[list[dict[str, str]], str]:
    rows, note = _SYNTHETIC[kind]
    return [dict(row) for row in rows], note


async def load_storage(settings: Settings, alias: str, reader: Reader | None = None) -> StoragePicture:
    """Read storage, recovery, archive, and the latest backup. One failed reading does not clear the others."""
    source = reader or (_synthetic_reader if settings.synthetic_mode else read_rows)
    groups = []
    notes: list[str | None] = []
    for kind in (
        "get_data_files", "get_temp_files", "get_control_files", "get_redo_logs", "get_largest_segments",
        "get_recovery_area", "get_archive", "get_backup",
    ):
        rows, note = await _read(settings, alias, kind, source)
        groups.append(rows)
        notes.append(note)
    return assemble(groups[0], groups[1], groups[2], groups[3], groups[4], groups[5], groups[6], groups[7], notes)
