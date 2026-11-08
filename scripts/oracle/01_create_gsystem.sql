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

-- Grant access to dual
GRANT SELECT ON dual TO GSYSTEM;

-- Verify the user
SELECT username, account_status FROM dba_users WHERE username = 'GSYSTEM';
