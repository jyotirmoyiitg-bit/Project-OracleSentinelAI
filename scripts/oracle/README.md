# Oracle monitoring permissions

DBA-reviewed SQL*Plus/SQLcl scripts for the dedicated **GSYSTEM** read-only
monitoring account used by Phase 1-3 collectors. They grant `CREATE SESSION` and
specific `SELECT` privileges, not DBA roles, dictionary-wide access, object-creation
privileges, tablespace quotas, or AWR/ASH/ADDM access.

## Provision each target service

Connect as SYSDBA to the **PDB service used by the portal's TNS alias** (for example
`PDBORCL`), not to `CDB$ROOT` or `PDB$SEED`. Scripts reject root/seed execution.
Do not set `_ORACLE_SCRIPT` or create a common `C##` account to bypass this check.
Non-CDB databases can run the same scripts. Root-wide/common-user monitoring is
not provisioned by these local-user templates.

For a new account:

```sql
-- Connect using your DBA-approved authentication method; do not put passwords
-- on the command line. In SQL*Plus/SQLcl, from this directory:
@01_create_gsystem.sql
```

[01_create_gsystem.sql](01_create_gsystem.sql) prompts for a hidden password and
invokes [02_grant_readonly_monitoring.sql](02_grant_readonly_monitoring.sql)
relative to its own location. There is no default password. Use a unique password
without double quotes or ampersands for this SQL*Plus substitution-based template;
use DBA-approved provisioning for passwords outside that restriction. Do not spool
the password-entry session. No default tablespace or quota is required.

For an **existing GSYSTEM account**, apply the full current grant set without
recreating the user or changing its password:

```sql
@02_grant_readonly_monitoring.sql
```

Grants are rerunnable. This does not unlock an account or remove older privileges;
review existing grants/roles separately, especially if older provisioning granted
unlimited quota or broad dictionary access. New scripts no longer add the unused
session/sysstat/sysmetric grants. Oracle DDL commits implicitly: a failed run may
have partially applied grants. Correct the reported failure and rerun the grant
script; do not assume a rollback undoes account/grant DDL.

Reconnect as **GSYSTEM through that same database alias** and run:

```sql
@03_verify_gsystem.sql
```

[03_verify_gsystem.sql](03_verify_gsystem.sql) fails on the first SQL error and checks
the views and columns used by instance, tablespace, temporary space, FRA/archive,
resource, configuration and alert-log probes. Queries return at most one row per
view (seven selected parameters); alert message text and client details are not
printed. Empty results still establish access, but do not prove FRA/archiving or
alert-log visibility is configured in that container. PDB visibility is determined
by Oracle version/configuration; do not compensate with broad grants if a feature
is unavailable. Record any unsupported views/columns and review the collector's
capability/error status.

In the portal, register each intended database service explicitly. Four TNS entries
do not necessarily mean four databases: listener/external-procedure aliases are
not database targets, and different aliases may reach the same database. Provision
each distinct target PDB/database; restarting the app does not provision Oracle users.
Configure matching backend credentials/secret references; no password is stored by
these scripts in the application's PostgreSQL registry.

## Grant coverage

`V$` names are synonyms; direct grants use the underlying `SYS.V_$` objects
to avoid synonym grant errors such as ORA-02030.

| Probe | Direct SYS object grants |
|---|---|
| Connection/version | `V_$VERSION`, `DUAL` |
| Instance/database | `V_$INSTANCE`, `V_$DATABASE` |
| Permanent tablespace usage | `DBA_TABLESPACES`, `DBA_DATA_FILES`, `DBA_FREE_SPACE` |
| Temporary space | `V_$TEMP_SPACE_HEADER` |
| FRA | `V_$RECOVERY_FILE_DEST`, `V_$FLASH_RECOVERY_AREA_USAGE` |
| Archive destinations | `V_$ARCHIVE_DEST_STATUS` |
| Resource limits | `V_$RESOURCE_LIMIT` |
| Configuration | `V_$PARAMETER` |
| Phase 3 alert ingestion | `V_$DIAG_ALERT_EXT` |
| Copilot datafiles | `DBA_DATA_FILES`, `DBA_TEMP_FILES` |
| Copilot memory | `V_$SGAINFO`, `V_$PGASTAT` |
| Copilot top SQL | `V_$SQL` |
| Copilot sessions | `V_$SESSION` |
| Copilot backups | `V_$SESSION_LONGOPS`, `V_$RMAN_BACKUP_JOB_DETAILS` |
| Copilot running jobs | `DBA_SCHEDULER_RUNNING_JOBS`, `DBA_JOBS_RUNNING` |
| Readings: availability | `V_$PDBS`, `V_$SERVICES` |
| Readings: performance | `V_$OSSTAT`, `V_$SYS_TIME_MODEL`, `V_$SYSTEM_EVENT`, `V_$FILESTAT` |
| Readings: memory and parse | `V_$SYSSTAT`, `V_$LIBRARYCACHE` |
| Readings: recovery and Data Guard | `V_$ARCHIVED_LOG`, `V_$DATAGUARD_STATS` |
| Readings: accounts, objects, roles, failed jobs | `DBA_USERS`, `DBA_OBJECTS`, `DBA_ROLE_PRIVS`, `DBA_SCHEDULER_JOB_RUN_DETAILS` |

Reference: [Oracle probes](../../backend/src/sentinel/adapters/oracle/probes.py),
[alert poller](../../backend/src/sentinel/adapters/oracle/alert_log.py),
[capability detection](../../backend/src/sentinel/adapters/oracle/capabilities.py).
SMTP and portal notifications do not need additional Oracle privileges.

## Emergency containment

Run [04_revoke_gsystem.sql](04_revoke_gsystem.sql) as SYSDBA in the target PDB:

```sql
@04_revoke_gsystem.sql
```

It locks GSYSTEM **before** revoking existing direct grants. It revokes only grants
that exist, including the additional objects used by older script versions, so
missing grants do not cause ORA-01927 on a rerun. It does not drop the user, terminate
existing sessions, revoke PUBLIC access, or remove unrelated roles/system
privileges. A DBA must handle those separately if immediate full containment is
required. Re-granting monitoring access does not unlock the account.

## Validation

Static regression tests compare grants with the collector SQL and verification
queries, check revoke coverage, and reject conflict markers or broad grants:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_oracle_permission_scripts.py -q
```

These are not a substitute for executing the scripts on each supported Oracle
version and target container. Live Oracle SQL execution has not been verified by
the static tests.
