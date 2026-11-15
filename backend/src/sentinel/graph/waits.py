"""Non-idle wait events for one database. Rates are computed later, on the page."""

from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel

from sentinel.adapters.oracle.activity import ActivityAccessError, ActivityUnavailable, read_rows
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings

Reader = Callable[[Settings, str, str], Awaitable[tuple[list[dict[str, str]], str]]]

_CAPTION = "Totals are since instance startup. Idle waits are not included."


class WaitEvent(BaseModel):
    event: str
    wait_class: str
    total_waits: int | None
    time_waited_micro: int | None


class WaitPicture(BaseModel):
    events: list[WaitEvent]
    cpu_micro: int | None
    collected_at: datetime
    limitations: list[str]
    caption: str


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


def _cpu(rows: list[dict[str, str]]) -> int | None:
    for row in rows:
        if (row.get("stat_name") or "").strip() == "DB CPU":
            return _number(row.get("value"))
    return None


def assemble(
    event_rows: list[dict[str, str]],
    cpu_rows: list[dict[str, str]],
    notes: list[str | None],
    collected_at: datetime | None = None,
) -> WaitPicture:
    limitations = [note for note in notes if note]
    return WaitPicture(
        events=_events(event_rows),
        cpu_micro=None if notes[1] else _cpu(cpu_rows),
        collected_at=collected_at or utcnow(),
        limitations=limitations,
        caption=_CAPTION,
    )


_SYNTHETIC: dict[str, tuple[list[dict[str, str]], str]] = {
    "get_wait_events": ([
        {"event": "db file sequential read", "wait_class": "User I/O", "total_waits": "1000", "time_waited_micro": "2000000"},
        {"event": "log file sync", "wait_class": "Commit", "total_waits": "100", "time_waited_micro": "500000"},
    ], ""),
    "get_wait_cpu": ([{"stat_name": "DB CPU", "value": "8000000"}], ""),
}


async def _synthetic_reader(_settings: Settings, _alias: str, kind: str) -> tuple[list[dict[str, str]], str]:
    rows, note = _SYNTHETIC[kind]
    return [dict(row) for row in rows], note


async def load_waits(settings: Settings, alias: str, reader: Reader | None = None) -> WaitPicture:
    """Read non-idle waits and DB CPU. One failed reading does not clear the other."""
    source = reader or (_synthetic_reader if settings.synthetic_mode else read_rows)
    groups = []
    notes: list[str | None] = []
    for kind in ("get_wait_events", "get_wait_cpu"):
        rows, note = await _read(settings, alias, kind, source)
        groups.append(rows)
        notes.append(note)
    return assemble(groups[0], groups[1], notes)
