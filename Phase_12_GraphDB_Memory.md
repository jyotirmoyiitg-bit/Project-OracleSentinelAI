# Phase 12 — GraphDB Memory Structures

**Phase:** 12  
**Status:** Implemented  
**Prerequisites:** [Phase 11 — GraphDB shell](Phase_11_GraphDB_Shell.md)  
**Next phase:** [Phase 13 — Layout and disk storage](Phase_13_GraphDB_Physical.md)

## 1. Overview

Fill the Memory structures panel for the selected database. Show the SGA, the PGA, and the background processes from fixed read-only readings. The picture is a current snapshot, not a forecast.

## 2. Readings

Use views already granted to `GSYSTEM`. Do not query AWR, ASH, or ADDM.

| Picture | Reading | Source |
|---|---|---|
| SGA pools | New fixed reading `get_sga_components` | `V$SGAINFO` |
| SGA target | Existing parameter reading | `V$PARAMETER` names `sga_target` and `sga_max_size` |
| PGA | New fixed reading `get_pga_status` | `V$PGASTAT` and `pga_aggregate_target` |
| Background processes | New fixed reading `get_background_processes` | `V$SESSION` where `TYPE = 'BACKGROUND'` |

`V$BGPROCESS` and `V$PROCESS` are not granted. Do not query them in this phase. The process card uses the session program name. If that name is empty, the card says the process name was not returned.

Each reading accepts only the database alias. The SQL text is fixed in the Oracle adapter. A failed grant returns the existing permission error for that reading and leaves the other cards visible.

## 3. Picture

Inside the Memory structures panel:

- **SGA.** One bar for each `V$SGAINFO` pool that has a byte value. The bar label is the pool name and the size. The target is a caption, not a second source of truth.
- **PGA.** Allocated, used, and the aggregate target. A used value above the target is marked high. A missing target says the target parameter was not returned.
- **Background processes.** One card per background session: program, status, and state. The card list scrolls inside the panel. The page itself does not grow with the process count.

Numbers are formatted as MB or GB. A byte value is not shown as an unlabeled integer. The panel states that the figures are the current instance snapshot.

## 4. API

`GET /api/v1/databases/{id}/graph/memory`

Administrator only. Response fields: `sga` items, `pga` items, `background_processes`, `collected_at`, and `limitations`. No SQL text is returned. A database the caller cannot access returns 404.

## 5. Acceptance

- [x] The Memory panel shows SGA, PGA, and background processes for the selected database only.
- [x] A missing grant on one reading does not blank the other two.
- [x] No AWR, ASH, ADDM, or ungranted dynamic view is queried.
- [x] A non-administrator receives 403.

## 6. Tests

- Adapter test: the SGA, PGA, and background SQL are the fixed statements and reference only the granted views.
- API test: an administrator receives the three groups; an operator receives 403.
- Portal test: the Memory panel renders the fixture pools, the PGA target, and at least one background process.
- Live check: `test_real_phase12\Verify_Phase12_Real.ps1`. `-CheckPrerequisites` does not connect to Oracle. The full run is a pass only after it is actually run.

## 7. Out of scope

Historical SGA charts, resize advice, and killing a session. A resize or a kill is a change and is not sent to Oracle.
