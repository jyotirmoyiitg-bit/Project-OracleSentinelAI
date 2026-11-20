# Phase 28 — SessionDB

**Phase:** 28
**Status:** Implemented
**Prerequisites:** [Phase 25 — Database sections](Phase_25_Database_Sections.md), [Phase 27 — Tablespace timeline](Phase_27_Tablespace_Timeline.md)

## 1. Overview

SessionDB is a new workspace tab that lists the user sessions of one database, grouped by the program that opened them, the way a desktop session browser does. When a session is chosen, the panel below shows the statement it is running, or the last one it ran when it is idle, and the plan Oracle cached for that statement. The user sent three session browser screenshots as the reference: the session list (LCOTP.EXE with two sessions, TOAD.EXE with two), the Current Statement tab showing a formatted `DELETE`, and the Explain Plan tab showing the cached plan.

The tab sits after WaitDB: Overview, GraphDB, WaitDB, **SessionDB**, MemoryDB, StorageDB, ParameterDB, AlertDB, Events, Copilot. Its address is `#/sessions/databases/{id}`.

No migration and no new setting. One new Oracle grant, `SELECT ON SYS.V_$SQL_PLAN`, for the plan. The page is read-only: it cannot end, kill, or trace a session, and it never explains a statement again.

## 2. The session list

`get_session_list` is one fixed `SELECT` on `V$SESSION`:

| Column shown | From `V$SESSION` |
|---|---|
| Program / user name | `PROGRAM` (group), `USERNAME` (row) |
| OS user | `OSUSER` |
| Terminal | `TERMINAL`, or `MACHINE` when the terminal is empty or `unknown` |
| SID | `SID` (the serial number `SERIAL#` is read too and identifies the row) |
| Status | `STATUS` |
| Module, Action | `MODULE`, `ACTION` |
| Logon time | `LOGON_TIME` |
| Last call | `SYSDATE - LAST_CALL_ET`; the hover text says how long ago |
| SQL_ID | `SQL_ID` |

- User sessions only (`TYPE = 'USER'`). Background processes are left out.
- The session that runs the reading is left out, so the page does not list itself.
- At most 500 sessions. When the cap is reached the page says some may be missing.
- Text columns are cut to 64 characters. Times are the database server's clock.

The page groups by program, upper-cased like the reference, with each group's count and the number active. Groups fold with a click; "Collapse all" and "Expand all" act on every group. Active sessions are green. There are figures for sessions, active, and programs, a status filter, and a text filter over program, user, OS user, terminal, module, action, SID, and SQL_ID. The list refreshes with the portal's refresh setting.

## 3. The statement and its cached plan

Choosing a row (click, Enter, or Space) opens the panel under the list, like the lower half of the reference tool.

**Statement tab.** The statement the session is running. For an idle session it is the last statement it ran, labelled "Last statement", with the note "The session is idle. This is the last statement it ran." The text is shown with line numbers. "Format statement" (on by default) starts each main clause on its own line and indents `AND`/`OR`. It never changes text inside quotes or comments, or the case of any word, and leaves statements that already have line breaks as written. A Copy button copies the text shown. Under the text: SQL_ID and child number, plan hash value and optimizer mode, executions and total elapsed time, buffer gets and disk reads, and rows processed.

**Explain plan tab.** The cached explain plan: the plan Oracle used, read from `V$SQL_PLAN`. Each step shows its Id, the operation and options indented by depth, the object, the optimizer, cost, rows (cardinality), and bytes. A full table access is shown in red.

The panel reads again when the list refreshes, and "Read again" reads it at once.

**How it is read.** Two fixed statements run on one Oracle session:

| Reading | Binds | What it reads |
|---|---|---|
| `get_session_statement` | the SID and serial number the list returned | `V$SESSION` joined to `V$SQL`: `SQL_ID`, or `PREV_SQL_ID` when idle, with its child number, `SQL_FULLTEXT`, and the statistics above |
| `get_statement_plan` | the SQL_ID and child number the first statement returned | `V$SQL_PLAN` for that one cursor |

- These are the first readings with bind variables. Only a whole number, or a SQL_ID written exactly as Oracle writes it (13 lower-case letters and digits), can be bound. Each value is checked before it is bound, and text from a person or the model is never bound or joined into a statement. A reading that takes binds refuses to run without them.
- Binding the serial number as well as the SID means a SID that Oracle has given to a new session finds nothing: "This session has ended, or its SID now belongs to another session. Refresh the list."
- The plan is read for the same SQL_ID and child as the text, so the two always belong together.
- Nothing is explained again: no `EXPLAIN PLAN`, no plan table, no statement run on the user's behalf.
- The statement is kept up to 20,000 characters with its line breaks. Longer text is cut and the page says so.
- A statement that has aged out of the shared pool shows its SQL_ID and the note that its text cannot be shown.
- Without the `V$SQL_PLAN` grant the statement is still shown, and the plan tab says "Grant SELECT on SYS.V_$SQL_PLAN to the monitoring account to see the plan."

