"""Map an operator question to one fixed database reading."""

import re
from dataclasses import dataclass

from sentinel.ai.safety import sanitize_untrusted
from sentinel.ai.types import EvidenceCitation, ToolOutcome
from sentinel.config.settings import Settings

TOOL_NAMES = {
    "datafiles": "list_datafiles",
    "memory": "get_memory_status",
    "top_sql": "get_top_sql",
    "sessions": "get_sessions",
    "backups": "get_backup_status",
    "jobs": "get_running_jobs",
    "availability": "get_database_availability",
    "errors": "get_critical_errors",
    "tablespace": "get_tablespace_pressure",
    "growth": "get_tablespace_growth",
    "performance": "get_performance_status",
    "blocking": "get_blocking_sessions",
    "sql_workload": "get_sql_workload",
    "memory_config": "get_memory_configuration",
    "recovery": "get_recovery_status",
    "dataguard": "get_data_guard_status",
    "security": "get_security_posture",
    "failed_jobs": "get_failed_jobs",
    "issues": "get_current_issues",
}
ACTIVITY_TOOLS = frozenset(TOOL_NAMES.values())


@dataclass
class ActivityReading:
    kind: str
    tool_name: str
    status: str
    rows: list[dict[str, str]]
    note: str = ""


def match_activity(content: str) -> str | None:
    text = content.lower()
    if any(phrase in text for phrase in (
        "most critical", "root cause", "corrective action", "happened before", "what changed",
    )):
        return "issues"
    if any(phrase in text for phrase in ("ora-", "ora error", "critical ora", "alert log")):
        return "errors"
    if any(phrase in text for phrase in (
        "currently running", "and healthy", "restarted", "outage", "pdb",
        "services operational", "critical status", "unknown, warning",
    )):
        return "availability"
    if any(phrase in text for phrase in ("become full", "growth rate", "grown")):
        return "growth"
    if "temporary" in text or "temp tablespace" in text:
        return "memory_config"
    if any(phrase in text for phrase in (
        "tablespace", "autoextend", "maximum allocated", "running out of space", "80%", "90%", "95%",
    )):
        return "tablespace"
    if any(phrase in text for phrase in ("data file", "datafile", "temp file", "tempfile")):
        return "datafiles"
    if "list" in text and "file" in text:
        return "datafiles"
    if any(phrase in text for phrase in (
        "data guard", "standby", "transport lag", "apply lag", "failover", "switchover", "redo shipping",
    )):
        return "dataguard"
    if any(phrase in text for phrase in (
        "archive log", "recovery point", "recovery time", "rpo", "rto", "last successful",
    )) or ("backup" in text and "failed" in text) or ("rman" in text and "failed" in text):
        return "recovery"
    if "backup" in text or re.search(r"\brman\b", text):
        return "backups"
    if ("failed" in text and "job" in text) or "stopped executing" in text:
        return "failed_jobs"
    if any(phrase in text for phrase in ("scheduler", "dbms_job", "running job", "job running")):
        return "jobs"
    if "job" in text and any(word in text for word in ("running", "program", "scheduled")):
        return "jobs"
    if any(phrase in text for phrase in (
        "top query", "top sql", "consuming resource", "slow query", "highest cpu",
    )):
        return "top_sql"
    if any(phrase in text for phrase in (
        "sql id", "execution count", "execution plan", "physical read", "logical i/o", "logical io",
        "degradation", "elapsed time",
    )):
        return "sql_workload"
    if any(phrase in text for phrase in (
        "responding slowly", "cpu utilization", "active session", "wait event", "i/o", "io latency",
        "throughput", "baseline",
    )):
        return "performance"
    if any(phrase in text for phrase in (
        "blocking", "blocked", "unusually long", "session count", "deadlock", "lock contention",
        "close to exhaustion", "connection limit",
    )):
        return "blocking"
    if "who" in text and any(word in text for word in ("logon", "logged", "connected", "session")):
        return "sessions"
    if any(phrase in text for phrase in ("logged on", "logged in", "who is connected", "sessions")):
        return "sessions"
    if any(phrase in text for phrase in (
        "hard pars", "shared pool", "initialization parameter", "parameters changed",
        "resource utilization", "configured maximum",
    )):
        return "memory_config"
    if any(word in text for word in ("memory", "sga", "pga")):
        return "memory"
    if any(phrase in text for phrase in ("locked", "expired", "privileged", "invalid object", "invalid database")):
        return "security"
    return None


