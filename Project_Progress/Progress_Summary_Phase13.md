# Phase 13 Implementation Summary — GraphDB Layout and Disk Storage

Later work is in [Phase 14 — Service, host, and server processes](./Progress_Summary_Phase14.md).

**Status:** The GraphDB page is four columns. Disk storage shows data-file and redo cylinders, plus the control-file list, for the selected database. The live launcher was added. `-CheckPrerequisites` passed. The full Oracle run was not performed.<br>
**Phase specification:** [Phase 13 — Layout and disk storage](../Phase_13_GraphDB_Physical.md)<br>
**Previous summary:** [Phase 12 — Memory structures](./Progress_Summary_Phase12.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head remains `0010`. Privileged execution stays disabled. No table or view was created in `GSYSTEM`.

## What Was Implemented

The page for one database is four columns, in the performance-home order, using the portal colors:

| Column | This phase |
|---|---|
| Service and host | Each value says `Not collected in this phase.` |
| Server processes | PGA target and used size from the Phase 12 memory reading. The count ovals say `Not collected in this phase.` |
| SGA and background processes | The Phase 12 pool bars and process list |
| Disk storage | Data-file cylinder, temp files labeled Temp, control-file list, and redo cylinder |

`GET /api/v1/databases/{id}/graph/storage` is administrator-only. It returns `data_files`, `temp_files`, `control_files`, `redo_logs`, `collected_at`, and `limitations`.

- **Data files.** Online file count, online tablespace count, used bytes, and used percent. Used bytes are `DBA_DATA_FILES` minus `DBA_FREE_SPACE`. The cylinder fill is that percent. Offline files are marked. The file names scroll inside the card.
- **Temp files.** Listed from `DBA_TEMP_FILES` and labeled Temp. Their online count is included with the data files.
- **Control files.** Name and status from `V$CONTROLFILE`. They are a list, not a cylinder. An empty status says the status was not returned.
- **Redo logs.** Group count and total size from `V$LOG`. Member paths from `V$LOGFILE`. The cylinder fill is the share of groups that are current or active. The current group is marked. Paths are text and are not executed.
- **Flash cache.** The sentence `Not collected.`

A failed grant names its view and the other lists stay. An empty control-file or redo result does not draw a cylinder as if the database had none. The response does not include SQL. A missing or disabled database returns 404. An operator receives 403.

`SELECT` on `SYS.V_$CONTROLFILE`, `SYS.V_$LOG`, and `SYS.V_$LOGFILE` is in `scripts/oracle/02_grant_readonly_monitoring.sql`. The target PDB still needs that script applied. Data files, temp files, and free space were already granted. `DBA_SEGMENTS` remains in the script for Phase 14 and is not queried here.

## Live launcher

`test_real_phase13\Verify_Phase13_Real.ps1` follows the Phase 12 launcher and then reads disk storage for the selected database.

1. Check Python 3.14, Node, and Docker. `-CheckPrerequisites` stops there and does not connect to Oracle.
2. Load the project-root `.env`, then `test_real_phase13\.env`. The phase file keeps sign-in on, the language model off, and both execution flags false.
3. Start Docker PostgreSQL and upgrade Alembic to head `0010`.
4. Stop a listener already on port 8000 or the portal port, then start the API.
5. Sign in with the local bootstrap account. The password stays in the root `.env`.
6. Reuse or register the selected TNS alias and test the Oracle connection.
7. Read disk storage for that database. Data files, control files, and redo logs must be present or replaced by a named limitation. Flash cache must say `Not collected.` Sizes that are present must be labeled in MB or GB. An operator receives 403. A missing database returns not-found. The response must not contain SQL. Counts are printed. File paths are not printed. The temporary `phase13-operator` account is removed. Audit rows are not deleted.
8. Start the portal. `-Interactive` opens `#/graph/databases/{id}`.

```powershell
.\test_real_phase13\Verify_Phase13_Real.ps1 -CheckPrerequisites
.\test_real_phase13\Verify_Phase13_Real.ps1 -TnsAlias PDBORCL -Interactive
```

Run `scripts/oracle/02_grant_readonly_monitoring.sql` as SYSDBA in the target PDB before the full launcher if that PDB does not already have the control-file and redo grants.

A full run stops whatever is already listening on port 8000 and the portal port (5173 by default). It does not stop a listener on port 8765.

## Verification

- `backend/tests/graph/test_storage.py`: the storage statements use only the granted views, and a missing control-file or redo grant leaves the data-file list.
- `backend/tests/api/test_graph.py`: an administrator receives the storage groups, flash cache is not collected, an operator receives 403, and a missing database returns 404.
- `backend/tests/test_oracle_permission_scripts.py`: the control-file, redo, and segment grants stay in the grant script. The verify script does not have to query those GraphDB-only views.
- `frontend/src/GraphPage.test.tsx`: the four columns render, an offline data file and the current redo group are marked, and a file path is not a link. **4 passed**.
- The GraphDB backend tests for memory, storage, the API, and the grant scripts: **50 passed**.
- `test_real_phase13\Verify_Phase13_Real.ps1 -CheckPrerequisites` passed. The full Oracle launcher was not run.

## Resume / Next-Agent Pickup

1. Run the grant script in the target PDB, then restart the API before opening GraphDB.
2. Sign in as the administrator. Service, host, and the process-count ovals stay uncollected until Phase 14.
3. Do not create a table or a view in `GSYSTEM`. Do not grant `V$SYSMETRIC`, `V$BGPROCESS`, or `V$PROCESS`.
4. Do not record the full live check as passed until `Verify_Phase13_Real.ps1` is run against Oracle.
5. Phase 14 fills service, host, and the server-process ovals, and adds the 20 largest segments.
6. These edits are uncommitted. Commit only when requested.
