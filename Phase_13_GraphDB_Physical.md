# Phase 13 — GraphDB Layout and Disk Storage

**Phase:** 13  
**Status:** Implemented  
**Prerequisites:** [Phase 12 — Memory structures](Phase_12_GraphDB_Memory.md)  
**Next phase:** [Phase 14 — Service, host, and server processes](Phase_14_GraphDB_Logical.md)

The reference is a performance-home page: four columns, card headers, horizontal meters, count ovals, process pills, and storage drawn as cylinders. This phase adopts that arrangement. It does not adopt the black background or the green-on-black palette. Cards, type, and status marks use the portal colors already on the fleet pages.

## 1. Overview

Replace the four equal GraphDB panels with one page for the selected database:

| Column | Contents in this phase | Filled later |
|---|---|---|
| Service and host | Card shells. Each value says `Not collected in this phase.` | Phase 14 |
| Server processes | The PGA target and used value already returned by the memory reading. The other ovals say `Not collected in this phase.` | Phase 14 |
| SGA and background processes | The Phase 12 pools and process cards, placed in this column. Process names stay as returned. | Phase 15 turns the known background names into pills and adds the SGA meters |
| Disk storage | Cylinders for database files and redo logs, plus the control-file list | Phase 15 adds the recovery cylinder and the archive cylinder |

A column is never a mix of two databases. There is still no Execute, Backup, or SQL control.

## 2. Disk storage

| Picture | Reading | Source |
|---|---|---|
| Database files cylinder | Existing data-file and temp-file readings | `DBA_DATA_FILES`, `DBA_TEMP_FILES` |
| Redo cylinder | New fixed reading `get_redo_logs` | `V$LOG`, `V$LOGFILE` |
| Control files | New fixed reading `get_control_files` | `V$CONTROLFILE` |

`SELECT` on `SYS.V_$CONTROLFILE`, `SYS.V_$LOG`, and `SYS.V_$LOGFILE` is already in `scripts/oracle/02_grant_readonly_monitoring.sql`. The target PDB still needs that script applied. Until the grant is present, that cylinder or list says the reading is not available and names the view. It does not draw an empty cylinder as if the database had no redo or no control file.

The database-files card shows online file count, online tablespace count, used bytes, and used percent. The cylinder fill is that used percent. Temp files are included and labeled Temp. The file names scroll inside the card.

The redo card shows group count and total size. The cylinder fill is the share of groups that are current or active. One row per group lists thread, member path, size, and status. The current group is marked. Paths are text. They are not passed to a shell and they are not downloads.

Control files are a short list under the database-files card: name and status. They are not drawn as a cylinder, because a control file has no used-percent fill.

Flash cache is a row on the database-files card with the sentence `Not collected.` This deployment has no flash-cache reading. The row is not given a zero.

## 3. API

`GET /api/v1/databases/{id}/graph/storage`

Administrator only. Response fields: `data_files`, `temp_files`, `control_files`, `redo_logs`, `collected_at`, and `limitations`. A failed grant names its view and the other lists still return. No SQL text is returned. The existing memory endpoint is unchanged.

## 4. Acceptance

- [x] The page is four columns in the reference order, using the portal palette.
- [x] Database files and redo logs are cylinders. Control files are a list.
- [x] A missing control-file or redo grant does not clear the data-file cylinder.
- [x] A file path is displayed and is not executed.
- [x] Flash cache says `Not collected.`

## 5. Tests

- Adapter test: control-file and redo SQL reference only `V$CONTROLFILE`, `V$LOG`, and `V$LOGFILE`.
- API test: a permission error on control files still returns the data-file list.
- Portal test: the current redo group is marked, and an offline data file is marked, without using the reference color scheme.
- Live check: `test_real_phase13\Verify_Phase13_Real.ps1`. `-CheckPrerequisites` does not connect to Oracle. The full run is a pass only after it is actually run.

## 6. Out of scope

Creating a data file, switching a log, or copying a control file. Service, host, process ovals, SGA hit ratio, recovery area, archive, backup, and the top rate meters wait for later phases.
