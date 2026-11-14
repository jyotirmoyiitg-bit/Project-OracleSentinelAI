# Phase 12 Implementation Summary — GraphDB Memory

Later work is in [Phase 13 — Layout and disk storage](./Progress_Summary_Phase13.md).

**Status:** The Memory structures panel shows the current SGA, PGA, and background processes for the selected database. The live launcher was added. `-CheckPrerequisites` passed. The full Oracle run was not performed.<br>
**Phase specification:** [Phase 12 — Memory structures](../Phase_12_GraphDB_Memory.md)<br>
**Previous summary:** [Phase 11 — GraphDB shell](./Progress_Summary_Phase11.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head remains `0010`. Privileged execution stays disabled. No new grant was required. `V$SGAINFO`, `V$PGASTAT`, `V$PARAMETER`, and `V$SESSION` were already granted to `GSYSTEM`.

## What Was Implemented

`GET /api/v1/databases/{id}/graph/memory` is administrator-only. It reads one registered database and returns `sga`, `pga`, `background_processes`, `collected_at`, `limitations`, and `snapshot`.

- **SGA.** One bar for each `V$SGAINFO` pool that has a byte value. The label is the pool name and the size in MB or GB. `sga_target` and `sga_max_size` are a caption.
- **PGA.** Allocated, used, and the aggregate target from `V$PGASTAT` and `pga_aggregate_target`. Used above the target is marked high. A missing target says the target parameter was not returned.
- **Background processes.** One card per `V$SESSION` row whose `TYPE` is `BACKGROUND`: program, status, and state. The list scrolls inside the panel. An empty program name says the process name was not returned.

The three readings are fixed SQL in the Oracle adapter. Each accepts only the database alias. A failed grant is named on that card and the other cards stay. The response does not include SQL. `V$BGPROCESS`, `V$PROCESS`, AWR, ASH, and ADDM are not queried. A missing or disabled database returns 404. An operator receives 403.

Physical structures, logical structures, and management still say they are not collected. Phases 13 through 16 replace those equal panels with the performance-home columns: service and host, server processes, SGA with process pills, and disk storage drawn as cylinders. The portal palette stays. There is no resize or kill control.

## Live launcher

`test_real_phase12\Verify_Phase12_Real.ps1` follows the Phase 11 launcher and then reads memory for the selected database.

1. Check Python 3.14, Node, and Docker. `-CheckPrerequisites` stops there and does not connect to Oracle.
2. Load the project-root `.env`, then `test_real_phase12\.env`. The phase file keeps sign-in on, the language model off, and both execution flags false.
3. Start Docker PostgreSQL and upgrade Alembic to head `0010`.
4. Stop a listener already on port 8000 or the portal port, then start the API.
5. Sign in with the local bootstrap account. The password stays in the root `.env`.
6. Reuse or register the selected TNS alias and test the Oracle connection.
7. Read memory for that database. Sizes must be labeled in MB or GB. SGA, PGA, and background processes must all be present. A limitation may replace one group. An operator receives 403. A missing database returns not-found. The response must not contain SQL. The temporary `phase12-operator` account is removed. Audit rows are not deleted.
8. Start the portal. `-Interactive` opens `#/graph/databases/{id}`.

```powershell
.\test_real_phase12\Verify_Phase12_Real.ps1 -CheckPrerequisites
.\test_real_phase12\Verify_Phase12_Real.ps1 -TnsAlias PDBORCL -Interactive
```

A full run stops whatever is already listening on port 8000 and the portal port (5173 by default). It does not stop a listener on port 8765.

## Verification

- `backend/tests/graph/test_memory.py`: the three statements use only the granted views, and a missing PGA grant leaves the SGA pools and the background process.
- `backend/tests/api/test_graph.py`: an administrator receives the three groups, an operator receives 403, and a missing or disabled database returns 404.
- `frontend/src/GraphPage.test.tsx`: the Memory panel shows the buffer cache, the PGA target, and a background process, and switching databases replaces the previous process name. **4 passed**.
- `test_real_phase12\Verify_Phase12_Real.ps1 -CheckPrerequisites` passed. The full Oracle launcher was not run.

## Resume / Next-Agent Pickup

1. Restart the API before expecting memory figures in a portal that was already running.
2. Sign in as the administrator and open GraphDB for one database.
3. Do not query `V$BGPROCESS` or `V$PROCESS` for this panel.
4. Do not record the full live check as passed until `Verify_Phase12_Real.ps1` is run against Oracle.
5. Phase 13 turns the page into four columns and fills the disk-storage cylinders. Control-file and redo grants are already in `02_grant_readonly_monitoring.sql`; the target PDB still needs that script applied.
6. These edits are uncommitted. Commit only when requested.