async def prepare_reading(settings: Settings, alias: str, kind: str) -> ActivityReading:
    tool_name = TOOL_NAMES[kind]
    if not alias.strip():
        return ActivityReading(kind, tool_name, "no_alias", [], "This database has no TNS alias.")
    if settings.synthetic_mode:
        return ActivityReading(kind, tool_name, "ok", [dict(row) for row in SYNTHETIC[kind]], "")
    from sentinel.mcp.client import ask_reading

    try:
        status, rows, note = await ask_reading(settings, tool_name, alias)
    except Exception:  # noqa: BLE001 -- the server path must not surface process or driver text
        return ActivityReading(kind, tool_name, "unavailable", [], "The readings server could not be reached.")
    if status not in {"ok", "not_granted", "unavailable"}:
        status = "unavailable"
    return ActivityReading(kind, tool_name, status, rows, note)


def reading_outcome(reading: ActivityReading, target_id: str | None) -> ToolOutcome:
    if reading.status != "ok":
        summary = sanitize_untrusted(reading.note or "The database reading is unavailable.", 400)
        return ToolOutcome(reading.tool_name, "allowed", summary, [], {"lines": [], "status": reading.status})
    summary, lines = _present(reading)
    clean = [sanitize_untrusted(line, 240) for line in lines if line.strip()]
    citations = [
        EvidenceCitation("metric", _label(reading.kind, line), f"{reading.tool_name}:{index}", line, target_id)
        for index, line in enumerate(clean[:12])
    ]
    if reading.note:
        summary = f"{summary} {sanitize_untrusted(reading.note, 200)}"
    return ToolOutcome(
        reading.tool_name, "allowed", sanitize_untrusted(summary, 400), citations,
        {"lines": clean, "status": reading.status},
    )


def _present(reading: ActivityReading) -> tuple[str, list[str]]:
    if reading.kind == "datafiles":
        return _files(reading.rows)
    if reading.kind == "memory":
        return _memory(reading.rows)
    if reading.kind == "top_sql":
        return _sql(reading.rows)
    if reading.kind == "sessions":
        return _sessions(reading.rows)
    if reading.kind == "backups":
        return _backups(reading.rows)
    if reading.kind == "jobs":
        return _jobs(reading.rows)
    from sentinel.ai.reading_text import present_reading

    return present_reading(reading.kind, reading.rows)


