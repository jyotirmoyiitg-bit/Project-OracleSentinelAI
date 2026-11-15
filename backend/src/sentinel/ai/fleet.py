"""Fleet chat: which fleet tool answers a question, and one fixed reading from every database.

A fleet question never becomes SQL. Words from the question, such as a program name or a size,
are used only to filter rows in Python after a fixed reading has returned.
"""

import asyncio
import re
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sentinel.ai.activity import prepare_reading
from sentinel.config.settings import Settings
from sentinel.persistence.models import DatabaseTarget

# Fleet tools, the live reading each one needs (None means stored data only), and what the model is told.
FLEET_TOOLS: dict[str, tuple[str | None, str]] = {
    "get_fleet_health": (None, "Stored health of every enabled database, worst first, with last seen and open incidents."),
    "get_fleet_incidents": (None, "Which databases have open or recent incidents, with severity and the latest message."),
    "get_fleet_compliance": (None, "Which databases fail an organizational compliance policy, and which policies."),
    "get_fleet_tablespaces": (None, "Stored tablespace percent used across the fleet. Optional threshold_pct, default 85."),
    "find_fleet_programs": (
        "session_programs",
        "Which databases have user sessions from given program or executable names. Needs programs, a list of names.",
    ),
    "find_fleet_datafiles": (
        "datafile_sizes",
        "Which databases have a tablespace whose largest datafile exceeds a size. Optional threshold_gb, default 10.",
    ),
}
FLEET_ROUTE_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["tool"],
    "properties": {
        "tool": {"type": "string"},
        "programs": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "threshold_gb": {"type": "number"},
        "threshold_pct": {"type": "number"},
    },
}

_PROGRAM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@ \-]{0,63}$")
_EXECUTABLE = re.compile(r"(?i)(?<![\w.\-])[A-Za-z0-9_][A-Za-z0-9_\-]{0,60}\.(?:exe|jar|bat|cmd|sh|py|dll)\b")
_QUOTED = re.compile(r"['\"‘’“”]([^'\"‘’“”]{2,64})['\"‘’“”]")
_PROGRAM_WORDS = re.compile(r"(?i)\b(?:process(?:es)?|programs?|executables?|applications?|clients?|binar(?:y|ies))\b")
_PROGRAM_STATE = re.compile(r"(?i)\b(?:running|connected|active|logged|sessions?|using|attached)\b")
_SIZE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(tb|gb|g|mb|m)\b")
_PERCENT = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(?:%|percent|pct)")
_DATAFILE = re.compile(r"(?i)\bdata\s*files?\b|\bdatafiles?\b")
_TABLESPACE = re.compile(r"(?i)\btablespaces?\b")
_BIG = re.compile(r"(?i)\b(?:larger|bigger|greater|more|over|above|exceed\w*|at least|large|big|largest|biggest)\b")
_FULL = re.compile(r"(?i)\b(?:full|filling|fullest|space pressure|running out|out of space|used|usage|utili[sz]ation)\b")
_INCIDENT = re.compile(r"(?i)\bincidents?\b")
_POLICY = re.compile(r"(?i)\b(?:violat\w*|non[- ]?complian\w*|out of compliance|complian\w*|polic(?:y|ies))\b")
_SUBJECT = re.compile(r"(?i)\b(?:databases?|fleet|dbs?|instances?|estate|which|who|any|every|all)\b")
_EXPLAIN = re.compile(r"(?i)\b(?:what (?:is|are) (?:our|the|a|an)\b|what does|explain|define|meaning of|how does|how do)\b")
_HEALTH = re.compile(
    r"(?i)\b(?:down|offline|unreachable|unhealthy|healthy|health|status|stale|not responding|up and running|"
    r"availability|overview|summary)\b",
)


def clean_programs(values: object, question: str | None = None) -> list[str]:
    """Program names safe to compare. With a question, a name must appear in it, so none is invented."""
    if not isinstance(values, list):
        return []
    folded = question.lower() if question is not None else None
    names: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        name = value.strip().strip("'\"").strip()
        if _PROGRAM_NAME.fullmatch(name) is None:
            continue
        if folded is not None and name.lower() not in folded:
            continue
        if name.lower() not in [item.lower() for item in names]:
            names.append(name)
    return names[:8]


