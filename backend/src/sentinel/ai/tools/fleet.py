"""Fleet tools. Stored status, incidents, compliance, and tablespaces; live programs and datafile sizes.

The live tools format a scan the orchestrator already ran. A tool does not open Oracle itself.
A database that could not be read is reported as unread. It is never counted as clean.
"""

from datetime import timedelta

from sqlalchemy import select

from sentinel.ai.fleet import DatabaseScan, FleetScan, clean_number, clean_programs
from sentinel.ai.safety import sanitize_untrusted
from sentinel.ai.tools.context import ToolContext
from sentinel.ai.types import EvidenceCitation, ToolOutcome
from sentinel.collectors.normalization import utcnow
from sentinel.persistence.models import CurrentObservation, DatabaseTarget, Incident, PolicyResult

_GB = 1024 ** 3
_SEVERITY_ORDER = {"critical": 0, "high": 1, "warning": 2, "medium": 3, "low": 4, "info": 5}


def _targets(context: ToolContext) -> list[DatabaseTarget]:
    return list(context.session.scalars(
        select(DatabaseTarget).where(DatabaseTarget.enabled.is_(True)).order_by(DatabaseTarget.display_name).limit(500),
    ).all())


def _names(names: list[str], limit: int = 15) -> str:
    shown = ", ".join(names[:limit])
    return shown if len(names) <= limit else f"{shown}, and {len(names) - limit} more"


def _outcome(
    tool: str, summary: str, matched: list[tuple[str, str, str]], extra: list[str], payload: dict[str, object],
) -> ToolOutcome:
    """matched is (database name, target id, line). Extra lines follow the matches."""
    lines = [sanitize_untrusted(line, 400) for _name, _target, line in matched]
    lines.extend(sanitize_untrusted(line, 400) for line in extra if line)
    citations = [
        EvidenceCitation("metric", name, f"{tool}:{name}"[:120], sanitize_untrusted(line, 400), target_id)
        for name, target_id, line in matched[:12]
    ]
    return ToolOutcome(
        tool, "allowed", sanitize_untrusted(summary, 400), citations,
        {"lines": lines[:80], "matched": [name for name, _target, _line in matched], **payload},
    )


def _gb(value: float) -> str:
    amount = value / _GB
    text = f"{amount:.1f}".removesuffix(".0")
    return f"{text} GB"


def _number(value: str | None) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _unread(scan: FleetScan) -> tuple[list[DatabaseScan], list[str]]:
    """Databases that answered, and the lines that say which did not."""
    answered = [item for item in scan.databases if item.status == "ok"]
    lines: list[str] = []
    failed = [item for item in scan.databases if item.status != "ok"]
    if failed:
        parts = [f"{item.name} ({sanitize_untrusted(item.note or item.status, 80)})" for item in failed[:15]]
        lines.append("Could not be read, so not known: " + "; ".join(parts) + ".")
    if scan.not_read:
        lines.append(f"{scan.not_read} more enabled databases were not read because of the per-question limit.")
    return answered, lines


