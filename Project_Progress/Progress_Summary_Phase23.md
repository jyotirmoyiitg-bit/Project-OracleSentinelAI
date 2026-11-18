# Phase 23 Implementation Summary — Graph History: GraphDB and WaitDB Open Filled

**Status:** GraphDB and WaitDB no longer open empty. A page takes a second reading 3 seconds after it opens, and the scheduler stores counter samples that the pages replay, so the WaitDB charts are drawn at once and the GraphDB meters show an average at once. Automated checks pass. The real API and portal were run with synthetic databases and both pages were opened in a browser. The live launcher was added and `-CheckPrerequisites` passed. The full Oracle run was not performed.<br>
**Phase specification:** [Phase 23 — Graph history](../Phase_23_Graph_History.md)<br>
**Previous summary:** [Phase 22 — Agents](./Progress_Summary_Phase22.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)<br>
**Architecture:** [docs/architecture_v2.html](../docs/architecture_v2.html)

Later work is in [Phase 24 — Copilot supervisor](./Progress_Summary_Phase24.md), which moves the head to `0012`.

Migration head remains `0011`. No table or column was added. No Oracle grant was added. No reading SQL changed. Privileged execution stays disabled. No language model is used.

## Why

The operator asked whether the data the agents store fills the graphs before live data arrives. It does not: agent snapshots are taken on request, are off by default, and do not hold the rate counters. The graceful fix was a quick second reading on the page and a stored history taken by the always-on scheduler. The operator asked for both as Phase 23.

## What Was Implemented

### Stored samples

- `backend/src/sentinel/graph/history.py`: `take_sample` reads five existing fixed readings on one Oracle logon; `store_sample` writes one JSON row to `observations` (`activity.sample`); `read_history` returns samples oldest first with their age; `sample_fleet` reads every enabled database, four at a time, with a 45 second limit each; `delete_expired` applies the retention.
- `backend/src/sentinel/graph/sampler.py`: `GraphHistorySampler`, a timer that checks every 30 seconds whether a sweep is due and runs retention hourly. `main.py` starts it with the collection scheduler and stops it on shutdown.
- `backend/src/sentinel/persistence/telemetry.py`: the general observation list leaves `activity.sample` rows out unless asked for by name.
- `config/settings.py` and `config/store.py`: `graph_history_enabled` (on), `graph_history_interval_seconds` (300, 60 to 3600), `graph_history_retention_hours` (24, 1 to 168), in a new Graph history section in Admin.

### API

- `GET /api/v1/databases/{id}/graph/history?minutes=60` in `api/v1/graph.py`. Every signed-in role may read it. It does not read Oracle.

### Portal

- `frontend/src/api.ts`: `getGraphHistory`, which returns no samples instead of failing the page.
- `frontend/src/GraphPage.tsx`: the stored sample becomes the first baseline; the caption says the rate is an average since that sample; a quick reading of the activity picture follows after 3 seconds.
- `frontend/src/WaitPage.tsx`: stored samples are replayed through `waitView`, `topPoint`, and a new `ioPoints` function that the live path now shares; one extra reading follows after 3 seconds; the caption comes from the server when history is shown.

### Documents

- [docs/architecture_v2.html](../docs/architecture_v2.html) was written in this session and now includes the stored history in its three diagrams.
- The Fleet analyst, the Advisor, and copilot answers from agent snapshots were called Phase 23 in the Phase 22 documents. Those documents now call them a later phase.

## Verification

- `backend/tests/graph/test_phase23_history.py`: **13 passed**. Full backend suite: **491 passed, 1 skipped**. Ruff is clean on the changed files.
- `frontend/src/Phase23.test.tsx`: **5 passed**. Full frontend suite: **63 passed** in 13 files. `tsc -b` is clean.
- The API and the portal were started on spare ports with synthetic databases, a temporary SQLite database, the scheduler on, and a 60 second interval. The sampler stored a row for a database that had none. WaitDB opened with all five charts drawn and the stored-history caption, asked for the history once, and read both pictures a second time on its own; the parsing rates then appeared. GraphDB opened with the four meters filled and the stored-sample caption, then changed to the live caption after its quick reading, with memory and storage read once. The browser console had no error.
- `Verify_Phase23_Real.ps1` parsed and `-CheckPrerequisites` passed. Its check step passed against a synthetic API with the scheduler on: two samples stored in about a minute, every check as expected, settings put back. The full Oracle launcher was not run.
- The synthetic counters do not change, so the synthetic rates were zero. A changing rate was checked in the portal tests, not in the browser.

## Live launcher

`test_real_phase23\Verify_Phase23_Real.ps1` follows the Phase 22 launcher through the real Oracle connection. It then sets a 60 second interval, waits up to four minutes for two stored samples of the selected database, and checks: the four rate counters are stored; samples come oldest first and contain no SQL; the live counters continue from the latest stored sample; samples are history rows only and stay out of the general observation list; an operator can read the history and cannot change the settings; an interval under 60 seconds and a retention over 168 hours are refused; turning the setting off is reported. Every setting is put back and the temporary `phase23-operator` is removed.

```powershell
.\test_real_phase23\Verify_Phase23_Real.ps1 -CheckPrerequisites
.\test_real_phase23\Verify_Phase23_Real.ps1 -TnsAlias PDBORCL -Interactive
```

`test_real_phase23\.env` is gitignored and holds no secret. The quick second sample is taken by the page, so the launcher does not check it; open GraphDB at the end of an `-Interactive` run to see it.

## Resume / Next-Agent Pickup

1. No migration is needed. Restart the API to get the sampler. It runs only when `SCHEDULER_ENABLED=true`.
2. Stored history is on by default. It adds one Oracle logon per database every 5 minutes. Turn it off or change it in Admin, Graph history.
3. A sample is one JSON row in `observations` with `metric_name = 'activity.sample'`. Do not add it to `current_observations`: health and rules read that table.
4. A new stored counter goes in `graph/history.py` (`KINDS` and `build_payload`) and must be a reading already in the catalog. The page must replay it through the same function it uses for a live reading.
5. Lock activity, parsing, and buffer busy counters are not stored. They fill from the quick second sample.
6. The charts space their points evenly and have no time axis, so a stored 5 minute point and a live point look the same width. The caption says which is which.
7. The settings apply to the whole system. A value for one database was not built.
8. Later phase, not built: Fleet analyst and daily brief, Advisor drafts from accepted findings, copilot answers from agent snapshots.
9. Do not record the full live check as passed until `Verify_Phase23_Real.ps1` is run against Oracle.
10. These edits are uncommitted. Commit only when requested.
