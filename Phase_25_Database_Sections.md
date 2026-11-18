# Phase 25 — MemoryDB, StorageDB, ParameterDB, and AlertDB

**Phase:** 25
**Status:** Implemented
**Prerequisites:** [Phase 24 — Copilot supervisor](Phase_24_Copilot_Supervisor.md), [Phase 19 — GraphDB wait stats](Phase_19_GraphDB_Waits.md), [Phase 17 — GraphDB for every operator](Phase_17_GraphDB_Access.md)

## 1. Overview

A database workspace gains four sections beside GraphDB and WaitDB. Each follows a screen the operator supplied from another monitoring tool, redrawn in the portal's own light palette.

| Section | Tabs | Shows |
|---|---|---|
| MemoryDB | SGA utilization, Shared pool, Buffer cache, PGA | The SGA by pool with used and free memory, its largest components, the largest areas of the shared pool, the buffer cache size and hit ratio, and the PGA |
| StorageDB | Tablespace utilization, Online redo, Recovery area, Archive destinations | Every tablespace with total, free, used, contents, status, fragments, and used percent, with a used and free bar for each |
| ParameterDB | none | The initialization parameters in effect: name, value, default or changed, system modifiable, and description, with the version banner |
| AlertDB | none | The most recent lines of the alert log, oldest first, with errors and warnings marked |

The workspace tabs are now Overview, GraphDB, WaitDB, MemoryDB, StorageDB, ParameterDB, AlertDB, Events, Copilot. Choosing a tab keeps the database, and choosing a database keeps the tab.

No migration and no new Oracle grant were added. No page changes Oracle. No language model is used.

## 2. Readings

Four fixed readings were added to the catalog. Each is a `SELECT` with a row cap, takes no argument, and reads views the monitoring account already had.

| Reading | Views | Cap |
|---|---|---|
| `get_sga_breakdown` | `V$SGASTAT`, three statements: largest components, totals by pool, largest shared pool areas | 60, 40, 25 rows |
| `get_tablespace_utilization` | `DBA_TABLESPACES`, `DBA_DATA_FILES`, `DBA_TEMP_FILES`, `DBA_FREE_SPACE`, `V$TEMP_SPACE_HEADER` | 200 rows |
| `get_parameters` | `V$PARAMETER`, and `V$VERSION` for the banner (optional) | 700 rows |
| `get_alert_tail` | `V$DIAG_ALERT_EXT`, database alert file only, last 7 days | 300 rows |

MemoryDB also uses the existing GraphDB memory picture for the hit ratio and the PGA. StorageDB also uses the existing GraphDB storage picture for redo, the recovery area, and the archive destinations. When that older picture cannot be read, the new part of the page is still shown.

## 3. What each section computes

- **MemoryDB.** Entries of `V$SGASTAT` with no pool are grouped: `buffer_cache` and `shared_io_pool` as buffer cache, the rest as other. A pool's free memory is its `free memory` entry. Used percent is used over the pool's size.
- **StorageDB.** Free space of a permanent or undo tablespace is the sum of its free extents, and its fragment count is how many free extents it has. Free space of a temporary tablespace comes from its temp-file headers, and it has no fragment count. Used is total minus free.
- **ParameterDB.** A parameter is changed when `ISDEFAULT` is not `TRUE`. Parameters are sorted by name.
- **AlertDB.** A line is an error when it carries an ORA, TNS, RMAN, or PLS code, or when Oracle typed it as an error or an incident. It is a warning when Oracle typed it as a warning or the text says warning, cannot, failed, failure, corrupt, or terminated. Most lines have the type "unknown" and are plain lines.

## 4. Access

Every signed-in role can read all four sections, as with GraphDB and WaitDB.

| Held back from a non-administrator | Shown instead |
|---|---|
| A parameter value that holds a file path or a network address | "Shown to an administrator." The parameter name and its other columns stay |
| A file path inside an alert log line | "(path shown to an administrator)". The rest of the line stays |
| Redo member file names and the recovery area location | The existing GraphDB rule |

The page says which rule applied.

## 5. API

| Route | Returns |
|---|---|
| `GET /api/v1/databases/{id}/memory/sga` | `pools`, `components`, `shared_pool`, `total_bytes`, `total_display`, `collected_at`, `limitation`, `caption` |
| `GET /api/v1/databases/{id}/storage/tablespaces` | `tablespaces`, `total_bytes`, `used_bytes`, `total_display`, `used_display`, `collected_at`, `limitation`, `caption` |
| `GET /api/v1/databases/{id}/parameters` | `version`, `parameters`, `total`, `changed`, `collected_at`, `limitation`, `detail`, `detail_note`, `caption` |
| `GET /api/v1/databases/{id}/alert-log` | `lines`, `errors`, `warnings`, `window`, `collected_at`, `limitation`, `detail`, `detail_note`, `caption` |

Each needs `dashboard.read` and opens one Oracle session for the request. A disabled or unknown database returns 404. A reading that is not granted returns HTTP 200 with a `limitation` that names the view. No response contains SQL or a view name.

## 6. Portal

- Routes: `#/memory/databases/{id}`, `#/storage/databases/{id}`, `#/parameters/databases/{id}`, `#/alerts/databases/{id}`. There is no chooser page; the sections are reached from a database.
- Every section has the database heading, the collection time, Database detail, and Ask copilot, and follows the header refresh.
- MemoryDB and StorageDB draw horizontal bars on one scale, split into used and free. Tables right-align numbers and show megabytes with two decimals.
- ParameterDB has a text filter and a "Changed only" switch. A changed parameter has a marker on its row.
- AlertDB has a text filter and a choice of all lines, warnings and errors, or errors only. The log scrolls to the newest line.
- The copilot's opening panel links to the four sections.

## 7. Acceptance

- [x] The four readings are fixed read-only statements on views already granted, each with a row cap.
- [x] The SGA is grouped by pool with used and free, and rows that cannot be used are left out.
- [x] Tablespaces report used, free, percent, and fragments, and a temporary tablespace has no fragment count.
- [x] Parameters are listed with their default flag and the version, and a non-administrator does not see paths or addresses.
- [x] The alert log reads oldest first with errors and warnings marked, and a non-administrator reads it without file paths.
- [x] A reading that is denied or fails names its limitation. Each loader asks for one fixed reading.
- [x] The four routes are open to every role, name no SQL, and use one Oracle session each.
- [x] The portal shows each section, its tabs and filters, the held-back values, and the error states, and the workspace keeps the database when the tab changes.
- [x] The four readings were run once against the local Oracle database through the monitoring account: 5 SGA pools totalling 12.8 GB, 6 tablespaces, 366 parameters with 19 changed, and 299 alert lines.
- [ ] The full live check is recorded only after `Verify_Phase25_Real.ps1` is run. A written spec is not a pass.

## 8. Tests

- `backend/tests/graph/test_phase25_sections.py`: the statements and their grants; each assembly; the role rules; limitations; the routes; one session per picture.
- `frontend/src/Phase25.test.tsx`: each page, its tabs and filters, held-back values, error states, and the workspace tabs.
- Live check: `test_real_phase25\Verify_Phase25_Real.ps1`. `-CheckPrerequisites` does not connect to Oracle.

## 9. Out of scope

Changing a parameter; the buffer pool advisory and `V$BUFFER_POOL`, which is not granted; the in-memory column store tab and the flash cache tab of the reference screens; more than 300 alert lines or a date picker; alert lines from the listener; history of tablespace size on this page (the Overview forecast has it); copilot use of the four new readings.
