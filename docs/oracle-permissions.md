# Oracle Sentinel AI — GSYSTEM Permissions

## Overview

GSYSTEM is a local Oracle monitoring user with minimal read-only privileges. It is **not** a superuser and cannot execute DDL or DML.

## Required Privileges

- `CREATE SESSION` — to connect
- `SELECT` on specific `V$` and `DBA_` views for monitoring

<<<<<<< HEAD
## Critical: ORA-02030 Gotcha

Oracle `v$` views (e.g., `v$instance`, `v$database`) are **PUBLIC synonyms** that point to the actual fixed tables `SYS.V_$INSTANCE`, `SYS.V_$DATABASE`, etc. Similarly, `DBA_*` views are PUBLIC synonyms for `SYS.DBA_*` views owned by SYS.

When granting privileges, you **cannot** use the synonym name:

```sql
-- ❌ FAILS with ORA-02030: can only select from fixed tables/views
GRANT SELECT ON v$instance TO GSYSTEM;

-- ✅ CORRECT — grant on the actual underlying object
GRANT SELECT ON SYS.V_$INSTANCE TO GSYSTEM;
```

The same rule applies to `DBA_*` views:

```sql
-- ❌ May fail with ORA-02030
GRANT SELECT ON dba_tablespaces TO GSYSTEM;

-- ✅ CORRECT
GRANT SELECT ON SYS.DBA_TABLESPACES TO GSYSTEM;
```

**At query time**, GSYSTEM can still use `v$instance` and `dba_tablespaces` in SQL — the synonyms resolve correctly. The `SYS.V_$` prefix is only required in `GRANT`/`REVOKE` statements.

All provisioning scripts in `scripts/oracle/` use the correct `SYS.V_$` and `SYS.DBA_*` syntax.

=======
>>>>>>> 52d1e42f9e9a3a1cd2443f67e95224f00e15bdac
## Provisioning

Run `scripts/oracle/01_create_gsystem.sql` as SYSDBA on each target database.

## Verification

Run `scripts/oracle/03_verify_gsystem.sql` as GSYSTEM to verify access.

## Emergency Revoke

Run `scripts/oracle/04_revoke_gsystem.sql` as SYSDBA to revoke all privileges and lock the account.
