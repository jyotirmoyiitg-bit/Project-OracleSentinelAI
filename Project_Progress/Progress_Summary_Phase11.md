# Phase 11 Implementation Summary — GraphDB Shell

Later work is in [Phase 12 — Memory structures](./Progress_Summary_Phase12.md).

**Status:** The administrator GraphDB page is in the portal. It shows one registered database and four empty panels. It does not query Oracle and does not draw measurements. The live launcher was added. `-CheckPrerequisites` passed. The full Oracle run was not performed.<br>
**Phase specification:** [Phase 11 — GraphDB shell](../Phase_11_GraphDB_Shell.md)<br>
**Previous summary:** [Configuration and knowledge intake](./Progress_Summary_Configuration.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head remains `0010`. Privileged execution stays disabled, and `privileged_operations` is still not written.

## What Was Implemented

GraphDB is the portal name for a graphical view of one registered Oracle database. It is not a separate graph database.

- The **GraphDB** navigation entry is shown only to an administrator (`admin` or `platform_admin`).
- `#/graph` asks the administrator to choose a registered database.
- `#/graph/databases/{id}` shows that database's display name, TNS alias, and four panels: Memory structures, Physical structures, Logical structures, and Management.
- Each panel says `Not collected in this phase.` There is no Execute, Backup, or SQL control.
- `GET /api/v1/graph` returns those four panels. An operator, DBA, auditor, or viewer receives 403. A signed-out request receives 401 when sign-in is required.
- A missing or disabled database uses the existing not-found state. Switching databases replaces the previous name.
- The page uses the existing database list and database detail endpoints. It does not open an Oracle reading.

`scripts/oracle/02_grant_readonly_monitoring.sql` now also grants `SELECT` on `SYS.V_$CONTROLFILE`, `SYS.V_$LOG`, `SYS.V_$LOGFILE`, and `SYS.DBA_SEGMENTS`. Those objects are for later GraphDB panels. Phase 11 does not query them. The emergency revoke script lists the same four objects. Run the grant script as SYSDBA in the target PDB before Phase 13.

## Live launcher

`test_real_phase11\Verify_Phase11_Real.ps1` follows the Phase 9 launcher and stops at the empty shell.

1. Check Python 3.14, Node, and Docker. `-CheckPrerequisites` stops there and does not connect to Oracle.
2. Load the project-root `.env`, then `test_real_phase11\.env`. The phase file keeps sign-in on, the language model off, and both execution flags false.
3. Start Docker PostgreSQL and upgrade Alembic to head `0010`.
4. Stop a listener already on port 8000 or the portal port, then start the API.
5. Sign in with the local bootstrap account. The password stays in the root `.env`.
6. Reuse or register the selected TNS alias and test the Oracle connection. This connection check is the registry test. GraphDB itself does not read memory, files, segments, or backups.
7. Confirm the four empty panels, that a signed-out request is rejected, that a temporary operator receives 403, and that a missing database returns not-found. The temporary `phase11-operator` account is removed. Audit rows are not deleted.
8. Start the portal. `-Interactive` opens `#/graph/databases/{id}`.

```powershell
.\test_real_phase11\Verify_Phase11_Real.ps1 -CheckPrerequisites
.\test_real_phase11\Verify_Phase11_Real.ps1 -TnsAlias PDBORCL -Interactive
```

A full run stops whatever is already listening on port 8000 and the portal port (5173 by default). It does not stop a listener on port 8765.

## Verification

- `backend/tests/api/test_graph.py`: an administrator receives the four empty panels, and an operator, DBA, auditor, and viewer receive 403. **1 passed**.
- `frontend/src/GraphPage.test.tsx`: an administrator opens one database and sees the four titles, a switch replaces the previous name, an operator does not see the link and receives 403, and a disabled database shows not-found. **4 passed**.
- `frontend/src/App.test.tsx` still passed. **7 passed**.
- `test_real_phase11\Verify_Phase11_Real.ps1 -CheckPrerequisites` passed. The full Oracle launcher was not run.

## Resume / Next-Agent Pickup

1. Restart the API before expecting GraphDB in a portal that was already running.
2. Sign in as the administrator. An operator does not see GraphDB.
3. Do not query Oracle from the shell. Measurements start in Phase 12.
4. Run `02_grant_readonly_monitoring.sql` in the target PDB before the physical and logical panels need the new views.
5. Do not record the full live check as passed until `Verify_Phase11_Real.ps1` is run against Oracle.
6. These edits are uncommitted. Commit only when requested.
