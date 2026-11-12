"""Fixed read-only SQL for copilot questions. No statement accepts user text."""

import logging
import re
from dataclasses import dataclass

import oracledb

from sentinel.config.settings import Settings

logger = logging.getLogger(__name__)

ACCESS_CODES = frozenset({942, 1031, 904})
_ORA = re.compile(r"ORA-(\d{5})")

DATAFILES_SQL = """
    SELECT file_name, tablespace_name, bytes, autoextensible, status, online_status
    FROM (
        SELECT file_name, tablespace_name, bytes, autoextensible, status, online_status
        FROM dba_data_files
        ORDER BY tablespace_name, file_name
    )
    WHERE ROWNUM <= 40
"""
TEMPFILES_SQL = """
    SELECT file_name, tablespace_name, bytes, autoextensible, status
    FROM (
        SELECT file_name, tablespace_name, bytes, autoextensible, status
        FROM dba_temp_files
        ORDER BY tablespace_name, file_name
    )
    WHERE ROWNUM <= 20
"""
MEMORY_SQL = """
    SELECT pool, name, value
    FROM (
        SELECT 'sga' AS pool, name, bytes AS value
        FROM v$sgainfo
        WHERE name IN (
            'Maximum SGA Size', 'Free SGA Memory Available', 'Buffer Cache Size',
            'Shared Pool Size', 'Large Pool Size', 'Java Pool Size'
        )
        UNION ALL
        SELECT 'pga', name, value
        FROM v$pgastat
        WHERE name IN (
            'aggregate PGA target parameter', 'total PGA allocated',
            'total PGA inuse', 'maximum PGA allocated'
        )
    )
"""
TOP_SQL = """
    SELECT sql_id, parsing_schema_name, executions, cpu_time, elapsed_time, buffer_gets, disk_reads, sql_text
    FROM (
        SELECT sql_id, parsing_schema_name, executions, cpu_time, elapsed_time, buffer_gets, disk_reads,
               SUBSTR(sql_text, 1, 160) AS sql_text
        FROM v$sql
        WHERE sql_text IS NOT NULL
          AND NVL(parsing_schema_name, 'SYS') NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'GSYSTEM')
        ORDER BY cpu_time DESC
    )
    WHERE ROWNUM <= 5
"""
SESSIONS_SQL = """
    SELECT username, status, program, machine, osuser, module, sid, serial_no, logon_time
    FROM (
        SELECT username, status,
               SUBSTR(program, 1, 80) AS program,
               SUBSTR(machine, 1, 80) AS machine,
               SUBSTR(osuser, 1, 40) AS osuser,
               SUBSTR(module, 1, 80) AS module,
               sid, serial# AS serial_no,
               TO_CHAR(logon_time, 'YYYY-MM-DD HH24:MI:SS') AS logon_time
        FROM v$session
        WHERE type = 'USER' AND username IS NOT NULL
        ORDER BY logon_time DESC
    )
    WHERE ROWNUM <= 30
"""
BACKUP_LONGOPS_SQL = """
    SELECT opname, target, sofar, totalwork, units, start_time, time_remaining, username
    FROM (
        SELECT SUBSTR(opname, 1, 80) AS opname, SUBSTR(target, 1, 80) AS target,
               sofar, totalwork, units,
               TO_CHAR(start_time, 'YYYY-MM-DD HH24:MI:SS') AS start_time,
               time_remaining, username
        FROM v$session_longops
        WHERE totalwork > 0 AND sofar < totalwork
          AND (UPPER(opname) LIKE '%RMAN%' OR UPPER(opname) LIKE '%BACKUP%')
        ORDER BY start_time DESC
    )
    WHERE ROWNUM <= 10
"""
BACKUP_JOBS_SQL = """
    SELECT input_type, status, start_time, end_time, output_bytes
    FROM (
        SELECT input_type, status,
               TO_CHAR(start_time, 'YYYY-MM-DD HH24:MI:SS') AS start_time,
               TO_CHAR(end_time, 'YYYY-MM-DD HH24:MI:SS') AS end_time,
               output_bytes
        FROM v$rman_backup_job_details
        ORDER BY start_time DESC
    )
    WHERE ROWNUM <= 5
"""
SCHEDULER_SQL = """
    SELECT owner, job_name, session_id, elapsed_time
    FROM (
        SELECT owner, job_name, session_id, elapsed_time
        FROM dba_scheduler_running_jobs
        ORDER BY owner, job_name
    )
    WHERE ROWNUM <= 20
"""
DBMS_JOBS_SQL = """
    SELECT job, sid, this_date
    FROM (
        SELECT job, sid, TO_CHAR(this_date, 'YYYY-MM-DD HH24:MI:SS') AS this_date
        FROM dba_jobs_running
        ORDER BY job
    )
    WHERE ROWNUM <= 20
"""


