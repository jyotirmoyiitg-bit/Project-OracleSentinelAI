-- Run as GSYSTEM connected through the SAME service/TNS alias the portal uses.
-- Every query is bounded; an empty result still proves view/column access.
WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET PAGESIZE 100
SET LINESIZE 200

BEGIN
    IF SYS_CONTEXT('USERENV', 'SESSION_USER') <> 'GSYSTEM' THEN
        RAISE_APPLICATION_ERROR(-20001, 'Run verification as GSYSTEM, not SYS/SYSTEM.');
    END IF;
    IF SYS_CONTEXT('USERENV', 'CON_NAME') IN ('CDB$ROOT', 'PDB$SEED') THEN
        RAISE_APPLICATION_ERROR(-20002, 'Connect GSYSTEM to the target PDB, not the root/seed.');
    END IF;
END;
/

SELECT USER AS monitoring_user,
       SYS_CONTEXT('USERENV', 'CON_NAME') AS container_name,
       SYS_CONTEXT('USERENV', 'CON_ID') AS container_id
FROM dual;

SELECT banner FROM v$version WHERE rownum <= 1;

SELECT instance_name, status, database_status, host_name, version, startup_time
FROM v$instance WHERE rownum <= 1;

SELECT name, open_mode, database_role, log_mode, flashback_on, platform_name, created,
       protection_mode, switchover_status
FROM v$database WHERE rownum <= 1;

SELECT tablespace_name, status, contents, logging, extent_management
FROM dba_tablespaces WHERE rownum <= 1;

SELECT tablespace_name, bytes, maxbytes, autoextensible
FROM dba_data_files WHERE rownum <= 1;

SELECT tablespace_name, bytes FROM dba_free_space WHERE rownum <= 1;

SELECT tablespace_name, bytes_used, bytes_free
FROM v$temp_space_header WHERE rownum <= 1;

SELECT name, space_limit, space_used, space_reclaimable, number_of_files
FROM v$recovery_file_dest WHERE rownum <= 1;

SELECT file_type, percent_space_used, percent_space_reclaimable, number_of_files
FROM v$flash_recovery_area_usage WHERE rownum <= 1;

SELECT destination, status, error FROM v$archive_dest_status WHERE rownum <= 1;

SELECT name, value, issys_modifiable FROM v$parameter
WHERE name IN ('db_create_file_dest', 'db_create_online_log_dest_1',
               'db_recovery_file_dest', 'db_recovery_file_dest_size',
               'log_archive_dest_1', 'audit_trail', 'control_file_record_keep_time')
  AND rownum <= 7;

SELECT resource_name, current_utilization, max_utilization, initial_allocation, limit_value
FROM v$resource_limit WHERE rownum <= 1;

SELECT file_name, tablespace_name, bytes, autoextensible, status
FROM dba_temp_files WHERE rownum <= 1;

SELECT name, bytes FROM v$sgainfo WHERE rownum <= 1;

SELECT name, value FROM v$pgastat WHERE rownum <= 1;

SELECT sql_id FROM v$sql WHERE rownum <= 1;

SELECT username, status, sid FROM v$session WHERE type = 'USER' AND rownum <= 1;

SELECT opname, sofar, totalwork FROM v$session_longops WHERE rownum <= 1;

SELECT input_type, status, start_time FROM v$rman_backup_job_details WHERE rownum <= 1;

SELECT owner, job_name, session_id FROM dba_scheduler_running_jobs WHERE rownum <= 1;

SELECT job, sid FROM dba_jobs_running WHERE rownum <= 1;

SELECT name, open_mode, restricted FROM v$pdbs WHERE rownum <= 1;

SELECT name FROM v$services WHERE rownum <= 1;

SELECT stat_name, value FROM v$osstat WHERE rownum <= 1;

SELECT stat_name, value FROM v$sys_time_model WHERE rownum <= 1;

SELECT event, total_waits, time_waited_micro FROM v$system_event WHERE rownum <= 1;

SELECT file# AS file_no, phyrds, phywrts, readtim, writetim
FROM v$filestat WHERE rownum <= 1;

SELECT name, value FROM v$sysstat WHERE rownum <= 1;

SELECT namespace, gets, gethits, reloads, invalidations
FROM v$librarycache WHERE rownum <= 1;

SELECT sequence# AS sequence_no, completion_time, archived, status, backup_count
FROM v$archived_log WHERE rownum <= 1;

SELECT name, value FROM v$dataguard_stats WHERE rownum <= 1;

SELECT username, account_status, lock_date, expiry_date
FROM dba_users WHERE rownum <= 1;

SELECT owner, object_type, object_name, status FROM dba_objects WHERE rownum <= 1;

SELECT grantee, granted_role FROM dba_role_privs WHERE rownum <= 1;

SELECT owner, job_name, status, log_date
FROM dba_scheduler_job_run_details WHERE rownum <= 1;

-- Check the actual alert-poller columns without dumping message text/client details.
SELECT record_id, originating_timestamp, message_type,
       LENGTH(message_text) AS message_length,
       LENGTH(module) AS module_length, LENGTH(client_id) AS client_id_length
FROM v$diag_alert_ext
WHERE originating_timestamp > SYSTIMESTAMP - INTERVAL '1' DAY
  AND rownum <= 1;

PROMPT PASS: required monitoring views and columns are accessible in this container.
PROMPT Empty FRA/archive/alert rows do not imply that these features are configured or populated.
