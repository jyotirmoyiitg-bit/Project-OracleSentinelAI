# Phase 16 — Rate Meters, Refresh, and Live Check

**Phase:** 16  
**Status:** Implemented  
**Prerequisites:** [Phase 15 — SGA meters and recovery](Phase_15_GraphDB_Management.md)  
**Next phase:** [Phase 17 — GraphDB for every operator](Phase_17_GraphDB_Access.md)

## 1. Overview

Add the horizontal rate meters across the top of the center column, refresh every column from the readings already defined, and add the live check. This phase does not add a new Oracle feature and does not query ASH, AWR, or ADDM.

## 2. Rate meters

The reference shows block, redo, parse, and execute rates. Those rates are the difference between two collections of `V$SYSSTAT` for this database:

- physical reads
- redo entries
- parse count (total)
- execute count

`V$SYSSTAT` is already granted. The first response for a database has no previous sample, so each meter says `Rate needs a second sample.` Refresh stores the new sample in the page and fills the meter with `(new value - previous value) / seconds between the two responses`. The caption says the rate is since the previous refresh on this page. It is not an Oracle historical metric.

A counter that goes backwards, a missing statistic, or a gap of less than one second leaves that meter at `Not collected.` The other meters still render. The samples are not written to PostgreSQL and are not shared between browsers or between databases.

## 3. Refresh

- One Refresh control reloads memory, storage, and activity. It does not send one SQL statement that joins every view.
- A card that was already collected keeps its previous snapshot, with the collection time, until the new reading returns.
- A reading that fails is replaced by its limitation. The other cards stay.
- Automatic refresh uses the portal interval already in the page header. Closing GraphDB stops that page's requests.
- Two administrators looking at different databases do not share a response or a rate sample.

## 4. Grants

`SYS.V_$SGASTAT` is granted by `scripts/oracle/02_grant_readonly_monitoring.sql`, together with `SYS.V_$CONTROLFILE`, `SYS.V_$LOG`, `SYS.V_$LOGFILE`, and `SYS.DBA_SEGMENTS`. The script stays rerunnable and does not change passwords or account locks. A target that has not been granted still renders the cards whose views are already granted. Phase 16 does not add another grant.

## 5. Live check

Add `test_real_phase16\Verify_Phase16_Real.ps1`, following the Phase 12 launcher.

1. `-CheckPrerequisites` checks Python, Node, and Docker and does not connect to Oracle.
2. The full run upgrades Alembic to the current head, signs in as the local administrator, and reuses the selected TNS alias.
3. It calls memory, storage, and activity for that database.
4. It checks that memory returns SGA or a named limitation, storage returns data files or a named limitation, and activity returns the service card with response and average active set to `Not collected.`
5. It checks that an operator session receives 403 on each endpoint.
6. It does not print the cookie, the Oracle password, a connection string, or SQL text.
7. `-Interactive` opens `#/graph/databases/{id}`.

The language model stays off for this run. GraphDB does not need it.

## 6. Acceptance

- [x] The rate meters stay empty until a second sample exists for the same database.
- [x] Refresh updates memory, storage, and activity, and a failed storage card does not clear SGA.
- [x] The live check passes `-CheckPrerequisites` without Oracle.
- [ ] The full live check is recorded only after it is run. A written spec is not a pass.

## 7. Tests

- Portal test: the first load says a second sample is required, and the second load shows a rate only for a counter that increased.
- Portal test: Refresh issues the three reads and keeps a failed storage card from clearing memory.
- API test: an operator receives 403 on memory, storage, and activity.
- PowerShell parser check for `Verify_Phase16_Real.ps1` before any Oracle run.

## 8. Out of scope

A historical chart, a forecast, ASH, AWR, ADDM, `V$SYSMETRIC`, and any control that changes the database. Growth history remains the existing capacity view.