class ActivityAccessError(Exception):
    def __init__(self, object_name: str) -> None:
        super().__init__(object_name)
        self.object_name = object_name


class ActivityUnavailable(Exception):
    def __init__(self, code: int | None) -> None:
        super().__init__("unavailable" if code is None else str(code))
        self.code = code


@dataclass(frozen=True)
class FixedQuery:
    source: str
    sql: str
    object_name: str
    required: bool = True


CATALOG: dict[str, tuple[FixedQuery, ...]] = {
    "datafiles": (
        FixedQuery("dba_data_files", DATAFILES_SQL, "SYS.DBA_DATA_FILES"),
        FixedQuery("dba_temp_files", TEMPFILES_SQL, "SYS.DBA_TEMP_FILES", required=False),
    ),
    "memory": (FixedQuery("v$sgainfo", MEMORY_SQL, "SYS.V_$SGAINFO"),),
    "top_sql": (FixedQuery("v$sql", TOP_SQL, "SYS.V_$SQL"),),
    "sessions": (FixedQuery("v$session", SESSIONS_SQL, "SYS.V_$SESSION"),),
    "backups": (
        FixedQuery("v$session_longops", BACKUP_LONGOPS_SQL, "SYS.V_$SESSION_LONGOPS"),
        FixedQuery("v$rman_backup_job_details", BACKUP_JOBS_SQL, "SYS.V_$RMAN_BACKUP_JOB_DETAILS", required=False),
    ),
    "jobs": (
        FixedQuery("dba_scheduler_running_jobs", SCHEDULER_SQL, "SYS.DBA_SCHEDULER_RUNNING_JOBS"),
        FixedQuery("dba_jobs_running", DBMS_JOBS_SQL, "SYS.DBA_JOBS_RUNNING", required=False),
    ),
}


def _merge_readings() -> None:
    from sentinel.adapters.oracle.readings import reading_queries

    CATALOG.update(reading_queries())


_merge_readings()


def oracle_code(exc: BaseException) -> int | None:
    error = exc.args[0] if exc.args else None
    code = getattr(error, "code", None)
    if isinstance(code, int):
        return code
    match = _ORA.search(str(exc))
    return int(match.group(1)) if match else None


def collect_rows(connection: oracledb.Connection, kind: str) -> tuple[list[dict[str, str]], str]:
    """Run the fixed statements for one question. User text is never bound into SQL."""
    notes: list[str] = []
    rows: list[dict[str, str]] = []
    for query in CATALOG[kind]:
        cursor = connection.cursor()
        try:
            cursor.execute(query.sql)
            columns = [str(column[0]).lower() for column in cursor.description or ()]
            for raw in cursor.fetchall():
                item = {column: _cell(value) for column, value in zip(columns, raw, strict=True)}
                item["source"] = query.source
                rows.append(item)
        except oracledb.Error as exc:
            code = oracle_code(exc)
            if code in ACCESS_CODES:
                if query.required:
                    raise ActivityAccessError(query.object_name) from None
                notes.append(f"{query.object_name} is not granted to the monitoring account.")
                continue
            raise ActivityUnavailable(code) from None
        finally:
            cursor.close()
    logger.info("activity kind=%s rows=%s", kind, len(rows))
    return rows, " ".join(notes)


async def read_rows(settings: Settings, alias: str, kind: str) -> tuple[list[dict[str, str]], str]:
    from sentinel.adapters.oracle.connection import OracleConnectionManager

    manager = OracleConnectionManager(settings)
    try:
        async with manager.connection_context(alias) as connection:
            return collect_rows(connection, kind)
    except (ActivityAccessError, ActivityUnavailable):
        raise
    except oracledb.Error as exc:
        raise ActivityUnavailable(oracle_code(exc)) from None
    except Exception:  # noqa: BLE001 -- driver and path errors must not reach the operator
        logger.warning("activity read failed kind=%s", kind)
        raise ActivityUnavailable(None) from None


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    text = str(value).replace("\n", " ").strip()
    return text[:200]
