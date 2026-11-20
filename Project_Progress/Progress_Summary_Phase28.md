# Phase 28 Implementation Summary — SessionDB

**Status:** Each database workspace has a SessionDB tab that lists user sessions grouped by program, like the session browser in the reference screenshots. Choosing a session shows the statement it is running, or the last one it ran, formatted, and the explain plan Oracle cached for it. Automated checks pass. The list and the statement were read from MY19C, the launcher's checks were run in process against MY19C, and the list was checked in a browser against the synthetic preview. The cached plan needs one new grant, which has not yet been applied to the local databases. The live launcher was not run end to end.<br>
**Phase specification:** [Phase 28 — SessionDB](../Phase_28_SessionDB.md)<br>
**Previous summary:** [Phase 27 — Tablespace timeline](./Progress_Summary_Phase27.md)<br>
**Architecture:** [docs/architecture.md](../docs/architecture.md)

Migration head remains `0012`. No table, column, or setting was added. One Oracle grant was added: `SELECT ON SYS.V_$SQL_PLAN`. Privileged execution stays disabled.

## What was asked

1. A SessionDB feature at the database level, from a screenshot of a session browser: sessions grouped by program with user, OS user, terminal, SID, status, module, action, logon time, last call, and SQL_ID.
2. The current SQL statement and the explain plan of the selected session, from two more screenshots, in the same phase.
3. This summary updated, and a PowerShell test script for Phase 28.

## What changed

| Area | Change |
|---|---|
| Session list | `get_session_list`: one fixed `SELECT` on `V$SESSION`, user sessions only, not its own session, at most 500 rows, now with the serial number |
| Statement | `get_session_statement`: `V$SESSION` joined to `V$SQL` for one SID and serial number; the current statement, or the last one when idle, with SQL_ID, child, plan hash, and statistics |
| Plan | `get_statement_plan`: `V$SQL_PLAN` for that SQL_ID and child. Cached plan only; nothing is explained again |
| Binding | First readings with bind variables. Only a whole number or an Oracle SQL_ID can be bound, each checked first; person and model text is never bound |
| API | `GET /api/v1/databases/{id}/sessions` (every role; names for administrators only) and `GET /api/v1/databases/{id}/sessions/{sid}/statement?serial=` (administrators only, 403 otherwise) |
| Portal | SessionDB tab after WaitDB; program groups, active sessions in green, filters; selectable rows; Statement tab with line numbers, Format statement, Copy, and statistics; Explain plan tab with the cached plan as an indented table |
| Oracle scripts | Grant, verify, and revoke scripts updated for `V$SQL_PLAN` and the new `V$SESSION` and `V$SQL` columns |
| Live check | `test_real_phase28\Verify_Phase28_Real.ps1` and `real_runtime.py` |

## Files

- Backend: `adapters/oracle/activity.py` (bound readings), `adapters/oracle/readings.py`, `graph/sections.py`, `graph/session_sql.py` (new), `api/v1/graph.py`
- Scripts: `scripts/oracle/02_grant_readonly_monitoring.sql`, `03_verify_gsystem.sql`, `04_revoke_gsystem.sql`
- Frontend: `SessionPage.tsx` (new), `components/SessionStatementPanel.tsx` (new), `sqlFormat.ts` (new), `App.tsx`, `api.ts`, `components/DatabaseTabs.tsx`, `styles.css`
- Tests: `backend/tests/graph/test_phase28_sessions.py` (new), `backend/tests/test_oracle_permission_scripts.py`, `frontend/src/Phase28.test.tsx` (new), `frontend/src/FleetWorkspace.test.tsx`
- Live check: `test_real_phase28/Verify_Phase28_Real.ps1`, `test_real_phase28/real_runtime.py`, `test_real_phase28/.env` (copied from Phase 26, not committed)
- Docs: `Phase_28_SessionDB.md`, `docs/architecture.md`, `docs/operations-runbook.md`

## Verification

| Check | Result |
|---|---|
| Backend | 607 passed, 1 skipped |
| Frontend | 90 passed; TypeScript clean |
| Ruff | Changed and new files clean |
| Live list | MY19C: LCOTP.EXE (2), TOAD.EXE (2), the same as the reference |
| Live statement | MY19C: each session returned its last statement; SID 246 returned the `DELETE` in the reference |
| Live plan | Not read: the monitoring account has no `V$SQL_PLAN` grant yet, and the page says so |
| Launcher checks in process | MY19C: tagged session listed, marked statement shown, wrong serial found nothing, operator view without names |
| Browser | Synthetic preview of the list as a non-administrator |

## To finish on the local databases

Run as SYSDBA in each monitored PDB (MY19C, PDBORCL):

```sql
GRANT SELECT ON SYS.V_$SQL_PLAN TO GSYSTEM;
```

Then:

```powershell
.\test_real_phase28\Verify_Phase28_Real.ps1 -TnsAlias MY19C -RequirePlan -Interactive
```

## Open decisions

- DBAs see the list without names and cannot see statements, because only the administrator role has `platform.read`.
- No `EXPLAIN PLAN` for a statement whose plan is no longer cached; it would write to a plan table.
