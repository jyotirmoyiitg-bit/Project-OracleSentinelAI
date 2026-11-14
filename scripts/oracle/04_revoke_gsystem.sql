-- Oracle Sentinel AI: emergency lock and revoke direct monitoring grants.
-- Run as SYSDBA in the target PDB (or non-CDB). Existing data is not deleted.
WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE

DECLARE
    user_count PLS_INTEGER;
BEGIN
    IF SYS_CONTEXT('USERENV', 'CON_NAME') IN ('CDB$ROOT', 'PDB$SEED') THEN
        RAISE_APPLICATION_ERROR(-20001, 'Connect to the target PDB before revoking local monitoring access.');
    END IF;
    SELECT COUNT(*) INTO user_count FROM SYS.DBA_USERS WHERE username = 'GSYSTEM';
    IF user_count = 0 THEN
        RAISE_APPLICATION_ERROR(-20002, 'GSYSTEM does not exist in this container.');
    END IF;
END;
/

-- Lock first so a missing grant cannot prevent new-login containment.
ALTER USER GSYSTEM ACCOUNT LOCK;

BEGIN
    FOR grant_row IN (
        SELECT table_name
        FROM SYS.DBA_TAB_PRIVS
        WHERE grantee = 'GSYSTEM' AND owner = 'SYS' AND privilege = 'SELECT'
          AND table_name IN (
              'V_$INSTANCE', 'V_$DATABASE', 'V_$VERSION', 'V_$TEMP_SPACE_HEADER',
              'V_$RECOVERY_FILE_DEST', 'V_$FLASH_RECOVERY_AREA_USAGE',
              'V_$ARCHIVE_DEST_STATUS', 'V_$PARAMETER', 'V_$RESOURCE_LIMIT',
              'V_$DIAG_ALERT_EXT', 'DBA_TABLESPACES', 'DBA_DATA_FILES',
              'DBA_FREE_SPACE', 'DBA_TEMP_FILES', 'DUAL',
              'V_$SGAINFO', 'V_$PGASTAT', 'V_$SQL', 'V_$SESSION',
              'V_$SESSION_LONGOPS', 'V_$RMAN_BACKUP_JOB_DETAILS',
              'DBA_SCHEDULER_RUNNING_JOBS', 'DBA_JOBS_RUNNING',
              'V_$PDBS', 'V_$SERVICES', 'V_$OSSTAT', 'V_$SYS_TIME_MODEL',
              'V_$SYSTEM_EVENT', 'V_$FILESTAT', 'V_$LIBRARYCACHE',
              'V_$ARCHIVED_LOG', 'V_$DATAGUARD_STATS', 'DBA_USERS',
              'DBA_OBJECTS', 'DBA_ROLE_PRIVS', 'DBA_SCHEDULER_JOB_RUN_DETAILS',
              'V_$CONTROLFILE', 'V_$LOG', 'V_$LOGFILE', 'DBA_SEGMENTS', 'V_$SGASTAT',
              -- Clean up extra grants from earlier provisioning scripts as well.
              'V_$TABLESPACE', 'V_$SYSSTAT', 'V_$SYSMETRIC'
          )
    ) LOOP
        EXECUTE IMMEDIATE 'REVOKE SELECT ON SYS.' || grant_row.table_name || ' FROM GSYSTEM';
    END LOOP;
    FOR grant_row IN (
        SELECT privilege FROM SYS.DBA_SYS_PRIVS
        WHERE grantee = 'GSYSTEM' AND privilege = 'CREATE SESSION'
    ) LOOP
        EXECUTE IMMEDIATE 'REVOKE CREATE SESSION FROM GSYSTEM';
    END LOOP;
END;
/

SELECT username, account_status FROM SYS.DBA_USERS WHERE username = 'GSYSTEM';
PROMPT GSYSTEM locked and direct monitoring grants revoked. Existing sessions are not terminated.
PROMPT Review any separately assigned roles/system privileges; PUBLIC privileges are unchanged.