def _files(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = []
    for row in rows:
        kind = "tempfile" if row.get("source") == "dba_temp_files" else "datafile"
        online = row.get("online_status") or row.get("status") or ""
        lines.append(
            f"{row.get('tablespace_name') or 'UNKNOWN'} {kind} {row.get('file_name') or ''} "
            f"{_bytes(row.get('bytes', ''))} autoextend={row.get('autoextensible') or 'NO'} {online}".strip(),
        )
    if not lines:
        return "No datafiles were returned.", ["No datafiles were returned."]
    return f"{len(lines)} files read from the database.", lines


def _memory(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    values = {_name(row): _number(row.get("value", "")) for row in rows}
    lines = ["Database memory inside the instance (SGA and PGA), not host operating-system memory."]
    maximum = values.get("maximum sga size")
    free = values.get("free sga memory available")
    if maximum:
        used = maximum - (free or 0)
        lines.append(f"SGA used {_bytes(used)} of {_bytes(maximum)} ({_percent(used, maximum)}).")
    target = values.get("aggregate pga target parameter")
    allocated = values.get("total pga allocated")
    if allocated is not None:
        if target:
            lines.append(f"PGA allocated {_bytes(allocated)} of {_bytes(target)} target ({_percent(allocated, target)}).")
        else:
            lines.append(f"PGA allocated {_bytes(allocated)}. No PGA target is set.")
    for row in rows:
        lines.append(f"{row.get('pool', '').upper()} {row.get('name', '')} {_bytes(row.get('value', ''))}".strip())
    return "SGA and PGA readings from the database.", lines


def _sql(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = [
        f"{row.get('sql_id') or 'SQL'} {row.get('parsing_schema_name') or ''} "
        f"cpu={_micros(row.get('cpu_time', ''))} elapsed={_micros(row.get('elapsed_time', ''))} "
        f"executions={row.get('executions') or '0'} buffers={row.get('buffer_gets') or '0'} "
        f"{row.get('sql_text') or ''}".strip()
        for row in rows
    ]
    if not lines:
        return "No user SQL is in the current cursor cache.", ["No user SQL is in the current cursor cache."]
    return "Top SQL in the current cursor cache, ordered by CPU time. This is not AWR history.", lines


def _sessions(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    lines = [
        f"{row.get('username') or ''} {row.get('status') or ''} program={row.get('program') or 'unknown'} "
        f"machine={row.get('machine') or 'unknown'} sid={row.get('sid') or ''} since {row.get('logon_time') or ''}".strip()
        for row in rows
    ]
    if not lines:
        return "No user sessions are logged on.", ["No user sessions are logged on."]
    return f"{len(lines)} user sessions are logged on. Background processes are not listed.", lines


def _backups(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    running = [row for row in rows if row.get("source") == "v$session_longops"]
    recent = [row for row in rows if row.get("source") == "v$rman_backup_job_details"]
    lines: list[str] = []
    if running:
        lines.extend(
            f"RUNNING {row.get('opname') or 'backup'} target={row.get('target') or ''} "
            f"progress={row.get('sofar') or '0'}/{row.get('totalwork') or '0'} {row.get('units') or ''} "
            f"started {row.get('start_time') or ''}".strip()
            for row in running
        )
    else:
        lines.append("No backup is running.")
    lines.extend(
        f"{row.get('status') or 'UNKNOWN'} {row.get('input_type') or 'backup'} "
        f"started {row.get('start_time') or ''} ended {row.get('end_time') or 'still open'} "
        f"output={_bytes(row.get('output_bytes', ''))}"
        for row in recent
    )
    summary = "A backup is running." if running else "No backup is running."
    return summary, lines


def _jobs(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    scheduler = [row for row in rows if row.get("source") == "dba_scheduler_running_jobs"]
    legacy = [row for row in rows if row.get("source") == "dba_jobs_running"]
    lines = [
        f"RUNNING {row.get('owner') or ''}.{row.get('job_name') or ''} session={row.get('session_id') or ''} "
        f"elapsed={row.get('elapsed_time') or ''}".strip()
        for row in scheduler
    ]
    lines.extend(
        f"RUNNING DBMS_JOB {row.get('job') or ''} sid={row.get('sid') or ''} started {row.get('this_date') or ''}".strip()
        for row in legacy
    )
    if not lines:
        return "No scheduler job or DBMS_JOB is running.", ["No scheduler job or DBMS_JOB is running."]
    return f"{len(lines)} database jobs are running.", lines


def _label(kind: str, line: str) -> str:
    if kind == "memory":
        return "Memory"
    if kind == "backups":
        return "Backup"
    if kind == "jobs":
        return "Job"
    return line.split(" ", 1)[0][:80] or kind


def _name(row: dict[str, str]) -> str:
    return (row.get("name") or "").strip().lower()


def _number(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _bytes(value: str | float | None) -> str:
    amount = value if isinstance(value, (int, float)) else _number(value or "")
    if amount is None or amount < 0:
        return value if isinstance(value, str) and value else "unknown"
    size = float(amount)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "unknown"


def _percent(used: float, total: float) -> str:
    if total <= 0:
        return "n/a"
    return f"{used * 100 / total:.0f} percent"


def _micros(value: str) -> str:
    amount = _number(value)
    if amount is None:
        return value or "0s"
    return f"{amount / 1_000_000:.1f}s"


SYNTHETIC: dict[str, tuple[dict[str, str], ...]] = {
    "datafiles": (
        {
            "source": "dba_data_files", "file_name": "/u01/oradata/users01.dbf", "tablespace_name": "USERS",
            "bytes": "536870912", "autoextensible": "YES", "status": "AVAILABLE", "online_status": "ONLINE",
        },
    ),
    "memory": (
        {"source": "v$sgainfo", "pool": "sga", "name": "Maximum SGA Size", "value": "8589934592"},
        {"source": "v$sgainfo", "pool": "sga", "name": "Free SGA Memory Available", "value": "2147483648"},
        {"source": "v$pgastat", "pool": "pga", "name": "aggregate PGA target parameter", "value": "2147483648"},
        {"source": "v$pgastat", "pool": "pga", "name": "total PGA allocated", "value": "1073741824"},
    ),
    "top_sql": (
        {
            "source": "v$sql", "sql_id": "abc123def4567", "parsing_schema_name": "APPUSER",
            "executions": "20", "cpu_time": "12400000", "elapsed_time": "40100000",
            "buffer_gets": "1000", "disk_reads": "4", "sql_text": "SELECT * FROM orders",
        },
    ),
    "sessions": (
        {
            "source": "v$session", "username": "APPUSER", "status": "ACTIVE", "program": "sqlplus.exe",
            "machine": "DBHOST", "osuser": "oracle", "module": "SQL*Plus", "sid": "42",
            "serial_no": "9", "logon_time": "2026-11-12 10:00:00",
        },
    ),
    "backups": (
        {
            "source": "v$session_longops", "opname": "RMAN: aggregate input", "target": "PDBORCL",
            "sofar": "10", "totalwork": "100", "units": "Blocks", "start_time": "2026-11-12 13:00:00",
            "time_remaining": "120", "username": "SYS",
        },
    ),
    "jobs": (
        {
            "source": "dba_scheduler_running_jobs", "owner": "APPUSER", "job_name": "NIGHTLY_STATS",
            "session_id": "88", "elapsed_time": "0:05:12",
        },
    ),
    "availability": (
        {
            "source": "v$instance", "instance_name": "PDBORCL", "status": "OPEN",
            "database_status": "ACTIVE", "host_name": "DBHOST", "startup_time": "2026-11-01 08:00:00",
            "uptime_hours": "270.0",
        },
        {
            "source": "v$database", "name": "ORCL", "open_mode": "READ WRITE", "database_role": "PRIMARY",
            "log_mode": "ARCHIVELOG", "flashback_on": "NO", "protection_mode": "MAXIMUM PERFORMANCE",
            "switchover_status": "NOT ALLOWED",
        },
        {"source": "v$pdbs", "name": "PDBORCL", "open_mode": "READ WRITE", "restricted": "NO"},
        {"source": "v$services", "name": "pdborcl"},
        {"source": "portal", "display_name": "Local Oracle - PDBORCL", "health": "warning"},
    ),
    "errors": (
        {
            "source": "v$diag_alert_ext", "occurred_at": "2026-11-12 01:00:00",
            "message_text": "ORA-00600: internal error code",
        },
    ),
    "tablespace": (
        {
            "source": "tablespace", "tablespace_name": "USERS", "bytes_total": "1073741824",
            "bytes_used": "1030792151", "used_pct": "96.0", "autoextend": "1",
        },
        {
            "source": "datafile_limit", "file_name": "/u01/oradata/users01.dbf", "tablespace_name": "USERS",
            "bytes": "10737418240", "maxbytes": "10737418240", "autoextensible": "YES",
        },
        {
            "source": "v$temp_space_header", "tablespace_name": "TEMP", "bytes_used": "104857600",
            "bytes_free": "943718400", "used_pct": "10.0",
        },
    ),
    "growth": (
        {
            "source": "forecast", "resource": "tablespace:USERS", "status": "forecast",
            "growth_rate_gb_per_day": "0.25", "days_to_exhaustion": "12",
        },
    ),
    "performance": (
        {"source": "v$sys_time_model", "stat_name": "DB CPU", "value": "100"},
        {"source": "v$sys_time_model", "stat_name": "DB time", "value": "400"},
        {"source": "v$osstat", "stat_name": "BUSY_TIME", "value": "25"},
        {"source": "v$osstat", "stat_name": "IDLE_TIME", "value": "75"},
        {"source": "v$session", "status": "ACTIVE", "session_count": "2"},
        {
            "source": "v$system_event", "event": "db file sequential read",
            "total_waits": "1000", "time_waited_micro": "5000000",
        },
        {"source": "v$filestat", "file_no": "4", "phyrds": "100", "phywrts": "5", "readtim": "20", "writetim": "1"},
    ),
    "blocking": (
        {
            "source": "v$session", "sid": "42", "serial_no": "9", "username": "APPUSER", "status": "ACTIVE",
            "event": "enq: TX - row lock contention", "seconds_in_wait": "30", "blocking_session": "18",
            "last_call_et": "4000", "service_name": "pdborcl",
        },
        {"source": "session_count", "username": "APPUSER", "service_name": "pdborcl", "session_count": "6"},
        {
            "source": "v$resource_limit", "resource_name": "sessions", "current_utilization": "40",
            "max_utilization": "80", "limit_value": "200",
        },
        {"source": "alert_deadlock", "occurred_at": "2026-11-12 02:00:00", "message_text": "ORA-00060: deadlock detected"},
    ),
    "sql_workload": (
        {
            "source": "sql_elapsed", "sql_id": "abc123def4567", "parsing_schema_name": "APPUSER",
            "executions": "20", "cpu_time": "12400000", "elapsed_time": "40100000",
            "buffer_gets": "1000", "disk_reads": "4", "sql_text": "SELECT * FROM orders",
        },
        {
            "source": "sql_executions", "sql_id": "bbb123def4567", "parsing_schema_name": "APPUSER",
            "executions": "9000", "cpu_time": "1000", "elapsed_time": "2000",
            "buffer_gets": "10", "disk_reads": "0", "sql_text": "SELECT 1 FROM dual",
        },
        {
            "source": "sql_reads", "sql_id": "ccc123def4567", "parsing_schema_name": "APPUSER",
            "executions": "3", "buffer_gets": "500000", "disk_reads": "80000",
            "sql_text": "SELECT * FROM ledger",
        },
        {"source": "sql_plans", "sql_id": "abc123def4567", "plan_count": "2"},
    ),
    "memory_config": (
        {
            "source": "v$temp_space_header", "tablespace_name": "TEMP", "bytes_used": "943718400",
            "bytes_free": "104857600", "used_pct": "90.0",
        },
        {"source": "v$sysstat", "name": "parse count (total)", "value": "1000"},
        {"source": "v$sysstat", "name": "parse count (hard)", "value": "200"},
        {
            "source": "v$librarycache", "namespace": "SQL AREA", "gets": "100", "gethits": "80",
            "reloads": "4", "invalidations": "1",
        },
        {"source": "v$parameter", "name": "sga_target", "value": "8589934592"},
        {
            "source": "v$resource_limit", "resource_name": "processes", "current_utilization": "150",
            "max_utilization": "180", "limit_value": "200",
        },
    ),
    "recovery": (
        {
            "source": "backup_success", "input_type": "DB FULL", "status": "COMPLETED",
            "start_time": "2026-11-11 01:00:00", "end_time": "2026-11-11 02:00:00", "output_bytes": "1073741824",
        },
        {
            "source": "backup_failed", "input_type": "DB INCR", "status": "FAILED",
            "start_time": "2026-11-12 01:00:00", "end_time": "2026-11-12 01:10:00",
        },
        {
            "source": "v$recovery_file_dest", "name": "/fra", "space_limit": "10737418240",
            "space_used": "7516192768", "used_pct": "70.0",
        },
        {
            "source": "v$archived_log", "sequence_no": "120", "completion_time": "2026-11-12 03:00:00",
            "archived": "YES", "status": "A", "backup_count": "1",
        },
    ),
    "dataguard": (
        {
            "source": "v$database", "name": "ORCL", "open_mode": "READ WRITE", "database_role": "PRIMARY",
            "log_mode": "ARCHIVELOG", "protection_mode": "MAXIMUM PERFORMANCE",
            "switchover_status": "NOT ALLOWED",
        },
        {
            "source": "v$archive_dest_status", "dest_id": "2", "destination": "standby",
            "status": "ERROR", "error": "ORA-16198",
        },
        {"source": "v$dataguard_stats", "name": "apply lag", "value": "+00 00:05:00"},
    ),
    "security": (
        {
            "source": "dba_users", "username": "APPUSER", "account_status": "LOCKED",
            "lock_date": "2026-11-10", "expiry_date": "2026-12-01",
        },
        {"source": "dba_objects", "owner": "APPUSER", "object_type": "PROCEDURE", "object_name": "LOAD_ORDERS"},
        {"source": "dba_role_privs", "grantee": "APPUSER", "granted_role": "DBA"},
    ),
    "failed_jobs": (
        {
            "source": "dba_scheduler_job_run_details", "owner": "APPUSER", "job_name": "NIGHTLY_STATS",
            "status": "FAILED", "log_date": "2026-11-12 01:00:00",
        },
    ),
    "issues": (
        {
            "source": "v$instance", "instance_name": "PDBORCL", "status": "OPEN",
            "database_status": "ACTIVE", "host_name": "DBHOST", "startup_time": "2026-11-01 08:00:00",
            "uptime_hours": "270.0",
        },
        {
            "source": "v$diag_alert_ext", "occurred_at": "2026-11-12 01:00:00",
            "message_text": "ORA-00600: internal error code",
        },
        {
            "source": "tablespace", "tablespace_name": "USERS", "bytes_total": "1073741824",
            "bytes_used": "1030792151", "used_pct": "96.0", "autoextend": "1",
        },
    ),
}