def clean_number(value: object, low: float, high: float, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return min(high, max(low, float(value)))


def _size_gb(question: str) -> float | None:
    match = _SIZE.search(question)
    if match is None:
        return None
    amount = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "tb":
        amount *= 1024
    elif unit in {"mb", "m"}:
        amount /= 1024
    return clean_number(amount, 0.001, 1_000_000, 10)


def fleet_requests(question: str) -> list[tuple[str, dict[str, object]]]:
    """Fixed routing for fleet chat. Returns the fleet tools to run, in order, with their arguments."""
    requests: list[tuple[str, dict[str, object]]] = []
    explaining = _EXPLAIN.search(question) is not None

    names = [match.group(0) for match in _EXECUTABLE.finditer(question)]
    if _PROGRAM_WORDS.search(question):
        names.extend(match.group(1) for match in _QUOTED.finditer(question))
    programs = clean_programs(names, question)
    if programs or (_PROGRAM_WORDS.search(question) and _PROGRAM_STATE.search(question) and not explaining):
        requests.append(("find_fleet_programs", {"programs": programs}))

    size = _size_gb(question)
    if _DATAFILE.search(question) and (size is not None or _BIG.search(question)):
        requests.append(("find_fleet_datafiles", {"threshold_gb": size if size is not None else 10.0}))
    elif _TABLESPACE.search(question) and size is not None:
        requests.append(("find_fleet_datafiles", {"threshold_gb": size}))
    elif _TABLESPACE.search(question) and not explaining and (_PERCENT.search(question) or _FULL.search(question)):
        percent = _PERCENT.search(question)
        requests.append(("get_fleet_tablespaces", {
            "threshold_pct": clean_number(float(percent.group(1)), 1, 100, 85) if percent else 85.0,
        }))

    if _INCIDENT.search(question) and not explaining:
        requests.append(("get_fleet_incidents", {}))
    if _POLICY.search(question) and _SUBJECT.search(question) and not explaining:
        requests.append(("get_fleet_compliance", {}))
    if not requests and _HEALTH.search(question) and not explaining:
        requests.append(("get_fleet_health", {}))
    return requests[:3]


def nominated_request(parsed: object, question: str) -> tuple[str, dict[str, object]] | None:
    """Validate what the model nominated. An unknown tool or an invented program name is dropped."""
    if not isinstance(parsed, dict):
        return None
    tool = parsed.get("tool")
    if not isinstance(tool, str) or tool not in FLEET_TOOLS:
        return None
    arguments: dict[str, object] = {}
    if tool == "find_fleet_programs":
        arguments["programs"] = clean_programs(parsed.get("programs"), question)
    elif tool == "find_fleet_datafiles":
        arguments["threshold_gb"] = clean_number(parsed.get("threshold_gb"), 0.001, 1_000_000, 10)
    elif tool == "get_fleet_tablespaces":
        arguments["threshold_pct"] = clean_number(parsed.get("threshold_pct"), 1, 100, 85)
    return tool, arguments


@dataclass
class DatabaseScan:
    target_id: str
    name: str
    alias: str
    environment: str
    status: str
    rows: list[dict[str, str]] = field(default_factory=list)
    note: str = ""


@dataclass
class FleetScan:
    kind: str
    databases: list[DatabaseScan]
    not_read: int = 0  # enabled databases beyond the per-question limit


async def scan_fleet(settings: Settings, session: Session, kind: str) -> FleetScan:
    """Run one fixed reading on every enabled database, a few at a time, each with its own deadline."""
    limit = settings.ai_fleet_max_databases
    total = int(session.scalar(
        select(func.count()).select_from(DatabaseTarget).where(DatabaseTarget.enabled.is_(True)),
    ) or 0)
    targets = session.scalars(
        select(DatabaseTarget).where(DatabaseTarget.enabled.is_(True))
        .order_by(DatabaseTarget.display_name, DatabaseTarget.id).limit(limit),
    ).all()
    plain = [
        (str(target.id), target.display_name, (target.tns_alias or "").strip(), target.environment or "")
        for target in targets
    ]
    gate = asyncio.Semaphore(settings.ai_fleet_concurrency)

    async def read(item: tuple[str, str, str, str]) -> DatabaseScan:
        target_id, name, alias, environment = item
        async with gate:
            try:
                reading = await asyncio.wait_for(
                    prepare_reading(settings, alias, kind), timeout=settings.ai_fleet_database_timeout_seconds,
                )
            except TimeoutError:
                return DatabaseScan(target_id, name, alias, environment, "unavailable", [], "did not answer in time")
        return DatabaseScan(target_id, name, alias, environment, reading.status, reading.rows, reading.note)

    databases = list(await asyncio.gather(*(read(item) for item in plain)))
    return FleetScan(kind, databases, max(0, total - len(plain)))
