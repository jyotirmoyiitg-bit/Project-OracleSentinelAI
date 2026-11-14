# Phase 14 Implementation Summary — Service, Host, and Server Processes

Later work is in [Phase 15 — SGA meters and recovery](./Progress_Summary_Phase15.md).

**Status:** The service and host cards, the server-process ovals, and the 20 largest segments are filled for the selected database. The live launcher was added. `-CheckPrerequisites` passed. The full Oracle run was not performed.<br>
**Phase specification:** [Phase 14 — Service, host, and server processes](../Phase_14_GraphDB_Logical.md)<br>
**Previous summary:** [Phase 13 — Layout and disk storage](./Progress_Summary_Phase13.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head remains `0010`. Privileged execution stays disabled. No new Oracle grant was added. No table or view was created in `GSYSTEM`.

## What Was Implemented

`GET /api/v1/databases/{id}/graph/activity` is administrator-only. It returns `service`, `host`, `server_processes`, `collected_at`, and `limitations`.

- **Service.** Uptime from `V$INSTANCE`, formatted as days, hours, and minutes. Database name from `V$DATABASE`. Pluggable database name from `V$PDBS`. No PDB row says `Not a pluggable database.` Total users and active users come from `V$SESSION`. Response and average active stay `Not collected.`
- **Host.** A CPU donut from `BUSY_TIME / (BUSY_TIME + IDLE_TIME)`, captioned since instance startup. Run queue appears only when `V$OSSTAT` returns `LOAD`. Free memory appears only when it returns `FREE_MEMORY_BYTES`. A missing statistic says the host did not return it. There is no ESX line.
- **Server processes.** PGA target and used stay on this column. The ovals are Dedicated, Shared, Dispatchers, Parallel query, Job queue, and Total. Dispatchers are background sessions whose program contains `D000`. Parallel query slaves contain `(P0` or `(P1`. An empty program is not counted. Job queue is the row count from `DBA_SCHEDULER_RUNNING_JOBS`. A failed session reading names `SYS.V_$SESSION` on those ovals and leaves PGA visible. There is no Enterprise Manager agent oval.

The storage response gains `segments`: at most 20 rows from `DBA_SEGMENTS`, ordered by size. The heading says these are the 20 largest segments, not a full inventory. A missing grant says the segment inventory is not available and the data-file cylinder stays. `V$TEMPSEG_USAGE` is not queried.

`SELECT` on `SYS.DBA_SEGMENTS` is already in `scripts/oracle/02_grant_readonly_monitoring.sql`. The target PDB needs that script applied if the segment list says the inventory is not available.

## Live launcher

`test_real_phase14\Verify_Phase14_Real.ps1` follows the Phase 13 launcher and then reads activity and the segment list.

```powershell
.\test_real_phase14\Verify_Phase14_Real.ps1 -CheckPrerequisites
.\test_real_phase14\Verify_Phase14_Real.ps1 -TnsAlias PDBORCL -Interactive
```

A full run stops whatever is already listening on port 8000 and the portal port (5173 by default). It does not stop a listener on port 8765. The language model stays off. Counts are printed. Segment names and file paths are not printed.

## Verification

- `backend/tests/graph/test_activity.py`: the session SQL filters `TYPE` and `SERVER`, the segment SQL is limited to 20 rows from `DBA_SEGMENTS`, and a denied session reading leaves the PGA target.
- `backend/tests/api/test_graph.py`: an administrator receives the service lines, response and average active stay not collected, and the memory endpoint still returns PGA.
- `frontend/src/GraphPage.test.tsx`: response and average active render `Not collected.`, the CPU caption says since instance startup, and there is no agent oval. **4 passed**.
- The GraphDB backend tests for activity, storage, memory, the API, and the grant scripts: **54 passed**.
- `test_real_phase14\Verify_Phase14_Real.ps1 -CheckPrerequisites` passed. The full Oracle launcher was not run.

## Resume / Next-Agent Pickup

1. Restart the API before expecting the service and host cards in a portal that was already running.
2. If the segment list says the inventory is not available, run `scripts/oracle/02_grant_readonly_monitoring.sql` as SYSDBA in the target PDB. It already grants `DBA_SEGMENTS`.
3. Do not grant `V$SYSMETRIC`, `V$BGPROCESS`, `V$PROCESS`, or `V$TEMPSEG_USAGE`.
4. Do not record the full live check as passed until `Verify_Phase14_Real.ps1` is run against Oracle.
5. Phase 15 adds the SGA meters, background-process pills, recovery cylinders, backup status, and Data Guard. `V$SGASTAT` is the grant added in that phase.
6. These edits are uncommitted. Commit only when requested.
