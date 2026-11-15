# Phase 19 Implementation Summary — GraphDB Wait Stats

Later work is in [Phase 20 — Security, navigation, and copilot coverage](./Progress_Summary_Phase20.md).

**Status:** WaitDB is its own page. It asks for a database, then opens Summary, Event waits, Blocking locks, Lock activity, and Buffer busy waits. Charts use two samples kept on the page. The live launcher was added. `-CheckPrerequisites` passed. The full Oracle run was not performed. The new WaitDB grants were added to the script and were not run.<br>
**Phase specification:** [Phase 19 — GraphDB wait stats](../Phase_19_GraphDB_Waits.md)<br>
**Previous summary:** [Phase 18 — Structured AI answers](./Progress_Summary_Phase18.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head remains `0010`. Privileged execution stays disabled. `V$SYSTEM_EVENT` and `V$SYS_TIME_MODEL` were already granted. The grant script now also includes `V$ENQUEUE_STATISTICS`, `V$LOCK`, `V$WAITSTAT`, and `V$SESSION_EVENT`. That script was not run on Oracle.

## What Was Implemented

- `GET /api/v1/databases/{id}/graph/waits` returns up to 15 non-idle events and `DB CPU`. Every signed-in role can read it. A missing or disabled database returns 404. The response has no SQL.
- **WaitDB** is a top-level page beside GraphDB. `#/wait` asks for one registered database. `#/wait/databases/{id}` opens that database. GraphDB no longer has a View waits control.
- The page has five tabs. **Summary** shows block activity, event waits, top waits, data-file requests, data-file latency, and parsing. **Event waits** keeps the non-idle table and chart, including CPU time. **Blocking locks** lists SID, serial, user, PID, lock type, mode wanted, and object; an empty result says `No data`. **Lock activity** lists enqueue gets, waits, and wait time, with an ms/s chart. **Buffer busy waits** shows a tablespace pie for sessions waiting now, a block-class pie, and a user table.
- Total waits and total wait time are since instance startup. Per-second columns and every chart are since the previous sample on this page. The first view says a second sample is required.
- The charts are not filled from ASH, AWR, or ADDM. Samples are not stored in PostgreSQL. Average active sessions is not drawn.
- `GET /api/v1/databases/{id}/wait/activity` returns locking, blocking, parsing, buffer busy, and I/O. A failed section leaves the others. The response has no SQL.

## Live launcher

`test_real_phase19\Verify_Phase19_Real.ps1` follows the Phase 16 launcher and then reads waits for the selected database.

```powershell
.\test_real_phase19\Verify_Phase19_Real.ps1 -CheckPrerequisites
.\test_real_phase19\Verify_Phase19_Real.ps1 -TnsAlias PDBORCL -Interactive
```

A full run stops whatever is already listening on port 8000 and the portal port (5173 by default). It does not stop a listener on port 8765. The language model stays off. Counts and states are printed. Event names, file paths, cookies, and SQL are not printed. The temporary `phase19-operator` account is removed. Audit rows are not deleted.

The wait check confirms: anonymous requests receive 401; the administrator receives events or a named limitation, and CPU time or a named limitation; no idle wait class is included; an operator can read the same endpoint; a missing database returns 404; the response contains no SQL.

## Verification

- `backend/tests/graph/test_waits.py`: the wait statement excludes idle events, has no bind variable, and a missing CPU grant leaves the event row.
- `backend/tests/api/test_graph.py`: an operator receives the synthetic events and CPU time, and a missing or disabled database returns 404.
- `backend/tests/graph/test_wait_activity.py`: enqueue, lock, buffer-class, and file I/O statements have no bind variable. A failed enqueue reading leaves the blocking row and the block class.
- `frontend/src/WaitPage.test.tsx`: WaitDB asks for a database, then shows the summary charts, event table, blocking lock, lock activity, and buffer-busy pies. Refresh draws the chart.
- `test_real_phase19\Verify_Phase19_Real.ps1` parsed, and `-CheckPrerequisites` passed. The full Oracle launcher was not run.

## Resume / Next-Agent Pickup

1. Restart the API before expecting `/graph/waits` or `/wait/activity` from a process that was already running. Open WaitDB, choose the database, and read the page. The first view asks for a second sample.
2. Run `scripts/oracle/02_grant_readonly_monitoring.sql` in the target PDB before expecting lock activity, blocking locks, and the block-class pie. Until then those sections name the missing view. Do not query `V$SYSMETRIC`, `V$ACTIVE_SESSION_HISTORY`, or `DBA_HIST_*` to draw a chart.
3. Do not change the copilot's eight-event wait reading.
4. Do not record the full live check as passed until `Verify_Phase19_Real.ps1` is run against Oracle.
5. These edits are uncommitted. Commit only when requested.
