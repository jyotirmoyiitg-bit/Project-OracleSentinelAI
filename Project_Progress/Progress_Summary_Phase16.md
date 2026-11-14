# Phase 16 Implementation Summary — Rate Meters and Refresh

Later work is in [Phase 17 — GraphDB for every operator](./Progress_Summary_Phase17.md).

**Status:** The center column shows four rate meters from two page samples of `V$SYSSTAT`. Refresh reloads memory, storage, and activity and keeps each card until its new reading returns. The live launcher was added. `-CheckPrerequisites` passed. The full Oracle run was not performed.<br>
**Phase specification:** [Phase 16 — Rate meters, refresh, and live check](../Phase_16_GraphDB_Live.md)<br>
**Previous summary:** [Phase 15 — SGA meters and recovery](./Progress_Summary_Phase15.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head remains `0010`. Privileged execution stays disabled. No new grant was added. No table or view was created in `GSYSTEM`.

## What Was Implemented

The activity response gains `counters`: physical reads, redo entries, parse count (total), and execute count. Those values come from `V$SYSSTAT`, which is already granted. The page keeps the previous sample for the open database. It is not written to PostgreSQL and it is not shared with another browser or another database.

- **First sample.** Each meter says `Rate needs a second sample.`
- **Second sample.** The rate is `(new value − previous value) / seconds between the two responses` on this page. The caption is `Since the previous refresh on this page.` A real zero increase is `0 /s`.
- **A bad sample.** A counter that goes backwards, a missing statistic, or a gap under one second says `Not collected.` The other meters still render.
- **Refresh.** One control reloads memory, storage, and activity as three reads. A card stays visible, with its collection time, until the new reading returns. A failed reading is replaced by its limitation. A failed storage card does not clear SGA.
- **Automatic refresh.** The existing header interval calls that same refresh on GraphDB. Leaving the page aborts its requests.
- **Still uncollected.** Response and average active stay `Not collected.`

## Live launcher

`test_real_phase16\Verify_Phase16_Real.ps1` follows the Phase 15 launcher and then reads memory, storage, and activity.

```powershell
.\test_real_phase16\Verify_Phase16_Real.ps1 -CheckPrerequisites
.\test_real_phase16\Verify_Phase16_Real.ps1 -TnsAlias PDBORCL -Interactive
```

A full run stops whatever is already listening on port 8000 and the portal port (5173 by default). It does not stop a listener on port 8765. The language model stays off. Counts and states are printed. File paths, cookies, and SQL are not printed.

## Verification

- `backend/tests/graph/test_activity.py`: the rate statement uses only `V$SYSSTAT` and the four statistic names.
- `backend/tests/api/test_graph.py`: an operator receives 403 on memory, storage, and activity, and the activity response carries the four counters. Response and average active stay `Not collected.`
- `frontend/src/GraphPage.test.tsx`: the first load asks for a second sample, the next refresh shows a rate only for a counter that increased, and a failed storage reading leaves the memory card. **6 passed**.
- The activity, GraphDB API, and grant-script tests: **53 passed**.
- `test_real_phase16\Verify_Phase16_Real.ps1` parsed, and `-CheckPrerequisites` passed. The full Oracle launcher was not run.

## Resume / Next-Agent Pickup

1. Restart the API before expecting `counters` from a process that was already running. Reload GraphDB after that restart. The first view asks for a second sample; Refresh or the header interval fills the meters.
2. Do not add a grant for Phase 16. `V$SYSSTAT` is already in `scripts/oracle/02_grant_readonly_monitoring.sql`.
3. Do not grant `V$SYSMETRIC`, `V$ACTIVE_SESSION_HISTORY`, or `DBA_HIST_*`. Do not turn the four counters into ASH, AWR, or ADDM rates.
4. Do not record the full live check as passed until `Verify_Phase16_Real.ps1` is run against Oracle.
5. These edits are uncommitted. Commit only when requested.
