# Phase 15 Implementation Summary — SGA Meters and Recovery

Later work is in [Phase 16 — Rate meters and refresh](./Progress_Summary_Phase16.md).

**Status:** The SGA column has labeled meters, background pills, and Data Guard. Disk storage adds the recovery cylinder, archive lines, and the latest backup. The live launcher was added. `-CheckPrerequisites` passed. The full Oracle run was not performed.<br>
**Phase specification:** [Phase 15 — SGA meters and recovery](../Phase_15_GraphDB_Management.md)<br>
**Previous summary:** [Phase 14 — Service, host, and server processes](./Progress_Summary_Phase14.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head remains `0010`. Privileged execution stays disabled. No table or view was created in `GSYSTEM`.

## What Was Implemented

The memory response gains `hit_ratio`, `shared_pool_used`, `in_memory`, `memory_management`, `data_guard`, and `result_cache`. The storage response gains `recovery_area`, `archive`, and `backup`. Each reading fails on its own.

- **Pool meters.** Existing `V$SGAINFO` sizes stay horizontal meters. The buffer-cache hit ratio uses `V$SYSSTAT` and is captioned since instance startup. A missing statistic stays `Not collected.` and is not drawn as 100 percent.
- **Shared pool used.** `(shared pool size - free memory) / shared pool size` from the new `SELECT` on `SYS.V_$SGASTAT`. Until that grant works, the size meter stays and the used meter says `V$SGASTAT is not granted.`
- **Memory management.** `sga_target` above zero says Automatic Shared Memory Management is on. `memory_target` above zero says Automatic Memory Management is on. A missing parameter says the target parameter was not returned. An in-memory row with no bytes says the in-memory area is not in use. Result cache stays `Not collected.`
- **Background pills.** A program containing `DBW`, `LGWR`, `RVWR`, or `ARCH` is also shown as that short pill. The scrolling list stays. No new view is queried. `V$BGPROCESS` and `V$PROCESS` stay ungranted.
- **Data Guard.** Returned `V$DATAGUARD_STATS` rows, including apply lag when that name is present. No rows says `Not a Data Guard database.`
- **Recovery.** A cylinder from `V$RECOVERY_FILE_DEST`, with file types from `V$FLASH_RECOVERY_AREA_USAGE`. No destination says `No recovery area was returned.` and the cylinder is not drawn as zero used.
- **Archive.** Log mode, destination status, and the latest archived log when one was returned. Free minutes and time to failure stay `Not collected.`
- **Backup.** The latest `V$RMAN_BACKUP_JOB_DETAILS` row. No row says `No RMAN job was returned.` A failed status uses the existing critical mark. The page has no run or backup control.

`SELECT` on `SYS.V_$SGASTAT` was added to `scripts/oracle/02_grant_readonly_monitoring.sql` and to the revoke list. The script stays rerunnable and does not change the password or the account lock.

## Live launcher

`test_real_phase15\Verify_Phase15_Real.ps1` follows the Phase 14 launcher and then reads the SGA meters and the recovery fields.

```powershell
.\test_real_phase15\Verify_Phase15_Real.ps1 -CheckPrerequisites
.\test_real_phase15\Verify_Phase15_Real.ps1 -TnsAlias PDBORCL -Interactive
```

A full run stops whatever is already listening on port 8000 and the portal port (5173 by default). It does not stop a listener on port 8765. The language model stays off. Counts and states are printed. File paths and segment names are not printed.

## Verification

- `backend/tests/graph/test_memory.py`: the hit-ratio statement uses only `V$SYSSTAT`, the shared-pool statement uses only `V$SGASTAT`, and a denied shared-pool reading still returns the buffer cache.
- `backend/tests/api/test_graph.py`: the same denial is returned by the memory route, and an empty RMAN result keeps the recovery area.
- `backend/tests/graph/test_storage.py`: a denied backup reading leaves the recovery cylinder, and archive time stays not collected.
- `frontend/src/GraphPage.test.tsx`: a failed backup is marked critical, and there is no run or backup button. **4 passed**.
- The GraphDB backend tests for memory, storage, activity, the API, and the grant scripts: **59 passed**.
- `test_real_phase15\Verify_Phase15_Real.ps1 -CheckPrerequisites` passed. The full Oracle launcher was not run.

## Resume / Next-Agent Pickup

1. Restart the API before expecting the new meters in a portal that was already running.
2. Run `scripts/oracle/02_grant_readonly_monitoring.sql` as SYSDBA in the target PDB so shared-pool used percent can be read. The other Phase 15 readings use grants that were already in the script.
3. Do not grant `V$SYSMETRIC`, `V$BGPROCESS`, `V$PROCESS`, or `V$RESULT_CACHE_STATISTICS`.
4. Do not record the full live check as passed until `Verify_Phase15_Real.ps1` is run against Oracle.
5. Phase 16 adds the rate meters from two `V$SYSSTAT` samples and refreshes every column. It does not add another grant.
6. These edits are uncommitted. Commit only when requested.
