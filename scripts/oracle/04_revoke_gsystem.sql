-- ============================================================================
-- Oracle Sentinel AI — GSYSTEM Emergency Revoke
-- ============================================================================
-- Run as SYSDBA to revoke all privileges and lock the GSYSTEM account.
-- ============================================================================

-- Revoke all grants
REVOKE SELECT ON v$instance FROM GSYSTEM;
REVOKE SELECT ON v$database FROM GSYSTEM;
REVOKE SELECT ON v$version FROM GSYSTEM;
REVOKE SELECT ON v$tablespace FROM GSYSTEM;
REVOKE SELECT ON v$temp_space_header FROM GSYSTEM;
REVOKE SELECT ON v$recovery_file_dest FROM GSYSTEM;
REVOKE SELECT ON v$flash_recovery_area_usage FROM GSYSTEM;
REVOKE SELECT ON v$archive_dest_status FROM GSYSTEM;
REVOKE SELECT ON v$parameter FROM GSYSTEM;
REVOKE SELECT ON v$resource_limit FROM GSYSTEM;
REVOKE SELECT ON v$diag_alert_ext FROM GSYSTEM;
REVOKE SELECT ON dba_tablespaces FROM GSYSTEM;
REVOKE SELECT ON dba_data_files FROM GSYSTEM;
REVOKE SELECT ON dba_free_space FROM GSYSTEM;
REVOKE SELECT ON dba_temp_files FROM GSYSTEM;
REVOKE SELECT ON dual FROM GSYSTEM;
REVOKE CREATE SESSION FROM GSYSTEM;

-- Lock the account
ALTER USER GSYSTEM ACCOUNT LOCK;

-- Optionally drop the user
-- DROP USER GSYSTEM CASCADE;