## 4. Who sees what

| | Administrator | Every other role (DBA, operator, viewer) |
|---|---|---|
| Session list | Everything | Same rows, without user, OS user, and terminal; client host removed from program and module |
| Statement and plan | Yes | Refused with HTTP 403: "The statement of a session and its plan are shown to an administrator." The page says the same and does not ask |

This follows the standing rules that user names, SQL text, and object names are shown to an administrator only. A statement holds literal values from the application, and a plan names tables and indexes.

## 5. Oracle grant

Phase 28 adds one line to `scripts/oracle/02_grant_readonly_monitoring.sql`:

```sql
GRANT SELECT ON SYS.V_$SQL_PLAN TO GSYSTEM;
```

Run it as SYSDBA in each monitored PDB or non-CDB. `03_verify_gsystem.sql` checks every new column of `V$SESSION`, `V$SQL`, and `V$SQL_PLAN`, and `04_revoke_gsystem.sql` lists the new view.

## 6. Live check

`test_real_phase28\Verify_Phase28_Real.ps1` starts the live stack like the earlier launchers, then:

1. Opens one Oracle session of its own as the monitoring account, tags it with the module "Sentinel Phase 28 check", and runs a marked `SELECT ... FROM dual` as its last statement.
2. SessionDB must list that session with its program, SID, serial number, user, module, logon time, and last call.
3. Its statement must be the marked `SELECT`, shown as the last statement of an idle session, with a SQL_ID.
4. The cached plan is read when the grant is present. Without it a warning names the grant; `-RequirePlan` fails the run instead.
5. A wrong serial number must find nothing, and a request without one must be refused.
6. A temporary operator must see the session without names and be refused the statement. The operator account is removed and the check session closed.

Nothing is changed in Oracle. No user name or SQL text is printed.

```powershell
.\test_real_phase28\Verify_Phase28_Real.ps1 -TnsAlias MY19C -Interactive
```

## 7. Compact table rows

Rows of the SessionDB list and the plan are compact, like the reference tool: 4 px above and below each cell instead of 13 px, and 7 px in the header. At the user's request the same spacing now applies to every table in the portal:

| Where | Tables | Row height (measured in the preview) |
|---|---|---|
| Database sections | SessionDB, the plan, ParameterDB, StorageDB, MemoryDB, WaitDB | 23–29 px |
| Database Overview | Agent settings, tablespaces, compliance checks, observations | 24 px; compliance 40 px (two-line cells, was 54) |
| Fleet and incidents | Fleet list, incident list | 36 px (was 41): the database icon is 26 px instead of 32 inside a table |
| Other pages | Events, Agents runs, Knowledge, Admin configuration, operators, audit, copilot answer tables | Same 4 px cell padding |

Controls inside a row (the agent setting selects, number boxes, and Save buttons) have 3 px vertical padding so they do not stretch the row. In the compliance table the evidence column is at least 200 px wide, so the evidence text stays on one line. Tables that hold long text still wrap.

At the user's request the database Overview now shows the Tablespaces table first, with the Metric timeline and Recovery area below it and Compliance policies after them.

## 8. Not included

- Ending or killing a session, tracing, or any other change.
- Running `EXPLAIN PLAN` for a statement that has no cached plan.
- Waits, locks, open cursors, and session statistics tabs of the reference tool. Blocking sessions are already on WaitDB.
- The copilot and the agents do not read sessions or statements.

## 9. Verification

| Check | Result |
|---|---|
| Backend | 607 passed, 1 skipped. `backend/tests/graph/test_phase28_sessions.py` (13 tests): read-only statements, the bind rule, grouping, terminal fallback, cap, operator trimming, limitations, current and last statement, aged-out text, missing plan grant, endpoints by role |
| Frontend | 90 passed, including `frontend/src/Phase28.test.tsx` (10 tests); TypeScript clean |
| Ruff | Changed and new files clean, including the launcher runtime |
| Live Oracle, list | MY19C: LCOTP.EXE (2) and TOAD.EXE (2), as in the reference |
| Live Oracle, statement | MY19C: every listed session returned its last statement; SID 246 returned the `DELETE` shown in the reference. The plan reported the missing `V$SQL_PLAN` grant, as designed |
| Launcher core, in process | Against MY19C: the tagged session was listed with its module and user, its marked statement was shown with its SQL_ID, a wrong serial found nothing, and the operator view showed no names |
| Launcher | Parses. Not run end to end here, because it stops the services on ports 8000 and 5173 |
| Browser | Synthetic preview of the list as a non-administrator |
