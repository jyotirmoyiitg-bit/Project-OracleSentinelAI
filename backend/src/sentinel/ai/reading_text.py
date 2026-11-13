"""Plain-language lines for the extended database readings."""


def present_reading(kind: str, rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    handler = _HANDLERS.get(kind, _generic)
    summary, lines = handler(rows)
    if not lines:
        lines = [summary]
    return summary, lines


def _generic(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = [_row_line(row) for row in rows]
    if not lines:
        return "No rows were returned.", ["No rows were returned."]
    return f"{len(lines)} rows read from the database.", lines


def _availability(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    instance = _one(rows, "v$instance")
    database = _one(rows, "v$database")
    lines = [
        "This is a live read. A portal status of Unknown means the last scheduled collection did not succeed.",
    ]
    if instance:
        lines.append(
            f"Instance {instance.get('instance_name') or 'unknown'} status {instance.get('status') or 'unknown'} "
            f"database_status {instance.get('database_status') or 'unknown'} on {instance.get('host_name') or 'unknown'}. "
            f"Started {instance.get('startup_time') or 'unknown'} "
            f"({instance.get('uptime_hours') or 'unknown'} hours ago).",
        )
    if database:
        lines.append(
            f"Database {database.get('name') or 'unknown'} open_mode {database.get('open_mode') or 'unknown'} "
            f"role {database.get('database_role') or 'unknown'} log_mode {database.get('log_mode') or 'unknown'} "
            f"protection {database.get('protection_mode') or 'unknown'} "
            f"switchover {database.get('switchover_status') or 'unknown'}.",
        )
    lines.extend(
        f"PDB {row.get('name') or 'unknown'} open_mode {row.get('open_mode') or 'unknown'} "
        f"restricted {row.get('restricted') or 'NO'}"
        for row in _source(rows, "v$pdbs")
    )
    services = [row.get("name") or "" for row in _source(rows, "v$services") if row.get("name")]
    if services:
        lines.append("Services: " + ", ".join(services[:20]))
    outages = _source(rows, "alert_outage")
    if outages:
        lines.append("Alert lines mentioning startup or shutdown in the last 7 days. A planned restart appears here too.")
        lines.extend(f"{row.get('occurred_at') or ''} {row.get('message_text') or ''}".strip() for row in outages)
    else:
        lines.append("No startup or shutdown message was found in the alert log for the last 7 days.")
    portal = _one(rows, "portal")
    if portal:
        lines.append(
            f"Portal status for {portal.get('display_name') or 'this database'} is "
            f"{portal.get('health') or 'unknown'}.",
        )
    open_mode = (database.get("open_mode") if database else "") or ""
    status = (instance.get("status") if instance else "") or "unknown"
    summary = f"Instance status is {status} and open mode is {open_mode or 'unknown'}."
    return summary, lines


def _errors(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = [
        f"{row.get('occurred_at') or ''} {row.get('message_text') or ''}".strip()
        for row in rows
    ]
    if not lines:
        return (
            "No critical ORA error from the watched list was found in the alert log for the last 24 hours.",
            ["No critical ORA error from the watched list was found in the alert log for the last 24 hours."],
        )
    return f"{len(lines)} critical alert lines in the last 24 hours.", lines


def _ora_errors(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = [
        f"{row.get('occurred_at') or ''} {row.get('message_text') or ''}".strip()
        for row in rows
        if "ORA-" in (row.get("message_text") or "").upper()
    ]
    if not lines:
        empty = "There are no Oracle errors reported in the last 24 hours."
        return empty, [empty]
    noun = "error" if len(lines) == 1 else "errors"
    return f"{len(lines)} Oracle {noun} in the last 24 hours.", lines


def _alert_log(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    entries = _source(rows, "v$diag_alert_ext") or rows
    lines = [
        "Newest lines from this database alert log for the last 7 days. Listener and client traces are excluded.",
    ]
    lines.extend(
        f"{row.get('occurred_at') or ''} {row.get('message_text') or ''}".strip()
        for row in entries
        if (row.get("message_text") or "").strip()
    )
    if len(lines) == 1:
        lines.append("No alert log line was returned for the last 7 days.")
        return "No alert log line was returned for the last 7 days.", lines
    return f"{len(lines) - 1} latest alert log lines.", lines


def _tablespace(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    spaces = _source(rows, "tablespace")
    lines = []
    hot = 0
    for row in spaces:
        pct = _number(row.get("used_pct"))
        band = _band(pct)
        if band:
            hot += 1
        lines.append(
            f"{row.get('tablespace_name') or 'UNKNOWN'} {row.get('used_pct') or '0'} percent used "
            f"({_bytes(row.get('bytes_used'))} of {_bytes(row.get('bytes_total'))}) "
            f"autoextend={'YES' if row.get('autoextend') == '1' else 'NO'} {band}".strip(),
        )
    limits = _source(rows, "datafile_limit")
    if limits:
        lines.append("Datafiles at or above 90 percent of their autoextend maximum:")
        lines.extend(
            f"{row.get('tablespace_name') or ''} {row.get('file_name') or ''} "
            f"{_bytes(row.get('bytes'))} of max {_bytes(row.get('maxbytes'))}"
            for row in limits
        )
    else:
        lines.append("No autoextensible datafile is at or above 90 percent of its maximum size.")
    for row in _source(rows, "v$temp_space_header"):
        lines.append(
            f"Temporary {row.get('tablespace_name') or 'TEMP'} {row.get('used_pct') or '0'} percent used "
            f"({_bytes(row.get('bytes_used'))} used, {_bytes(row.get('bytes_free'))} free).",
        )
    summary = (
        f"{hot} tablespaces are at or above 80 percent."
        if hot
        else "No permanent tablespace is at or above 80 percent."
    )
    return summary, lines or [summary]


def _performance(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = [(
        "No stored performance baseline is compared. CPU and wait figures are cumulative since startup, "
        "not a difference from a normal hour, and they are not from AWR."
    )]
    values = {_name(row): _number(row.get("value")) for row in _source(rows, "v$sys_time_model")}
    db_cpu = values.get("db cpu")
    db_time = values.get("db time")
    if db_cpu is not None and db_time:
        lines.append(f"Database CPU is {db_cpu * 100 / db_time:.0f} percent of database time since startup.")
    busy = _number(_stat(rows, "v$osstat", "BUSY_TIME"))
    idle = _number(_stat(rows, "v$osstat", "IDLE_TIME"))
    if busy is not None and idle is not None and busy + idle > 0:
        lines.append(f"Host CPU busy time is {busy * 100 / (busy + idle):.0f} percent of busy plus idle since boot.")
    sessions = _source(rows, "v$session")
    if sessions:
        lines.append(
            "User sessions by status: "
            + ", ".join(f"{row.get('status') or 'UNKNOWN'} {row.get('session_count') or '0'}" for row in sessions),
        )
    waits = _source(rows, "v$system_event")
    lines.extend(
        f"Wait {row.get('event') or 'unknown'} waits={row.get('total_waits') or '0'} "
        f"time={_micros(row.get('time_waited_micro'))}"
        for row in waits
    )
    for row in _source(rows, "v$filestat"):
        reads = _number(row.get("phyrds")) or 0
        read_cs = _number(row.get("readtim")) or 0
        latency = f"{read_cs * 10 / reads:.1f} ms" if reads else "n/a"
        lines.append(
            f"File {row.get('file_no') or ''} reads={row.get('phyrds') or '0'} "
            f"writes={row.get('phywrts') or '0'} average read latency {latency} since startup.",
        )
    return "Performance evidence is cumulative since startup.", lines


def _blocking(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    people = _source(rows, "v$session")
    blocked = [row for row in people if row.get("blocking_session")]
    long_running = [row for row in people if (_number(row.get("last_call_et")) or 0) > 600]
    lines: list[str] = []
    if blocked:
        lines.extend(
            f"BLOCKED {row.get('username') or ''} sid {row.get('sid') or ''} waits on {row.get('event') or 'unknown'} "
            f"for {row.get('seconds_in_wait') or '0'}s, blocked by session {row.get('blocking_session')}."
            for row in blocked
        )
    else:
        lines.append("No user session is currently blocked by another session.")
    if long_running:
        lines.extend(
            f"LONG {row.get('username') or ''} sid {row.get('sid') or ''} status {row.get('status') or ''} "
            f"last call {_seconds(row.get('last_call_et'))} event {row.get('event') or 'unknown'} "
            f"service {row.get('service_name') or 'unknown'}."
            for row in long_running
        )
    else:
        lines.append("No user session has a current call older than 10 minutes.")
    counts = _source(rows, "session_count")
    lines.extend(
        f"{row.get('username') or ''} service {row.get('service_name') or 'unknown'} "
        f"sessions {row.get('session_count') or '0'}"
        for row in counts
    )
    for row in _source(rows, "v$resource_limit"):
        current = _number(row.get("current_utilization")) or 0
        limit = _number(row.get("limit_value"))
        if limit and limit > 0:
            lines.append(
                f"{row.get('resource_name') or ''} {int(current)} of {int(limit)} "
                f"({current * 100 / limit:.0f} percent).",
            )
        else:
            lines.append(f"{row.get('resource_name') or ''} current {row.get('current_utilization') or '0'}.")
    deadlocks = _source(rows, "alert_deadlock")
    if deadlocks:
        lines.extend(f"DEADLOCK {row.get('occurred_at') or ''} {row.get('message_text') or ''}".strip() for row in deadlocks)
    else:
        lines.append("No ORA-00060 deadlock was found in the alert log for the last 24 hours.")
    summary = (
        f"{len(blocked)} user sessions are blocked."
        if blocked
        else "No user session is currently blocked."
    )
    return summary, lines


def _sql(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = [(
        "Cursor cache only, ordered within each group. This is not AWR, so it cannot prove a statement "
        "is slower than it was yesterday."
    )]
    lines.extend(_sql_lines(rows, "sql_elapsed", "elapsed"))
    lines.extend(_sql_lines(rows, "sql_executions", "executions"))
    lines.extend(_sql_lines(rows, "sql_reads", "reads"))
    plans = _source(rows, "sql_plans")
    if plans:
        lines.append("More than one plan is in the cache for these SQL IDs. That does not by itself prove when it changed.")
        lines.extend(f"PLAN {row.get('sql_id') or ''} plans={row.get('plan_count') or '0'}" for row in plans)
    else:
        lines.append("No user SQL ID currently has more than one execution plan in the cache.")
    return "SQL workload from the current cursor cache.", lines


def _memory_config(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = [
        "Current memory, parse, and parameter values. This reading does not show who changed a parameter or when.",
    ]
    for row in _source(rows, "v$temp_space_header"):
        lines.append(
            f"Temporary {row.get('tablespace_name') or 'TEMP'} {row.get('used_pct') or '0'} percent used "
            f"({_bytes(row.get('bytes_used'))} used, {_bytes(row.get('bytes_free'))} free).",
        )
    stats = {_name(row): _number(row.get("value")) for row in _source(rows, "v$sysstat")}
    total = stats.get("parse count (total)")
    hard = stats.get("parse count (hard)")
    if total and hard is not None:
        lines.append(f"Hard parses are {hard:.0f} of {total:.0f} parses since startup ({hard * 100 / total:.1f} percent).")
    elif hard is not None:
        lines.append(f"Hard parses since startup: {hard:.0f}.")
    for row in _source(rows, "v$librarycache"):
        gets = _number(row.get("gets")) or 0
        hits = _number(row.get("gethits")) or 0
        ratio = f"{hits * 100 / gets:.1f} percent" if gets else "n/a"
        lines.append(
            f"Library {row.get('namespace') or ''} hit ratio {ratio} "
            f"reloads={row.get('reloads') or '0'} invalidations={row.get('invalidations') or '0'}.",
        )
    lines.extend(f"Parameter {row.get('name') or ''}={row.get('value') or ''}" for row in _source(rows, "v$parameter"))
    for row in _source(rows, "v$resource_limit"):
        lines.append(
            f"Limit {row.get('resource_name') or ''} current {row.get('current_utilization') or '0'} "
            f"max {row.get('max_utilization') or '0'} limit {row.get('limit_value') or 'unlimited'}.",
        )
    return "Memory configuration and parse activity since startup.", lines


def _recovery(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = [(
        "No recovery point or recovery time objective is stored for this database. "
        "Backup, fast recovery area, and archive evidence is below."
    )]
    success = _source(rows, "backup_success")
    if success:
        lines.extend(
            f"SUCCESS {row.get('input_type') or 'backup'} {row.get('status') or ''} "
            f"started {row.get('start_time') or ''} ended {row.get('end_time') or ''} "
            f"output={_bytes(row.get('output_bytes'))}"
            for row in success
        )
    else:
        lines.append("No completed RMAN backup was returned.")
    failed = _source(rows, "backup_failed")
    if failed:
        lines.extend(
            f"FAILED {row.get('input_type') or 'backup'} {row.get('status') or ''} started {row.get('start_time') or ''}"
            for row in failed
        )
    else:
        lines.append("No failed RMAN backup was returned for the last 7 days.")
    for row in _source(rows, "v$recovery_file_dest"):
        lines.append(
            f"Fast recovery area {row.get('name') or ''} {row.get('used_pct') or '0'} percent used "
            f"({_bytes(row.get('space_used'))} of {_bytes(row.get('space_limit'))}).",
        )
    archives = _source(rows, "v$archived_log")
    if archives:
        backed = sum(1 for row in archives if (_number(row.get("backup_count")) or 0) > 0)
        lines.append(f"{len(archives)} recent archive logs, {backed} with a backup count above zero.")
        lines.extend(
            f"ARCHIVE sequence {row.get('sequence_no') or ''} at {row.get('completion_time') or ''} "
            f"status {row.get('status') or ''} backups={row.get('backup_count') or '0'}"
            for row in archives[:8]
        )
    else:
        lines.append("No archive log from the last 2 days was returned.")
    return "Backup and archive evidence. Recovery objectives are not configured here.", lines


def _dataguard(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    database = _one(rows, "v$database")
    lines = []
    if database:
        lines.append(
            f"Role {database.get('database_role') or 'unknown'} open_mode {database.get('open_mode') or 'unknown'} "
            f"protection {database.get('protection_mode') or 'unknown'} "
            f"switchover {database.get('switchover_status') or 'unknown'}.",
        )
    dests = _source(rows, "v$archive_dest_status")
    if dests:
        lines.extend(
            f"Destination {row.get('dest_id') or ''} {row.get('destination') or ''} "
            f"status {row.get('status') or 'unknown'} {row.get('error') or ''}".strip()
            for row in dests
        )
    else:
        lines.append("No archive destination was returned.")
    stats = _source(rows, "v$dataguard_stats")
    if stats:
        lines.extend(f"Data Guard {row.get('name') or ''}={row.get('value') or 'none'}" for row in stats)
    else:
        lines.append("No Data Guard lag row was returned. This database may not have a standby.")
    role = database.get("database_role") if database else "unknown"
    return f"Database role is {role or 'unknown'}.", lines


def _database_users(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    accounts = _source(rows, "dba_users")
    lines = [
        f"{row.get('username') or 'unknown'} {row.get('account_status') or ''}".strip()
        for row in accounts
    ]
    if not lines:
        return "No database users were returned.", ["No database users were returned."]
    if len(lines) >= 80:
        lines.append("The list stops at 80 users.")
    return f"{len(accounts)} database users.", lines


def _security(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = [(
        "Locked or expired accounts and invalid objects are current state. "
        "Recent grant changes are not visible because the audit trail is not read."
    )]
    accounts = _source(rows, "dba_users")
    if accounts:
        lines.extend(
            f"ACCOUNT {row.get('username') or ''} {row.get('account_status') or ''} "
            f"locked {row.get('lock_date') or 'n/a'} expires {row.get('expiry_date') or 'n/a'}"
            for row in accounts
        )
    else:
        lines.append("No locked or expired application account was returned.")
    objects = _source(rows, "dba_objects")
    if objects:
        lines.extend(
            f"INVALID {row.get('owner') or ''}.{row.get('object_name') or ''} ({row.get('object_type') or ''})"
            for row in objects
        )
    else:
        lines.append("No invalid application object was returned.")
    roles = _source(rows, "dba_role_privs")
    lines.extend(
        f"ROLE {row.get('grantee') or ''} has {row.get('granted_role') or ''}"
        for row in roles
    )
    if not roles:
        lines.append("No extra privileged role grant was returned for non-admin accounts.")
    summary = f"{len(accounts)} locked or expired accounts and {len(objects)} invalid objects."
    return summary, lines


def _failed_jobs(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = [
        f"{row.get('owner') or ''}.{row.get('job_name') or ''} {row.get('status') or ''} at {row.get('log_date') or ''}".strip()
        for row in rows
    ]
    if not lines:
        return (
            "No scheduler job failed or stopped in the last 2 days.",
            ["No scheduler job failed or stopped in the last 2 days."],
        )
    return f"{len(lines)} scheduler jobs did not succeed in the last 2 days.", lines


def _growth(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = [
        "Growth uses retained Sentinel samples, not AWR. A forecast needs regular samples spanning at least 24 hours.",
    ]
    forecasts = [row for row in rows if row.get("status") == "forecast" and row.get("days_to_exhaustion")]
    if not forecasts:
        lines.append("Retained history is not sufficient to say when a tablespace or the fast recovery area will fill.")
        return "No tablespace exhaustion forecast is available yet.", lines
    for row in forecasts:
        rate = _number(row.get("growth_rate_gb_per_day"))
        days = _number(row.get("days_to_exhaustion"))
        week = f"{rate * 7:.2f} GB" if rate is not None else "unknown"
        month = f"{rate * 30:.2f} GB" if rate is not None else "unknown"
        lines.append(
            f"{row.get('resource') or 'resource'} fills in {days:.0f} days at "
            f"{rate if rate is not None else 'unknown'} GB/day "
            f"(about {week} in 7 days and {month} in 30 days)."
            if days is not None
            else f"{row.get('resource') or 'resource'} status {row.get('status') or 'unknown'}.",
        )
    soonest = min(forecasts, key=lambda row: _number(row.get("days_to_exhaustion")) or 10**9)
    return f"{soonest.get('resource') or 'A resource'} is the soonest forecast to fill.", lines


def _issues(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    summary, lines = _availability([row for row in rows if row.get("source") in {"v$instance", "v$database", "portal"}])
    error_summary, error_lines = _errors(_source(rows, "v$diag_alert_ext"))
    space_summary, space_lines = _tablespace(_source(rows, "tablespace"))
    block_summary, block_lines = _blocking(rows)
    lines = [
        (
            "These are the current readings. They are evidence for a likely cause, not a confirmed root cause. "
            "Past incidents and runbooks are cited separately when they were found."
        ),
        summary,
        *lines[1:],
        error_summary,
        *error_lines,
        space_summary,
        *space_lines,
        block_summary,
        *block_lines,
    ]
    return "Current availability, errors, space, and blocking evidence.", lines[:40]


def _sql_lines(rows: list[dict[str, str]], source: str, label: str) -> list[str]:
    found = _source(rows, source)
    if not found:
        return [f"No user SQL was returned for {label}."]
    return [
        f"{label.upper()} {row.get('sql_id') or ''} {row.get('parsing_schema_name') or ''} "
        f"executions={row.get('executions') or '0'} cpu={_micros(row.get('cpu_time'))} "
        f"elapsed={_micros(row.get('elapsed_time'))} buffers={row.get('buffer_gets') or '0'} "
        f"reads={row.get('disk_reads') or '0'} {row.get('sql_text') or ''}".strip()
        for row in found
    ]


def _source(rows: list[dict[str, str]], source: str) -> list[dict[str, str]]:
    return [row for row in rows if row.get("source") == source]


def _one(rows: list[dict[str, str]], source: str) -> dict[str, str] | None:
    found = _source(rows, source)
    return found[0] if found else None


def _stat(rows: list[dict[str, str]], source: str, name: str) -> str:
    for row in _source(rows, source):
        if (row.get("stat_name") or "").upper() == name.upper():
            return row.get("value") or ""
    return ""


def _row_line(row: dict[str, str]) -> str:
    parts = [f"{key}={value}" for key, value in row.items() if key != "source" and value]
    return " ".join(parts) or row.get("source") or "row"


def _name(row: dict[str, str]) -> str:
    return (row.get("stat_name") or row.get("name") or "").strip().lower()


def _number(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _bytes(value: str | None) -> str:
    amount = _number(value)
    if amount is None or amount < 0:
        return value or "unknown"
    size = float(amount)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "unknown"


def _band(pct: float | None) -> str:
    if pct is None:
        return ""
    if pct >= 95:
        return "above 95 percent"
    if pct >= 90:
        return "above 90 percent"
    if pct >= 80:
        return "above 80 percent"
    return ""


def _micros(value: str | None) -> str:
    amount = _number(value)
    if amount is None:
        return "n/a"
    return f"{amount / 1_000_000:.1f}s"


def _seconds(value: str | None) -> str:
    amount = _number(value)
    if amount is None:
        return "unknown"
    if amount < 90:
        return f"{amount:.0f}s"
    return f"{amount / 60:.0f}m"


_HANDLERS = {
    "availability": _availability,
    "errors": _errors,
    "ora_errors": _ora_errors,
    "alert_log": _alert_log,
    "tablespace": _tablespace,
    "performance": _performance,
    "blocking": _blocking,
    "sql_workload": _sql,
    "memory_config": _memory_config,
    "recovery": _recovery,
    "dataguard": _dataguard,
    "database_users": _database_users,
    "security": _security,
    "failed_jobs": _failed_jobs,
    "growth": _growth,
    "issues": _issues,
}