def fleet_incidents(context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    """Databases with an open incident, and those with an incident in the last 7 days. Stored data."""
    del arguments
    targets = _targets(context)
    by_id = {target.id: target for target in targets}
    since = utcnow() - timedelta(days=7)
    rows = context.session.scalars(
        select(Incident).where((Incident.state != "resolved") | (Incident.last_seen >= since))
        .order_by(Incident.last_seen.desc()).limit(2000),
    ).all()
    grouped: dict[object, list[Incident]] = {}
    for row in rows:
        if row.target_id in by_id:
            grouped.setdefault(row.target_id, []).append(row)
    matched: list[tuple[str, str, str]] = []
    open_databases = 0
    for target_id, incidents in grouped.items():
        target = by_id[target_id]
        opened = [item for item in incidents if (item.state or "new") != "resolved"]
        severities: dict[str, int] = {}
        for item in opened:
            key = (item.severity or "unknown").lower()
            severities[key] = severities.get(key, 0) + 1
        latest = incidents[0]
        breakdown = ", ".join(
            f"{count} {name}" for name, count in sorted(severities.items(), key=lambda pair: _SEVERITY_ORDER.get(pair[0], 9))
        )
        seen = latest.last_seen.strftime("%Y-%m-%d %H:%M UTC") if latest.last_seen else "unknown time"
        if opened:
            open_databases += 1
            head = f"{target.display_name}: {len(opened)} open incidents ({breakdown})"
        else:
            head = f"{target.display_name}: no open incident, {len(incidents)} resolved in the last 7 days"
        line = f"{head}. Latest {(latest.severity or 'unknown').lower()} {latest.state or 'new'} at {seen}: {(latest.message or '').strip()[:160]}"
        rank = min((_SEVERITY_ORDER.get((item.severity or '').lower(), 9) for item in opened), default=9)
        matched.append((f"{rank}{target.display_name}", str(target.id), line))
    matched.sort(key=lambda item: item[0])
    matched = [(name[1:], target_id, line) for name, target_id, line in matched]
    clean = [target.display_name for target in targets if target.id not in grouped]
    extra = [f"No incident in the last 7 days: {_names(clean)}."] if clean else []
    if not targets:
        summary = "No enabled database is registered."
    else:
        summary = (
            f"{open_databases} of {len(targets)} enabled databases have an open incident. "
            f"{len(matched)} reported an incident in the last 7 days."
        )
    return _outcome("get_fleet_incidents", summary, matched, extra, {"databases": len(targets), "open_databases": open_databases})


def fleet_compliance(context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    """Databases that fail a compliance policy. Stored evaluation results, latest per policy."""
    del arguments
    targets = _targets(context)
    rows = context.session.scalars(select(PolicyResult).order_by(PolicyResult.observed_at.desc()).limit(20000)).all()
    latest: dict[tuple[object, str], PolicyResult] = {}
    for row in rows:
        latest.setdefault((row.target_id, row.policy_id), row)
    matched: list[tuple[str, str, str]] = []
    passing: list[str] = []
    unknown: list[str] = []
    for target in targets:
        results = [row for (target_id, _policy), row in latest.items() if target_id == target.id]
        if not results:
            unknown.append(target.display_name)
            continue
        failing = sorted(
            (row for row in results if row.result == "fail"),
            key=lambda row: (_SEVERITY_ORDER.get((row.severity or "").lower(), 9), row.policy_id),
        )
        if not failing:
            passing.append(target.display_name)
            continue
        listed = ", ".join(f"{row.policy_id} ({(row.severity or 'unknown').lower()})" for row in failing[:8])
        more = f", and {len(failing) - 8} more" if len(failing) > 8 else ""
        count = "1 policy" if len(failing) == 1 else f"{len(failing)} policies"
        matched.append((target.display_name, str(target.id), f"{target.display_name}: {count} failing: {listed}{more}"))
    extra: list[str] = []
    if passing:
        extra.append(f"No failing policy: {_names(passing)}.")
    if unknown:
        extra.append(f"Not evaluated yet, so not known: {_names(unknown)}.")
    if not targets:
        summary = "No enabled database is registered."
    else:
        summary = f"{len(matched)} of {len(targets)} enabled databases fail at least one compliance policy."
    return _outcome("get_fleet_compliance", summary, matched, extra, {"databases": len(targets), "not_evaluated": len(unknown)})


def fleet_tablespaces(context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    """Tablespaces at or above a percent used, from the latest stored collection."""
    threshold = clean_number(arguments.get("threshold_pct"), 1, 100, 85)
    targets = _targets(context)
    by_id = {target.id: target for target in targets}
    rows = context.session.scalars(
        select(CurrentObservation).where(CurrentObservation.metric_name == "storage.tablespace.used_pct").limit(20000),
    ).all()
    per_target: dict[object, list[tuple[str, float]]] = {}
    measured: set[object] = set()
    for row in rows:
        if row.target_id not in by_id or row.value is None:
            continue
        measured.add(row.target_id)
        if row.value >= threshold:
            labels = row.labels if isinstance(row.labels, dict) else {}
            name = str(labels.get("tablespace_name") or labels.get("tablespace") or "tablespace")
            per_target.setdefault(row.target_id, []).append((name, float(row.value)))
    matched: list[tuple[str, str, str]] = []
    for target_id, spaces in per_target.items():
        target = by_id[target_id]
        spaces.sort(key=lambda pair: -pair[1])
        listed = ", ".join(f"{name} {value:.1f} percent used" for name, value in spaces[:8])
        matched.append((f"{100 - spaces[0][1]:07.2f}{target.display_name}", str(target.id), f"{target.display_name}: {listed}"))
    matched.sort(key=lambda item: item[0])
    matched = [(name[7:], target_id, line) for name, target_id, line in matched]
    below = [target.display_name for target in targets if target.id in measured and target.id not in per_target]
    unmeasured = [target.display_name for target in targets if target.id not in measured]
    extra: list[str] = []
    if below:
        extra.append(f"No tablespace at or above {threshold:g} percent: {_names(below)}.")
    if unmeasured:
        extra.append(f"No stored tablespace measurement, so not known: {_names(unmeasured)}.")
    summary = (
        f"{len(matched)} of {len(targets)} enabled databases have a tablespace at or above {threshold:g} percent used, "
        "from the last stored collection."
    ) if targets else "No enabled database is registered."
    return _outcome("get_fleet_tablespaces", summary, matched, extra, {"threshold_pct": threshold})


def fleet_programs(context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    """Databases with user sessions from the named programs. Live reading of every database."""
    scan = context.fleet_scans.get("session_programs")
    if scan is None:
        return ToolOutcome("find_fleet_programs", "denied", "The fleet reading was not requested.", reason="not_requested")
    programs = clean_programs(arguments.get("programs"))
    wanted = [name.lower() for name in programs]
    answered, extra = _unread(scan)
    matched: list[tuple[str, str, str]] = []
    absent: list[str] = []
    for item in answered:
        rows = [row for row in item.rows if (row.get("program") or "").strip()]
        if wanted:
            rows = [row for row in rows if any(name in (row.get("program") or "").lower() for name in wanted)]
        else:
            rows = rows[:5]
        if not rows:
            absent.append(item.name)
            continue
        parts = [
            f"{row.get('program')} {int(_number(row.get('session_count')))} sessions "
            f"({int(_number(row.get('active_count')))} active)"
            for row in rows[:8]
        ]
        matched.append((item.name, item.target_id, f"{item.name}: " + "; ".join(parts)))
    lines: list[str] = []
    if absent:
        lines.append((f"No session from {_names(programs)}: " if wanted else "No user session: ") + _names(absent) + ".")
    lines.extend(extra)
    if wanted:
        summary = f"{_names(programs)}: found on {len(matched)} of {len(answered)} databases read."
        # Say for each name whether it is active, connected but idle, or absent, so "active" is not guessed.
        parts: list[str] = []
        for name, folded in zip(programs, wanted, strict=True):
            active = idle = 0
            for item in answered:
                rows = [row for row in item.rows if folded in (row.get("program") or "").lower()]
                if not rows:
                    continue
                if any(_number(row.get("active_count")) > 0 for row in rows):
                    active += 1
                else:
                    idle += 1
            if not active and not idle:
                parts.append(f"{name} was not found on any database read")
            elif idle and not active:
                parts.append(f"{name} is connected but idle on {idle}")
            elif idle:
                parts.append(f"{name} has an active session on {active} and is connected but idle on {idle}")
            else:
                parts.append(f"{name} has an active session on {active}")
        summary += " " + "; ".join(parts) + "."
    else:
        summary = f"The most connected programs on {len(matched)} of {len(answered)} databases read."
    summary += " Program names are what client sessions report now."
    return _outcome("find_fleet_programs", summary, matched, lines, {
        "programs": programs, "databases_read": len(answered), "databases_unread": len(scan.databases) - len(answered),
    })


def fleet_datafiles(context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    """Databases with a tablespace whose largest datafile is over a size. Live reading of every database."""
    scan = context.fleet_scans.get("datafile_sizes")
    if scan is None:
        return ToolOutcome("find_fleet_datafiles", "denied", "The fleet reading was not requested.", reason="not_requested")
    threshold = clean_number(arguments.get("threshold_gb"), 0.001, 1_000_000, 10)
    limit_bytes = threshold * _GB
    answered, extra = _unread(scan)
    matched: list[tuple[str, str, str]] = []
    absent: list[str] = []
    for item in answered:
        rows = [row for row in item.rows if _number(row.get("largest_file_bytes")) > limit_bytes]
        rows.sort(key=lambda row: -_number(row.get("largest_file_bytes")))
        if not rows:
            absent.append(item.name)
            continue
        parts = [
            f"{row.get('tablespace_name') or 'tablespace'} largest datafile {_gb(_number(row.get('largest_file_bytes')))}, "
            f"{int(_number(row.get('file_count')))} files, {_gb(_number(row.get('total_bytes')))} total"
            for row in rows[:6]
        ]
        matched.append((item.name, item.target_id, f"{item.name}: " + "; ".join(parts)))
    lines: list[str] = []
    if absent:
        lines.append(f"No datafile over {threshold:g} GB: {_names(absent)}.")
    lines.extend(extra)
    summary = f"{len(matched)} of {len(answered)} databases read have a datafile larger than {threshold:g} GB."
    return _outcome("find_fleet_datafiles", summary, matched, lines, {
        "threshold_gb": threshold, "databases_read": len(answered), "databases_unread": len(scan.databases) - len(answered),
    })
