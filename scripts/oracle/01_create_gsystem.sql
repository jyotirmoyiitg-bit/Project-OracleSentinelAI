-- Oracle Sentinel AI: create a dedicated local monitoring user.
-- Run in SQL*Plus/SQLcl as SYSDBA connected to the target PDB (or non-CDB).
-- For an existing GSYSTEM account, run 02_grant_readonly_monitoring.sql instead.
SET ECHO OFF
SET VERIFY OFF
SET DEFINE ON
WHENEVER OSERROR EXIT FAILURE
WHENEVER SQLERROR EXIT SQL.SQLCODE

BEGIN
    IF SYS_CONTEXT('USERENV', 'CON_NAME') IN ('CDB$ROOT', 'PDB$SEED') THEN
        RAISE_APPLICATION_ERROR(-20001, 'Connect to the target PDB, not CDB$ROOT/PDB$SEED. Do not change _ORACLE_SCRIPT.');
    END IF;
END;
/

-- Use a unique password without double quotes or ampersands (SQL*Plus substitution).
-- Do not spool this session or supply the password on the command line.
ACCEPT gsystem_password CHAR PROMPT 'New GSYSTEM password: ' HIDE
CREATE USER GSYSTEM IDENTIFIED BY "&gsystem_password";
UNDEFINE gsystem_password

-- No object-creation privileges or tablespace quota are needed for monitoring.
@@02_grant_readonly_monitoring.sql

SELECT username, account_status FROM SYS.DBA_USERS WHERE username = 'GSYSTEM';
PROMPT GSYSTEM created. Connect as GSYSTEM to the same service and run 03_verify_gsystem.sql.
