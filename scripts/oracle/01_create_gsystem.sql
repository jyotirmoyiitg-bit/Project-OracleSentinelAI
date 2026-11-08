-- ============================================================================
-- Oracle Sentinel AI — GSYSTEM Read-Only Monitoring User
-- ============================================================================
-- This script creates the GSYSTEM user with minimal read-only privileges
-- for Oracle Sentinel AI monitoring. Run as SYSDBA on each target database.
-- ============================================================================

-- Create the GSYSTEM user
CREATE USER GSYSTEM IDENTIFIED BY "ChangeMe123!"
    DEFAULT TABLESPACE USERS
    QUOTA UNLIMITED ON USERS;

-- Grant minimal session privilege
GRANT CREATE SESSION TO GSYSTEM;

-- Grant read-only access to required views
GRANT SELECT ON v$instance TO GSYSTEM;
GRANT SELECT ON v$database TO GSYSTEM;
GRANT SELECT ON v$version TO GSYSTEM;
GRANT SELECT ON v$tablespace TO GSYSTEM;
GRANT SELECT ON v$temp_space_header TO GSYSTEM;
GRANT SELECT ON v$recovery_file_dest TO GSYSTEM;
GRANT SELECT ON v$flash_recovery_area_usage TO GSYSTEM;
GRANT SELECT ON v$archive_dest_status TO GSYSTEM;
GRANT SELECT ON v$parameter TO GSYSTEM;
GRANT SELECT ON v$resource_limit TO GSYSTEM;
GRANT SELECT ON v$diag_alert_ext TO GSYSTEM;

-- Grant read-only access to DBA views
GRANT SELECT ON dba_tablespaces TO GSYSTEM;
GRANT SELECT ON dba_data_files TO GSYSTEM;
GRANT SELECT ON dba_free_space TO GSYSTEM;
GRANT SELECT ON dba_temp_files TO GSYSTEM;

-- Grant access to dual
GRANT SELECT ON dual TO GSYSTEM;

-- Verify the user
SELECT username, account_status FROM dba_users WHERE username = 'GSYSTEM';
