-- Oracle Sentinel AI | GSYSTEM read-only monitoring user provisioning
-- TEMPLATE / DBA REVIEW REQUIRED. Run in EACH APPROVED PDB or non-CDB, not indiscriminately in CDB$ROOT.
-- For a true CDB-wide common user Oracle normally requires C## prefix. GSYSTEM here is a
-- LOCAL schema per PDB / non-CDB, with a centrally managed identity and secret.
-- Example: ALTER SESSION SET CONTAINER = YOUR_PDB_NAME;
-- Verify the current container before running:
SHOW CON_NAME
SELECT name, cdb, open_mode, log_mode, flashback_on FROM v$database;

-- SECURITY: replace the example password before running; avoid storing actual passwords
-- in source control, console logs, shell history, or tickets. Prefer wallet / secret vault.
CREATE USER GSYSTEM IDENTIFIED BY "REPLACE_WITH_STRONG_SECRET_FROM_VAULT"
  DEFAULT TABLESPACE USERS TEMPORARY TABLESPACE TEMP
  QUOTA 0 ON USERS ACCOUNT UNLOCK;
GRANT CREATE SESSION TO GSYSTEM;

-- Monitoring only. Explicit object SELECTs; NO SELECT ANY DICTIONARY,
-- SELECT ANY TABLE, DBA, SYSDBA, CREATE TABLE, ALTER SYSTEM or DDL permissions.
<<<<<<< HEAD
-- IMPORTANT: Oracle v$ views are PUBLIC synonyms for SYS.V_$ fixed tables.
-- Direct GRANT on v$ fails with ORA-02030: can only select from fixed tables/views.
-- Always grant on SYS.V_$ (the actual object), not v$ (the synonym).
-- The same applies to DBA_* views: grant on SYS.DBA_* not DBA_*.
=======
-- Oracle exposes dynamic performance views through underlying SYS.V_$ objects.
>>>>>>> 52d1e42f9e9a3a1cd2443f67e95224f00e15bdac
GRANT SELECT ON SYS.V_$DATABASE             TO GSYSTEM;
GRANT SELECT ON SYS.V_$INSTANCE             TO GSYSTEM;
GRANT SELECT ON SYS.V_$PARAMETER            TO GSYSTEM;
GRANT SELECT ON SYS.V_$RESOURCE_LIMIT       TO GSYSTEM;
GRANT SELECT ON SYS.V_$RECOVERY_FILE_DEST   TO GSYSTEM;
GRANT SELECT ON SYS.V_$FLASH_RECOVERY_AREA_USAGE TO GSYSTEM;
GRANT SELECT ON SYS.V_$SESSION              TO GSYSTEM;
GRANT SELECT ON SYS.V_$SYSTEM_EVENT         TO GSYSTEM;
GRANT SELECT ON SYS.V_$DIAG_INFO            TO GSYSTEM;
GRANT SELECT ON SYS.V_$DIAG_ALERT_EXT       TO GSYSTEM;
GRANT SELECT ON SYS.V_$ARCHIVE_DEST_STATUS  TO GSYSTEM;
GRANT SELECT ON SYS.V_$LOG                  TO GSYSTEM;
GRANT SELECT ON SYS.V_$LOGFILE              TO GSYSTEM;

-- Tablespace + storage information. Availability / columns can vary by release.
GRANT SELECT ON SYS.DBA_TABLESPACES             TO GSYSTEM;
GRANT SELECT ON SYS.DBA_DATA_FILES              TO GSYSTEM;
GRANT SELECT ON SYS.DBA_FREE_SPACE              TO GSYSTEM;
GRANT SELECT ON SYS.DBA_TEMP_FILES              TO GSYSTEM;
GRANT SELECT ON SYS.DBA_TABLESPACE_USAGE_METRICS TO GSYSTEM;

-- Optional backup signals; validate permissions / exposure and Oracle edition/version.
-- GRANT SELECT ON SYS.V_$RMAN_BACKUP_JOB_DETAILS TO GSYSTEM;

-- Optional RAC/fleet monitoring: grant appropriate GV_$ objects per target release.
-- Avoid AWR/ASH/ADDM/DBA_HIST views unless licensed and specifically approved.

-- DBA-only verification (switch session as GSYSTEM after provisioning):
-- SELECT instance_name, status FROM v$instance;
-- SELECT name, log_mode, flashback_on FROM v$database;
-- SELECT originating_timestamp, message_text FROM v$diag_alert_ext
--   WHERE originating_timestamp > SYSTIMESTAMP - INTERVAL '15' MINUTE
--   ORDER BY originating_timestamp DESC FETCH FIRST 20 ROWS ONLY;
-- SELECT tablespace_name, used_percent FROM dba_tablespace_usage_metrics;
-- In some releases / PDB contexts, additional privileges or different queries
-- may be needed; validate in target Oracle version before deployment.
