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
<<<<<<< HEAD
-- NOTE: v$ views are PUBLIC synonyms for SYS.V_$ fixed tables.
-- Direct GRANT on v$ fails with ORA-02030. Grant on SYS.V_$ instead.
GRANT SELECT ON SYS.V_$INSTANCE TO GSYSTEM;
GRANT SELECT ON SYS.V_$DATABASE TO GSYSTEM;
GRANT SELECT ON SYS.V_$VERSION TO GSYSTEM;
GRANT SELECT ON SYS.V_$TABLESPACE TO GSYSTEM;
GRANT SELECT ON SYS.V_$TEMP_SPACE_HEADER TO GSYSTEM;
GRANT SELECT ON SYS.V_$RECOVERY_FILE_DEST TO GSYSTEM;
GRANT SELECT ON SYS.V_$FLASH_RECOVERY_AREA_USAGE TO GSYSTEM;
GRANT SELECT ON SYS.V_$ARCHIVE_DEST_STATUS TO GSYSTEM;
GRANT SELECT ON SYS.V_$PARAMETER TO GSYSTEM;
GRANT SELECT ON SYS.V_$RESOURCE_LIMIT TO GSYSTEM;
GRANT SELECT ON SYS.V_$DIAG_ALERT_EXT TO GSYSTEM;

-- Grant read-only access to DBA views
-- NOTE: DBA_* are PUBLIC synonyms for SYS.DBA_* views owned by SYS.
-- Grant on the underlying SYS.DBA_* objects to avoid ORA-02030.
GRANT SELECT ON SYS.DBA_TABLESPACES TO GSYSTEM;
GRANT SELECT ON SYS.DBA_DATA_FILES TO GSYSTEM;
GRANT SELECT ON SYS.DBA_FREE_SPACE TO GSYSTEM;
GRANT SELECT ON SYS.DBA_TEMP_FILES TO GSYSTEM;
=======
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
>>>>>>> 52d1e42f9e9a3a1cd2443f67e95224f00e15bdac

-- Grant access to dual
GRANT SELECT ON dual TO GSYSTEM;

-- Verify the user
SELECT username, account_status FROM dba_users WHERE username = 'GSYSTEM';
