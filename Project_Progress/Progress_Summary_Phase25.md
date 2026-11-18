# Phase 25 Implementation Summary — MemoryDB, StorageDB, ParameterDB, and AlertDB

**Status:** A database workspace has four new sections beside GraphDB and WaitDB, modelled on the screens the operator supplied and drawn in the portal's light palette. Automated checks pass. The four new readings were run once against the local Oracle database through the monitoring account, and the four pages were opened in a browser against a running API with synthetic data. The live launcher was added and `-CheckPrerequisites` passed. The full launcher run was not performed.<br>
**Phase specification:** [Phase 25 — Database sections](../Phase_25_Database_Sections.md)<br>
**Previous summary:** [Phase 24 — Copilot supervisor](./Progress_Summary_Phase24.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)<br>
**Architecture:** [docs/architecture_v2.html](../docs/architecture_v2.html)

Migration head remains `0012`. No table or column was added. No Oracle grant was added. Privileged execution stays disabled. No language model is used.

## What Was Implemented

### Readings

- `backend/src/sentinel/adapters/oracle/readings.py`: seven fixed statements and four catalog entries: `get_sga_breakdown`, `get_tablespace_utilization`, `get_parameters`, `get_alert_tail`. Every view was already granted to the monitoring account.

### Loaders and routes

- `backend/src/sentinel/graph/sections.py`: models, assembly, and loaders for the four sections; `parameters_for_role` and `alert_tail_for_role` for the administrator-only values.
- `backend/src/sentinel/api/v1/graph.py`: `GET /databases/{id}/memory/sga`, `/storage/tablespaces`, `/parameters`, `/alert-log`.

### Portal

- `frontend/src/components/DatabaseView.tsx`: the frame the four sections share (database heading, loading and error states, refresh), plus `StackedBars`, `PercentBar`, and `SectionTabs`.
- `frontend/src/MemoryPage.tsx`, `StoragePage.tsx`, `ParameterPage.tsx`, `AlertPage.tsx`.
- `frontend/src/components/DatabaseTabs.tsx` and `App.tsx`: four workspace tabs and four routes. `CopilotPage.tsx`: links to the four sections. `styles.css`: a Phase 25 block.

## What the reference screens showed and what was built

| Reference screen | Built as | Left out |
|---|---|---|
| Disk Storage: Tablespace Utilization table and used/free chart; Online Redo; Recovery Area; Archive Destinations | StorageDB with the same four tabs, the same eight columns, and a bar for each tablespace | Flash Cache tab |
| SGA Utilization chart by pool and a Pool/Name/Bytes table; Shared Pool Utilization; Buffer Cache | MemoryDB with those three tabs and a PGA tab | In-Memory tab; the chart splits each pool into used and free, not into every component |
| Oracle Parameters: Name, Value, Is default, Sys modifiable, Description | ParameterDB with the same columns, a filter, and a Changed only switch | The Modify parameter control. Nothing can be changed |
| Alert Log | AlertDB with the same lines, a filter, and errors and warnings marked | OEM tabs |

The reference used a dark palette. The pages use the portal's own, as decided for GraphDB.

## Check against the real database

The four readings were run once against `PDBORCL` through the app's read-only account, printing counts only.

| Reading | Result |
|---|---|
| SGA | 5 pools totalling 12.8 GB, 60 components, 25 shared pool areas |
| Tablespaces | 6 rows, used percent and fragments present, one temporary |
| Parameters | 366, of which 19 changed; version banner returned; 10 values held back from an operator |
| Alert log | 299 lines over 4 days, 1 error and 10 warnings; 50 lines had a path masked for an operator |

Each took under a second. This run found two faults that the synthetic tests could not: Oracle rejected the first pool statement with ORA-00935, and the first severity rule marked 210 of 299 alert lines as errors because Oracle types most lines as "unknown". Both were fixed and the run repeated.

## Verification

- `backend/tests/graph/test_phase25_sections.py`: **12 passed**. Full backend suite: **546 passed, 1 skipped**. Ruff is clean on the changed files.
- `frontend/src/Phase25.test.tsx`: **10 passed**. Full frontend suite: **73 passed** in 14 files. `tsc -b` is clean. One existing test that pins the list of workspace tabs was updated.
- In a browser against a running API with synthetic data: the nine workspace tabs were shown, StorageDB listed five tablespaces with their bars, MemoryDB showed four pools and eight components, ParameterDB showed seven parameters with two values held back, and AlertDB showed five lines with the error and the warning marked. No page scrolled sideways at 1280 pixels.
- `Verify_Phase25_Real.ps1` parsed and `-CheckPrerequisites` passed. Its check step passed against a synthetic API. The full launcher was not run.

## Live launcher

`test_real_phase25\Verify_Phase25_Real.ps1` follows the Phase 23 launcher through the real Oracle connection, then reads the four sections as an administrator and as a temporary operator. It checks that used plus free equals each total, that parameters are sorted with a correct changed count, that the alert log is oldest first with a correct error count, that an operator is shown no path, and that an unknown database returns 404. It changes no setting.

```powershell
.\test_real_phase25\Verify_Phase25_Real.ps1 -CheckPrerequisites
.\test_real_phase25\Verify_Phase25_Real.ps1 -TnsAlias PDBORCL -Interactive
```

## Resume / Next-Agent Pickup

1. No migration and no grant. Restart the API and reload the portal.
2. A new section is a workspace tab, a route with a database id, and a page built on `DatabaseView`. Do not add a top-bar link or a chooser page.
3. A new reading must be a fixed statement with a row cap on a view that is already granted. If it needs a new grant, add it to `scripts/oracle/02_grant_readonly_monitoring.sql` and the permission tests.
4. A value that holds a path or an address stays administrator-only. Use `holds_location` and the path mask in `graph/sections.py`.
5. A response must not name a view or contain SQL; the launchers check for it.
6. Run a new statement against a real database before trusting it. Synthetic rows do not prove that Oracle accepts it.
7. Not built: the In-Memory and Flash Cache tabs, the buffer pool advisory, a date range for the alert log, copilot use of the four new readings.
8. Do not record the full live check as passed until `Verify_Phase25_Real.ps1` is run.
9. These edits are uncommitted. Commit only when requested.
