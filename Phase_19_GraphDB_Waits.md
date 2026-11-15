# Phase 19 — GraphDB Wait Stats

**Phase:** 19
**Status:** Implemented
**Prerequisites:** [Phase 18 — Structured AI answers](Phase_18_AI_Response_Formatting.md), [Phase 16 — Rate meters and refresh](Phase_16_GraphDB_Live.md)  
**Next phase:** [Phase 20 — Security, navigation, and copilot coverage](Phase_20_Security_Navigation_Copilot.md)

## 1. Overview

GraphDB keeps the existing columns. **WaitDB** is its own page. It asks for one registered database, then opens the non-idle wait events for that database. The table follows the event-waits layout: event, total waits, waits/s, percent of waits, total wait time, wait time/s, percent of time, and average wait. A chart shows milliseconds waited per second by wait class. The same page also shows locking, blocking, parsing, and buffer busy waits.

## 2. Rules

- Events come from `V$SYSTEM_EVENT` where `wait_class` is not `Idle`, at most 15, ordered by `time_waited_micro`. CPU time is `DB CPU` from `V$SYS_TIME_MODEL`. Both views are already granted. This phase adds no grant.
- Totals are since instance startup. Waits/s, wait time/s, and the chart are the difference between two responses on this page. The first response says a second sample is required.
- A counter that goes backwards, a missing value, or a gap under one second leaves that rate at `Not collected.` The other rows still render.
- Samples stay in the page. They are not written to PostgreSQL and are not shared between browsers or databases.
- The chart is not an Oracle historical chart. ASH, AWR, ADDM, `V$SYSMETRIC`, and `DBA_HIST_*` are not queried.
- The existing copilot wait reading, which returns eight events, is unchanged.
- A failed waits reading does not clear the GraphDB columns.

## 3. Acceptance

- [x] WaitDB lists non-idle events and CPU time for the selected database.
- [x] Rate columns and the chart stay empty until a second sample on this page.
- [x] An operator can read the waits endpoint. A missing or disabled database returns 404.
- [x] `Verify_Phase19_Real.ps1 -CheckPrerequisites` passes without Oracle.
- [ ] The full live check is recorded only after it is run. A written spec is not a pass.

## 4. Live check

Add `test_real_phase19\Verify_Phase19_Real.ps1`, following the Phase 16 launcher.

1. `-CheckPrerequisites` checks Python, Node, and Docker and does not connect to Oracle.
2. The full run upgrades Alembic to the current head, signs in as the local administrator, and reuses the selected TNS alias.
3. It calls waits for that database.
4. It checks that waits return non-idle events or a named limitation, and CPU time or a named limitation.
5. It checks that an operator can read waits, and that a missing database returns 404.
6. It does not print the cookie, the Oracle password, a connection string, event names, or SQL text.
7. `-Interactive` opens `#/wait/databases/{id}`.

The language model stays off for this run. GraphDB does not need it.

## 5. Out of scope

A stored wait history, a forecast, and any control that changes the database.
