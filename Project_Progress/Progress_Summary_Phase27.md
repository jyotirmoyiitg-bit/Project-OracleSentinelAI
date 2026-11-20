# Phase 27 Implementation Summary — Tablespace timeline

**Status:** The Overview timeline no longer draws every tablespace as one line, the "Tablespace usage" card shows the fullest tablespace by name, and used space is also measured against the autoextend maximum. Automated checks pass. The changed storage statement was run against PDBORCL and MY19C, and the pages were checked in a browser against a seeded copy of the MY19C values.<br>
**Phase specification:** [Phase 27 — Tablespace timeline](../Phase_27_Tablespace_Timeline.md)<br>
**Previous summary:** [Phase 26 — Copilot fixes](./Progress_Summary_Phase26.md)<br>
**Architecture:** [docs/architecture.md](../docs/architecture.md)

Migration head remains `0012`. No table, column, setting, or Oracle grant was added. Privileged execution stays disabled.

## What was reported

On the MY19C Overview page the "Tablespace Used Pct" timeline swung between about 0 and 95 percent every few minutes, and the card showed 0.5 percent.

## Cause

The development database held five rows per collection for MY19C, one per tablespace: USERS 94.41, SYSTEM 94.19, SYSAUX 94.06, UNDOTBS1 10.58, INDX 0.47. The timeline drew all of them as one line, and the card took the first row, INDX. Nothing was shrinking.

## What changed

| Area | Change |
|---|---|
| Storage probe | Also reads `MAXBYTES` and stores `storage.tablespace.bytes_max` and `storage.tablespace.used_pct_of_max` |
| Metrics API | `label=key=value` filters, up to four, matched in SQL; malformed filters return HTTP 422 |
| Detail API | The tablespace summary uses exact metric names and adds `max_bytes` and `used_pct_of_max` |
| Timeline | Series picker, fullest first; "All tablespaces" draws one line each; rows of different series are never joined; up to 5,000 rows |
| Card | Fullest tablespace, by name |
| Tablespaces table | "Of maximum" column |
| Synthetic adapter | Emits the two new metrics |

## Files

- `backend/src/sentinel/adapters/oracle/probes.py`, `synthetic.py`
- `backend/src/sentinel/api/v1/databases.py`, `dashboard.py`, `schemas.py`
- `backend/src/sentinel/persistence/telemetry.py`
- `frontend/src/series.ts` (new), `App.tsx`, `api.ts`, `types.ts`, `styles.css`
- `frontend/src/components/TimelineChart.tsx`, `TablespaceTable.tsx`
- Tests: `backend/tests/api/test_phase27_tablespace_series.py` (new), `backend/tests/collectors/test_collection.py`, `backend/tests/test_oracle_permission_scripts.py`, `frontend/src/Phase27.test.tsx` (new)
- Docs: `Phase_27_Tablespace_Timeline.md`, `docs/architecture.md`, `docs/operations-runbook.md`

## Verification

| Check | Result |
|---|---|
| Backend | 593 passed, 1 skipped |
| Frontend | 80 passed; TypeScript clean |
| Ruff | Changed and new files clean |
| Live Oracle | Statement ran on PDBORCL and MY19C; MY19C USERS is 94.41 % of allocated and 14.25 % of maximum |
| PostgreSQL | Label filter ran on the development database |
| Browser | Card, default series, all-tablespaces chart, and table checked on a seeded copy; no console errors |

## Open decision

Health and the rules still use percent of allocated space, so autoextend tablespaces keep MY19C at Warning. Switching them to percent of maximum would change when alerts fire.
