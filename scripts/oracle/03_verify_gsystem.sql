-- ============================================================================
-- Oracle Sentinel AI — GSYSTEM Verification Script
-- ============================================================================
-- Run as GSYSTEM to verify access to required views.
-- ============================================================================

-- Check version
SELECT banner FROM v$version WHERE rownum = 1;

-- Check instance status
SELECT instance_name, status, database_status, version FROM v$instance;

-- Check database info
SELECT name, open_mode, database_role, log_mode, flashback_on FROM v$database;

-- Check tablespace access
SELECT tablespace_name, status, contents FROM dba_tablespaces;

-- Check data files
SELECT tablespace_name, SUM(bytes) AS total_bytes
FROM dba_data_files
GROUP BY tablespace_name;

-- Check free space
SELECT tablespace_name, SUM(bytes) AS free_bytes
FROM dba_free_space
GROUP BY tablespace_name;

-- Check FRA
SELECT name, space_limit, space_used, space_reclaimable, number_of_files
FROM v$recovery_file_dest;

-- Check archive dest
SELECT destination, status, target FROM v$archive_dest_status WHERE status IS NOT NULL;

-- Check parameters
SELECT name, value FROM v$parameter
WHERE name IN ('db_create_file_dest', 'db_recovery_file_dest', 'db_recovery_file_dest_size');

-- Check resource limits
SELECT resource_name, current_utilization, max_utilization, limit_value
FROM v$resource_limit;

-- Check alert log access
SELECT COUNT(*) AS alert_count FROM v$diag_alert_ext
WHERE originating_timestamp > SYSDATE - 1;

-- Check CDB/PDB context
SELECT SYS_CONTEXT('USERENV', 'CON_NAME') AS container_name FROM dual;
SELECT SYS_CONTEXT('USERENV', 'CON_ID') AS container_id FROM dual;
