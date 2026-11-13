"""Additional fixed readings. No statement accepts operator text."""

from sentinel.adapters.oracle.activity import FixedQuery

INSTANCE_SQL = """
    SELECT instance_name, status, database_status, host_name,
           TO_CHAR(startup_time, 'YYYY-MM-DD HH24:MI:SS') AS startup_time,
           ROUND((SYSDATE - startup_time) * 24, 1) AS uptime_hours
    FROM v$instance
"""
DATABASE_SQL = """
    SELECT name, open_mode, database_role, log_mode, flashback_on,
           protection_mode, switchover_status
    FROM v$database
"""
PDB_SQL = """
    SELECT name, open_mode, restricted
    FROM (SELECT name, open_mode, restricted FROM v$pdbs ORDER BY name)
    WHERE ROWNUM <= 20
"""
SERVICE_SQL = """
    SELECT name
    FROM (SELECT name FROM v$services ORDER BY name)
    WHERE ROWNUM <= 30
"""
# V$DIAG_ALERT_EXT also exposes listener and client traces. Those traces can name
# another database, such as a failed connection to FREEPDB1. Keep the database alert only.
_DATABASE_ALERT_FILE = """
    LOWER(filename) LIKE '%rdbms%alert%log.xml'
    AND LOWER(filename) NOT LIKE '%tnslsnr%'
    AND LOWER(filename) NOT LIKE '%clients%'
"""
OUTAGE_SQL = f"""
    SELECT occurred_at, message_text
    FROM (
        SELECT TO_CHAR(originating_timestamp, 'YYYY-MM-DD HH24:MI:SS') AS occurred_at,
               SUBSTR(message_text, 1, 160) AS message_text
        FROM v$diag_alert_ext
        WHERE originating_timestamp > SYSTIMESTAMP - INTERVAL '7' DAY
          AND {_DATABASE_ALERT_FILE}
          AND (
              UPPER(message_text) LIKE '%SHUTDOWN%'
              OR UPPER(message_text) LIKE '%INSTANCE TERMINATED%'
              OR UPPER(message_text) LIKE '%STARTING ORACLE%'
          )
        ORDER BY originating_timestamp DESC
    )
    WHERE ROWNUM <= 10
"""
LATEST_ALERT_SQL = f"""
    SELECT occurred_at, message_text
    FROM (
        SELECT TO_CHAR(originating_timestamp, 'YYYY-MM-DD HH24:MI:SS') AS occurred_at,
               SUBSTR(message_text, 1, 180) AS message_text
        FROM v$diag_alert_ext
        WHERE originating_timestamp > SYSTIMESTAMP - INTERVAL '7' DAY
          AND message_text IS NOT NULL
          AND {_DATABASE_ALERT_FILE}
        ORDER BY originating_timestamp DESC
    )
    WHERE ROWNUM <= 30
"""
CRITICAL_SQL = f"""
    SELECT occurred_at, message_text
    FROM (
        SELECT TO_CHAR(originating_timestamp, 'YYYY-MM-DD HH24:MI:SS') AS occurred_at,
               SUBSTR(message_text, 1, 180) AS message_text
        FROM v$diag_alert_ext
        WHERE originating_timestamp > SYSTIMESTAMP - INTERVAL '1' DAY
          AND {_DATABASE_ALERT_FILE}
          AND (
              message_text LIKE '%ORA-00600%' OR message_text LIKE '%ORA-07445%'
              OR message_text LIKE '%ORA-04031%' OR message_text LIKE '%ORA-04030%'
              OR message_text LIKE '%ORA-00257%' OR message_text LIKE '%ORA-01653%'
              OR message_text LIKE '%ORA-01654%' OR message_text LIKE '%ORA-01578%'
              OR message_text LIKE '%ORA-19809%' OR message_text LIKE '%ORA-19815%'
              OR message_text LIKE '%ORA-00255%' OR message_text LIKE '%ORA-16038%'
              OR message_text LIKE '%ORA-16014%'               OR message_text LIKE '%ORA-00060%'
              OR (
                  UPPER(message_text) LIKE '%FATAL%'
                  AND UPPER(message_text) NOT LIKE '%FATAL NI%'
              )
          )
        ORDER BY originating_timestamp DESC
    )
    WHERE ROWNUM <= 20
"""
ORA_ERROR_SQL = f"""
    SELECT occurred_at, message_text
    FROM (
        SELECT TO_CHAR(originating_timestamp, 'YYYY-MM-DD HH24:MI:SS') AS occurred_at,
               SUBSTR(message_text, 1, 180) AS message_text
        FROM v$diag_alert_ext
        WHERE originating_timestamp > SYSTIMESTAMP - INTERVAL '1' DAY
          AND {_DATABASE_ALERT_FILE}
          AND message_text LIKE '%ORA-%'
        ORDER BY originating_timestamp DESC
    )
    WHERE ROWNUM <= 30
"""
TABLESPACE_SQL = """
    SELECT tablespace_name, bytes_total, bytes_used, used_pct, autoextend
    FROM (
        SELECT files.tablespace_name, files.bytes_total,
               files.bytes_total - NVL(free_space.bytes_free, 0) AS bytes_used,
               CASE WHEN files.bytes_total > 0
                    THEN ROUND((files.bytes_total - NVL(free_space.bytes_free, 0)) * 100 / files.bytes_total, 1)
                    ELSE 0 END AS used_pct,
               files.autoextend
        FROM (
            SELECT tablespace_name, SUM(bytes) AS bytes_total,
                   MAX(CASE WHEN autoextensible = 'YES' THEN 1 ELSE 0 END) AS autoextend
            FROM dba_data_files
            GROUP BY tablespace_name
        ) files
        LEFT JOIN (
            SELECT tablespace_name, SUM(bytes) AS bytes_free
            FROM dba_free_space
            GROUP BY tablespace_name
        ) free_space ON files.tablespace_name = free_space.tablespace_name
        ORDER BY used_pct DESC, files.tablespace_name
    )
    WHERE ROWNUM <= 30
"""
DATAFILE_LIMIT_SQL = """
    SELECT file_name, tablespace_name, bytes, maxbytes, autoextensible
    FROM (
        SELECT file_name, tablespace_name, bytes, maxbytes, autoextensible
        FROM dba_data_files
        WHERE autoextensible = 'YES' AND maxbytes > 0 AND bytes >= maxbytes * 0.90
        ORDER BY tablespace_name, file_name
    )
    WHERE ROWNUM <= 20
"""
TEMP_SQL = """
    SELECT tablespace_name, bytes_used, bytes_free,
           ROUND(bytes_used * 100 / NULLIF(bytes_used + bytes_free, 0), 1) AS used_pct
    FROM v$temp_space_header
    WHERE ROWNUM <= 10
"""
OSSTAT_SQL = """
    SELECT stat_name, value
    FROM v$osstat
    WHERE stat_name IN ('NUM_CPUS', 'BUSY_TIME', 'IDLE_TIME', 'USER_TIME', 'SYS_TIME', 'IOWAIT_TIME')
"""
TIME_MODEL_SQL = """
    SELECT stat_name, value
    FROM v$sys_time_model
    WHERE stat_name IN ('DB CPU', 'DB time')
"""
ACTIVE_SQL = """
    SELECT status, COUNT(*) AS session_count
    FROM v$session
    WHERE type = 'USER'
    GROUP BY status
"""
WAIT_SQL = """
    SELECT event, total_waits, time_waited_micro
    FROM (
        SELECT event, total_waits, time_waited_micro
        FROM v$system_event
        WHERE wait_class <> 'Idle'
        ORDER BY time_waited_micro DESC
    )
    WHERE ROWNUM <= 8
"""
IO_SQL = """
    SELECT file_no, phyrds, phywrts, readtim, writetim
    FROM (
        SELECT file# AS file_no, phyrds, phywrts, readtim, writetim
        FROM v$filestat
        WHERE NVL(phyrds, 0) + NVL(phywrts, 0) > 0
        ORDER BY NVL(readtim, 0) + NVL(writetim, 0) DESC
    )
    WHERE ROWNUM <= 8
"""
BLOCKING_SQL = """
    SELECT sid, serial_no, username, status, event, seconds_in_wait, blocking_session,
           last_call_et, service_name
    FROM (
        SELECT sid, serial# AS serial_no, username, status,
               SUBSTR(event, 1, 60) AS event, seconds_in_wait, blocking_session,
               last_call_et, SUBSTR(service_name, 1, 40) AS service_name
        FROM v$session
        WHERE type = 'USER' AND username IS NOT NULL
          AND (blocking_session IS NOT NULL OR last_call_et > 600)
        ORDER BY last_call_et DESC
    )
    WHERE ROWNUM <= 20
"""
SESSION_COUNT_SQL = """
    SELECT username, service_name, session_count
    FROM (
        SELECT username, SUBSTR(service_name, 1, 40) AS service_name, COUNT(*) AS session_count
        FROM v$session
        WHERE type = 'USER' AND username IS NOT NULL
        GROUP BY username, service_name
        ORDER BY COUNT(*) DESC, username
    )
    WHERE ROWNUM <= 15
"""
LIMIT_SQL = """
    SELECT resource_name, current_utilization, max_utilization, limit_value
    FROM v$resource_limit
    WHERE resource_name IN ('processes', 'sessions', 'transactions')
"""
DEADLOCK_SQL = f"""
    SELECT occurred_at, message_text
    FROM (
        SELECT TO_CHAR(originating_timestamp, 'YYYY-MM-DD HH24:MI:SS') AS occurred_at,
               SUBSTR(message_text, 1, 160) AS message_text
        FROM v$diag_alert_ext
        WHERE originating_timestamp > SYSTIMESTAMP - INTERVAL '1' DAY
          AND {_DATABASE_ALERT_FILE}
          AND message_text LIKE '%ORA-00060%'
        ORDER BY originating_timestamp DESC
    )
    WHERE ROWNUM <= 5
"""
SQL_ELAPSED = """
    SELECT sql_id, parsing_schema_name, executions, cpu_time, elapsed_time, buffer_gets, disk_reads, sql_text
    FROM (
        SELECT sql_id, parsing_schema_name, executions, cpu_time, elapsed_time, buffer_gets, disk_reads,
               SUBSTR(sql_text, 1, 120) AS sql_text
        FROM v$sql
        WHERE sql_text IS NOT NULL
          AND NVL(parsing_schema_name, 'SYS') NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'GSYSTEM')
        ORDER BY elapsed_time DESC
    )
    WHERE ROWNUM <= 5
"""
SQL_EXECUTIONS = """
    SELECT sql_id, parsing_schema_name, executions, cpu_time, elapsed_time, buffer_gets, disk_reads, sql_text
    FROM (
        SELECT sql_id, parsing_schema_name, executions, cpu_time, elapsed_time, buffer_gets, disk_reads,
               SUBSTR(sql_text, 1, 120) AS sql_text
        FROM v$sql
        WHERE sql_text IS NOT NULL
          AND NVL(parsing_schema_name, 'SYS') NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'GSYSTEM')
        ORDER BY executions DESC
    )
    WHERE ROWNUM <= 5
"""
SQL_READS = """
    SELECT sql_id, parsing_schema_name, executions, buffer_gets, disk_reads, sql_text
    FROM (
        SELECT sql_id, parsing_schema_name, executions, buffer_gets, disk_reads,
               SUBSTR(sql_text, 1, 120) AS sql_text
        FROM v$sql
        WHERE sql_text IS NOT NULL
          AND NVL(parsing_schema_name, 'SYS') NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'GSYSTEM')
        ORDER BY disk_reads DESC, buffer_gets DESC
    )
    WHERE ROWNUM <= 5
"""
SQL_PLANS = """
    SELECT sql_id, plan_count
    FROM (
        SELECT sql_id, COUNT(DISTINCT plan_hash_value) AS plan_count
        FROM v$sql
        WHERE sql_text IS NOT NULL
          AND NVL(parsing_schema_name, 'SYS') NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'GSYSTEM')
        GROUP BY sql_id
        HAVING COUNT(DISTINCT plan_hash_value) > 1
        ORDER BY COUNT(DISTINCT plan_hash_value) DESC
    )
    WHERE ROWNUM <= 5
"""
PARSE_SQL = """
    SELECT name, value
    FROM v$sysstat
    WHERE name IN (
        'parse count (total)', 'parse count (hard)', 'parse count (failures)',
        'session cursor cache hits'
    )
"""
LIBRARY_SQL = """
    SELECT namespace, gets, gethits, reloads, invalidations
    FROM v$librarycache
    WHERE namespace IN ('SQL AREA', 'TABLE/PROCEDURE', 'BODY', 'TRIGGER')
"""
PARAMETER_SQL = """
    SELECT name, value
    FROM v$parameter
    WHERE name IN (
        'sga_target', 'sga_max_size', 'pga_aggregate_target', 'memory_target',
        'shared_pool_size', 'db_cache_size', 'processes', 'sessions', 'open_cursors',
        'db_recovery_file_dest_size'
    )
    ORDER BY name
"""
BACKUP_SUCCESS_SQL = """
    SELECT input_type, status, start_time, end_time, output_bytes
    FROM (
        SELECT input_type, status,
               TO_CHAR(start_time, 'YYYY-MM-DD HH24:MI:SS') AS start_time,
               TO_CHAR(end_time, 'YYYY-MM-DD HH24:MI:SS') AS end_time,
               output_bytes
        FROM v$rman_backup_job_details
        WHERE status LIKE 'COMPLETED%'
        ORDER BY start_time DESC
    )
    WHERE ROWNUM <= 5
"""
BACKUP_FAILED_SQL = """
    SELECT input_type, status, start_time, end_time
    FROM (
        SELECT input_type, status,
               TO_CHAR(start_time, 'YYYY-MM-DD HH24:MI:SS') AS start_time,
               TO_CHAR(end_time, 'YYYY-MM-DD HH24:MI:SS') AS end_time
        FROM v$rman_backup_job_details
        WHERE start_time > SYSDATE - 7
          AND status NOT LIKE 'COMPLETED%'
          AND status NOT LIKE 'RUNNING%'
        ORDER BY start_time DESC
    )
    WHERE ROWNUM <= 10
"""
FRA_SQL = """
    SELECT name, space_limit, space_used,
           ROUND(space_used * 100 / NULLIF(space_limit, 0), 1) AS used_pct
    FROM v$recovery_file_dest
"""
ARCHIVE_SQL = """
    SELECT sequence_no, completion_time, archived, status, backup_count
    FROM (
        SELECT sequence# AS sequence_no,
               TO_CHAR(completion_time, 'YYYY-MM-DD HH24:MI:SS') AS completion_time,
               archived, status, backup_count
        FROM v$archived_log
        WHERE completion_time > SYSDATE - 2
        ORDER BY completion_time DESC
    )
    WHERE ROWNUM <= 15
"""
DEST_SQL = """
    SELECT dest_id, destination, status, error
    FROM (
        SELECT dest_id, SUBSTR(destination, 1, 80) AS destination, status,
               SUBSTR(error, 1, 120) AS error
        FROM v$archive_dest_status
        WHERE dest_id > 0 AND destination IS NOT NULL
        ORDER BY dest_id
    )
    WHERE ROWNUM <= 10
"""
GUARD_STATS_SQL = """
    SELECT name, value
    FROM v$dataguard_stats
"""
USER_LIST_SQL = """
    SELECT username, account_status
    FROM (
        SELECT username, account_status
        FROM dba_users
        ORDER BY username
    )
    WHERE ROWNUM <= 80
"""
ACCOUNT_SQL = """
    SELECT username, account_status, lock_date, expiry_date
    FROM (
        SELECT username, account_status,
               TO_CHAR(lock_date, 'YYYY-MM-DD') AS lock_date,
               TO_CHAR(expiry_date, 'YYYY-MM-DD') AS expiry_date
        FROM dba_users
        WHERE account_status <> 'OPEN'
          AND username NOT IN (
              'SYS', 'SYSTEM', 'DBSNMP', 'GSYSTEM', 'OUTLN', 'XS$NULL',
              'GSMADMIN_INTERNAL', 'APPQOSSYS', 'WMSYS', 'ORACLE_OCM'
          )
        ORDER BY username
    )
    WHERE ROWNUM <= 30
"""
INVALID_SQL = """
    SELECT owner, object_type, object_name
    FROM (
        SELECT owner, object_type, object_name
        FROM dba_objects
        WHERE status = 'INVALID'
          AND owner NOT IN ('SYS', 'SYSTEM', 'XDB', 'WMSYS', 'CTXSYS', 'MDSYS', 'OLAPSYS', 'ORDSYS')
        ORDER BY owner, object_type, object_name
    )
    WHERE ROWNUM <= 30
"""
ROLE_SQL = """
    SELECT grantee, granted_role
    FROM (
        SELECT grantee, granted_role
        FROM dba_role_privs
        WHERE granted_role IN (
            'DBA', 'IMP_FULL_DATABASE', 'EXP_FULL_DATABASE',
            'DATAPUMP_IMP_FULL_DATABASE', 'DATAPUMP_EXP_FULL_DATABASE'
        )
          AND grantee NOT IN ('SYS', 'SYSTEM', 'DBA')
        ORDER BY grantee, granted_role
    )
    WHERE ROWNUM <= 30
"""
FAILED_JOB_SQL = """
    SELECT owner, job_name, status, log_date
    FROM (
        SELECT owner, job_name, status,
               TO_CHAR(log_date, 'YYYY-MM-DD HH24:MI:SS') AS log_date
        FROM dba_scheduler_job_run_details
        WHERE status <> 'SUCCEEDED' AND log_date > SYSTIMESTAMP - INTERVAL '2' DAY
        ORDER BY log_date DESC
    )
    WHERE ROWNUM <= 20
"""


