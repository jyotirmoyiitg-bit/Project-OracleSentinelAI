# Phase 14 — Service, Host, and Server Processes

**Phase:** 14  
**Status:** Implemented  
**Prerequisites:** [Phase 13 — Layout and disk storage](Phase_13_GraphDB_Physical.md)  
**Next phase:** [Phase 15 — SGA meters and recovery](Phase_15_GraphDB_Management.md)

## 1. Overview

Fill the two left columns of the performance-home layout, and add the largest-segment list under the database-files cylinder. Counts are current readings. A missing row stays `Not collected.` It is not drawn as zero.

## 2. Service card

| Line | Reading | When it is not collected |
|---|---|---|
| Uptime | `V$INSTANCE` startup time | The startup time was not returned |
| Database name | `V$DATABASE` name | The database name was not returned |
| Pluggable database | `V$PDBS` name for this connection | No PDB row was returned. A non-CDB shows `Not a pluggable database.` |
| Total users | `V$SESSION` where `TYPE = 'USER'` | The session reading failed |
| Active users | Those user sessions whose status is `ACTIVE` | The session reading failed |
| Response | No granted reading | Always `Not collected.` |
| Average active | No granted reading | Always `Not collected.` |

Response and average active in the reference come from a one-minute metric or from ASH. Neither is granted here. The lines stay so the card matches the layout. They do not get a number from `V$SYS_TIME_MODEL` or from a guess.

## 3. Host card

`V$OSSTAT` is already granted.

- **CPU.** A donut. The used share is `BUSY_TIME / (BUSY_TIME + IDLE_TIME)` when both rows exist. The caption says the ratio is since instance startup, not the last minute.
- **Run queue.** Shown only when `V$OSSTAT` returns `LOAD`. Otherwise the line says the host did not return that statistic.
- **Free memory.** Shown only when `V$OSSTAT` returns `FREE_MEMORY_BYTES`. Otherwise the line says the host did not return that statistic.

There is no virtualization or ESX line. Those values are not an Oracle reading for this database.

## 4. Server processes

The column already shows PGA target and used. This phase adds one oval per count:

| Oval | Source |
|---|---|
| Dedicated | `V$SESSION` where `SERVER = 'DEDICATED'` and `TYPE = 'USER'` |
| Shared | `V$SESSION` where `SERVER = 'SHARED'` and `TYPE = 'USER'` |
| Dispatchers | Background sessions whose program name contains `D000` |
| Parallel query | Background sessions whose program name contains `(P0` or `(P1` |
| Job queue | `DBA_SCHEDULER_RUNNING_JOBS` row count |
| Total | All sessions returned by the fixed session count |

There is no Enterprise Manager agent oval. This portal is not Enterprise Manager.

The SQL is fixed. An empty program name is not counted as a dispatcher or a parallel query slave. A failed session reading leaves PGA visible and names `SYS.V_$SESSION` on the ovals.

## 5. Segments

`get_largest_segments` reads `DBA_SEGMENTS`, at most 20 rows, ordered by bytes descending, with no operator bind. `SELECT` on `SYS.DBA_SEGMENTS` is already in the grant script. The list sits under the database-files cylinder: owner, segment name, type, tablespace, and size. The heading says these are the 20 largest segments, not a full inventory. Until the grant works on the target, the list says the segment inventory is not available and the cylinder stays.

`V$TEMPSEG_USAGE` stays ungranted. The page does not name a per-session temp consumer.

## 6. API

`GET /api/v1/databases/{id}/graph/activity`

Administrator only. Response fields: `service`, `host`, `server_processes`, `collected_at`, and `limitations`.

The storage endpoint gains `segments`. A segment denial does not clear `data_files`.

## 7. Acceptance

- [x] Service shows uptime, database name, PDB or the non-CDB sentence, and the two user counts.
- [x] Response and average active say `Not collected.`
- [x] The CPU donut is labeled since instance startup. A missing load or free-memory statistic is named, not drawn as zero.
- [x] The process ovals match the fixed counts. There is no agent oval.
- [x] The segment list has at most 20 rows.

## 8. Tests

- Adapter test: the new session SQL filters `TYPE` and `SERVER` and does not query `V$BGPROCESS` or `V$PROCESS`.
- Adapter test: the segment statement has a fixed limit of 20 and references only `DBA_SEGMENTS`.
- API test: a denied session reading still returns PGA from the memory endpoint.
- Portal test: response and average active render the not-collected sentence.
- Live check: `test_real_phase14\Verify_Phase14_Real.ps1`. `-CheckPrerequisites` does not connect to Oracle. The full run is a pass only after it is actually run.

## 9. Out of scope

ASH, AWR, ADDM, `V$SYSMETRIC`, and any host metric the operating system did not return. Killing a session is a change and is not sent.
