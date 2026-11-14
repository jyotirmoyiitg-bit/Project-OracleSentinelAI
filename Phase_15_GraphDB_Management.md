# Phase 15 — SGA Meters and Recovery

**Phase:** 15  
**Status:** Implemented  
**Prerequisites:** [Phase 14 — Service, host, and server processes](Phase_14_GraphDB_Logical.md)  
**Next phase:** [Phase 16 — Refresh and live check](Phase_16_GraphDB_Live.md)

## 1. Overview

Finish the center column and the recovery cylinders. Pool sizes that Phase 12 already returns become labeled horizontal meters. Recovery, archive, backup, and Data Guard use readings that are already granted, plus one new shared-pool grant. Nothing on the page starts a backup or a job.

## 2. SGA meters

Inside the center column, one meter per pool returned by `V$SGAINFO`: buffer cache, keep pool, recycle pool, java pool, large pool, redo buffer, and shared pool. A pool row with no bytes is omitted. The current SGA caption remains the SGA target caption from Phase 12.

| Meter | Reading | Grant |
|---|---|---|
| Pool size | Existing `V$SGAINFO` rows | Granted |
| Buffer cache hit ratio | `V$SYSSTAT`: consistent gets, db block gets, physical reads | Granted |
| Shared pool used | `V$SGASTAT` free memory in the shared pool, against the shared-pool size | New `SELECT` on `SYS.V_$SGASTAT` |
| In-memory area | `V$SGAINFO` row `In-Memory Area Size` | Granted |
| ASMM | `V$PARAMETER` `sga_target` | Granted |

The hit ratio is `(consistent gets + db block gets - physical reads) / (consistent gets + db block gets)` when the denominator is positive. The caption says the ratio is since instance startup. A missing statistic leaves the meter at `Not collected.` and does not draw 100 percent.

Shared-pool used percent is `(shared pool size - free bytes) / shared pool size`. Add `SYS.V_$SGASTAT` to `scripts/oracle/02_grant_readonly_monitoring.sql` and to the revoke list. Until that grant works on the target, the size meter still shows and the used meter says `V$SGASTAT` is not granted.

If `sga_target` is greater than zero, the card says Automatic Shared Memory Management is on. If it is zero, the card says it is off. `memory_target` greater than zero says Automatic Memory Management is on. A missing parameter says the target parameter was not returned.

An in-memory row with no bytes says the in-memory area is not in use. Result cache has no granted reading, so that row says `Not collected.`

## 3. Background pills

The background list from Phase 12 stays. A program name that contains `DBW`, `LGWR`, `RVWR`, or `ARCH` is also shown as a short pill with that name. Any other background session remains a row in the scrolling list. An empty program name stays `The process name was not returned.` The pills do not add a query. `V$BGPROCESS` and `V$PROCESS` stay ungranted.

Data Guard sits under the pills. `V$DATAGUARD_STATS` supplies the returned stats, including apply lag when that name is in the rows. No rows means `Not a Data Guard database.` It does not mean a failed standby.

## 4. Recovery cylinders

These sit in the disk-storage column, under redo.

| Cylinder or line | Reading | Empty result |
|---|---|---|
| Fast recovery area | `V$RECOVERY_FILE_DEST` and `V$FLASH_RECOVERY_AREA_USAGE` | `No recovery area was returned.` The cylinder is not drawn as zero used. |
| Archive | `V$ARCHIVED_LOG` and `V$ARCHIVE_DEST_STATUS`, plus `V$DATABASE` log mode | Destinations still list. Time to failure says `Not collected.` |
| Backup | `V$RMAN_BACKUP_JOB_DETAILS`, latest row | `No RMAN job was returned.` This is not a claim that the database is backed up. |

The recovery cylinder fill is used percent from the destination reading. Archive shows log mode and destination status. Free minutes and time to failure need a rate this phase does not compute, so both say `Not collected.`

A failed backup status is marked with the existing critical status mark. The time is the returned time. A missing time is not replaced with now.

A set `DB_RECOVERY_FILE_DEST` does not prove every file is in the recovery area. `ARCHIVELOG` does not prove a destination is healthy or that a backup exists.

## 5. API

The memory endpoint gains `hit_ratio`, `shared_pool_used`, `in_memory`, `memory_management`, and `data_guard`. The storage endpoint gains `recovery_area`, `archive`, and `backup`. Each new field has its own limitation. A failed shared-pool grant does not clear the pool-size meters. A failed backup reading does not clear the recovery cylinder.

No SQL text is returned. No button starts RMAN, Data Pump, a scheduler job, or a SQL statement.

## 6. Acceptance

- [x] Pool sizes are horizontal meters. The hit ratio and shared-pool used percent are captioned.
- [x] Shared pool size remains visible when `V$SGASTAT` is not granted.
- [x] Known background programs appear as pills without a new view.
- [x] Recovery is a cylinder. Archive does not invent a time to failure. Backup does not invent a success.
- [x] Data Guard with no rows says `Not a Data Guard database.`
- [x] The page has no run or kill control.

## 7. Tests

`test_real_phase15\Verify_Phase15_Real.ps1` reads memory and storage for one real database. `-CheckPrerequisites` does not connect to Oracle. A full run stops listeners on port 8000 and the portal port.

- Adapter test: the shared-pool statement references only `V$SGASTAT`, and the hit-ratio statement references only `V$SYSSTAT`.
- API test: a denied `V$SGASTAT` reading still returns buffer-cache size.
- API test: an empty RMAN result returns the no-job limitation and still returns the recovery area.
- Portal test: a failed backup is marked and no run button is present.

## 8. Out of scope

The top rate strip, ASH, AWR, ADDM, result-cache statistics, and any control that changes the database. Enabling ARCHIVELOG, starting a backup, and opening a standby are changes and are not sent.