def reading_queries() -> dict[str, tuple[FixedQuery, ...]]:
    """Fixed statements for the operator question groups."""
    availability = (
        FixedQuery("v$instance", INSTANCE_SQL, "SYS.V_$INSTANCE"),
        FixedQuery("v$database", DATABASE_SQL, "SYS.V_$DATABASE"),
        FixedQuery("v$pdbs", PDB_SQL, "SYS.V_$PDBS", required=False),
        FixedQuery("v$services", SERVICE_SQL, "SYS.V_$SERVICES", required=False),
        FixedQuery("alert_outage", OUTAGE_SQL, "SYS.V_$DIAG_ALERT_EXT", required=False),
    )
    errors = (FixedQuery("v$diag_alert_ext", CRITICAL_SQL, "SYS.V_$DIAG_ALERT_EXT"),)
    ora_errors = (FixedQuery("v$diag_alert_ext", ORA_ERROR_SQL, "SYS.V_$DIAG_ALERT_EXT"),)
    alert_log = (FixedQuery("v$diag_alert_ext", LATEST_ALERT_SQL, "SYS.V_$DIAG_ALERT_EXT"),)
    tablespace = (
        FixedQuery("tablespace", TABLESPACE_SQL, "SYS.DBA_DATA_FILES"),
        FixedQuery("datafile_limit", DATAFILE_LIMIT_SQL, "SYS.DBA_DATA_FILES"),
        FixedQuery("v$temp_space_header", TEMP_SQL, "SYS.V_$TEMP_SPACE_HEADER", required=False),
    )
    performance = (
        FixedQuery("v$sys_time_model", TIME_MODEL_SQL, "SYS.V_$SYS_TIME_MODEL"),
        FixedQuery("v$osstat", OSSTAT_SQL, "SYS.V_$OSSTAT", required=False),
        FixedQuery("v$session", ACTIVE_SQL, "SYS.V_$SESSION"),
        FixedQuery("v$system_event", WAIT_SQL, "SYS.V_$SYSTEM_EVENT"),
        FixedQuery("v$filestat", IO_SQL, "SYS.V_$FILESTAT", required=False),
    )
    blocking = (
        FixedQuery("v$session", BLOCKING_SQL, "SYS.V_$SESSION"),
        FixedQuery("session_count", SESSION_COUNT_SQL, "SYS.V_$SESSION"),
        FixedQuery("v$resource_limit", LIMIT_SQL, "SYS.V_$RESOURCE_LIMIT"),
        FixedQuery("alert_deadlock", DEADLOCK_SQL, "SYS.V_$DIAG_ALERT_EXT", required=False),
    )
    sql_workload = (
        FixedQuery("sql_elapsed", SQL_ELAPSED, "SYS.V_$SQL"),
        FixedQuery("sql_executions", SQL_EXECUTIONS, "SYS.V_$SQL"),
        FixedQuery("sql_reads", SQL_READS, "SYS.V_$SQL"),
        FixedQuery("sql_plans", SQL_PLANS, "SYS.V_$SQL"),
    )
    memory_config = (
        FixedQuery("v$temp_space_header", TEMP_SQL, "SYS.V_$TEMP_SPACE_HEADER"),
        FixedQuery("v$sysstat", PARSE_SQL, "SYS.V_$SYSSTAT"),
        FixedQuery("v$librarycache", LIBRARY_SQL, "SYS.V_$LIBRARYCACHE", required=False),
        FixedQuery("v$parameter", PARAMETER_SQL, "SYS.V_$PARAMETER"),
        FixedQuery("v$resource_limit", LIMIT_SQL, "SYS.V_$RESOURCE_LIMIT"),
    )
    recovery = (
        FixedQuery("backup_success", BACKUP_SUCCESS_SQL, "SYS.V_$RMAN_BACKUP_JOB_DETAILS"),
        FixedQuery("backup_failed", BACKUP_FAILED_SQL, "SYS.V_$RMAN_BACKUP_JOB_DETAILS"),
        FixedQuery("v$recovery_file_dest", FRA_SQL, "SYS.V_$RECOVERY_FILE_DEST", required=False),
        FixedQuery("v$archived_log", ARCHIVE_SQL, "SYS.V_$ARCHIVED_LOG", required=False),
    )
    dataguard = (
        FixedQuery("v$database", DATABASE_SQL, "SYS.V_$DATABASE"),
        FixedQuery("v$archive_dest_status", DEST_SQL, "SYS.V_$ARCHIVE_DEST_STATUS"),
        FixedQuery("v$dataguard_stats", GUARD_STATS_SQL, "SYS.V_$DATAGUARD_STATS", required=False),
    )
    database_users = (FixedQuery("dba_users", USER_LIST_SQL, "SYS.DBA_USERS"),)
    security = (
        FixedQuery("dba_users", ACCOUNT_SQL, "SYS.DBA_USERS"),
        FixedQuery("dba_objects", INVALID_SQL, "SYS.DBA_OBJECTS"),
        FixedQuery("dba_role_privs", ROLE_SQL, "SYS.DBA_ROLE_PRIVS", required=False),
    )
    failed_jobs = (
        FixedQuery("dba_scheduler_job_run_details", FAILED_JOB_SQL, "SYS.DBA_SCHEDULER_JOB_RUN_DETAILS"),
    )
    issues = (
        FixedQuery("v$instance", INSTANCE_SQL, "SYS.V_$INSTANCE"),
        FixedQuery("v$database", DATABASE_SQL, "SYS.V_$DATABASE"),
        FixedQuery("v$diag_alert_ext", CRITICAL_SQL, "SYS.V_$DIAG_ALERT_EXT", required=False),
        FixedQuery("tablespace", TABLESPACE_SQL, "SYS.DBA_DATA_FILES"),
        FixedQuery("v$session", BLOCKING_SQL, "SYS.V_$SESSION"),
        FixedQuery("v$resource_limit", LIMIT_SQL, "SYS.V_$RESOURCE_LIMIT", required=False),
    )
    return {
        "availability": availability,
        "errors": errors,
        "ora_errors": ora_errors,
        "alert_log": alert_log,
        "tablespace": tablespace,
        "performance": performance,
        "blocking": blocking,
        "sql_workload": sql_workload,
        "memory_config": memory_config,
        "recovery": recovery,
        "dataguard": dataguard,
        "database_users": database_users,
        "security": security,
        "failed_jobs": failed_jobs,
        "issues": issues,
    }
