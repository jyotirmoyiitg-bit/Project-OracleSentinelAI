# Phase 21 — Fleet Copilot

**Phase:** 21
**Status:** Implemented
**Prerequisites:** [Phase 20 — Security, navigation, and copilot coverage](Phase_20_Security_Navigation_Copilot.md)  
**Next phase:** [Phase 22 — Agents](Phase_22_Agents.md)

## 1. Overview

The copilot now has two versions. The **fleet copilot** answers one question about every registered database, with the local model writing the answer. The **database copilot** keeps the readings for one database. Each version offers only the questions that apply to it. There is no migration and no new Oracle grant. Nothing in this phase changes an Oracle database.

## 2. Fleet questions

| Question | Tool | Source |
|---|---|---|
| Which databases have reported an incident? | `get_fleet_incidents` | Stored incidents: open ones, and any in the last 7 days |
| Which databases run `x_run.exe`, `lcotp.exe`, or `java.exe`? | `find_fleet_programs` | Live: user sessions grouped by client program on every database |
| Which databases have a datafile larger than 10 GB? | `find_fleet_datafiles` | Live: largest datafile and total size per tablespace on every database |
| Which databases violate organizational policy? | `get_fleet_compliance` | Stored compliance results, latest per policy |
| Which databases have a tablespace above 85 percent? | `get_fleet_tablespaces` | Stored tablespace percent used |
| Which databases are down or unhealthy? | `get_fleet_health` | Stored health, last seen, open incidents |

## 3. Rules

- **No operator text reaches Oracle.** A program name or a size from the question is used only to filter rows in Python after a fixed statement has returned. The two live statements read `V$SESSION` grouped by program and `DBA_DATA_FILES` grouped by tablespace. Both views were already granted.
- **Live fleet reads are bounded.** At most `AI_FLEET_MAX_DATABASES` (25) enabled databases are read for one question, `AI_FLEET_CONCURRENCY` (4) at a time, each with a deadline of `AI_FLEET_DATABASE_TIMEOUT_SECONDS` (45). Databases beyond the limit are stated as not read.
- **Unread is unknown.** A database that could not be read is listed as not known. It is never counted as clean.
- **Routing.** A fixed router handles incident, program, datafile, tablespace, policy, and health questions. When it has no match and a model is available, the model may nominate one fleet tool with its arguments. The server accepts only a listed tool, and only program names that appear in the question. `AI_ROUTER_FALLBACK=false` turns nomination off.
- **The model writes the answer.** The tool rows go to the model with an instruction to answer only from them, name each matching database, and say how many were checked. With no model, or when the model fails, the tool summaries are the answer. Either way the per-database rows are shown under the answer.
- **Other fleet-chat questions are unchanged.** An explanation, a company-policy document question, a SQL draft, and a change request do not scan the fleet. A database reading asked in fleet chat still says to open a database.
- **Roles.** Every signed-in role may ask a fleet question. Program names, session counts, tablespace names, and sizes are returned. File paths, usernames, and SQL text are not.
- Every fleet tool call is stored in `tool_executions`, and the question is in the audit trail as `chat.question`.

## 4. Guided questions

| Chat | Offered |
|---|---|
| Fleet | Health, Incidents, Programs, Storage, Policy, and Knowledge groups. No database reading is offered. |
| Database, incident | The common database questions and the catalog groups. No fleet question is offered. |

Changes to the database list:

- Ten catalog questions that can only answer "not collected" are no longer offered as buttons: session baseline, SQL regressions, load baseline, metric changes, TEMP consumers, schema storage, unusable indexes, optimizer statistics, parameter changes, and segment growth. Typing one still returns the honest unsupported answer.
- Database users, Control files, and Largest segments are offered only to an admin operator, who is the only role that can use them.
- Redo logs, Control files, Background processes, and Connected programs were added to the common questions.

## 5. Portal

- **Fleet copilot** (top bar, Copilot): its own title and description, a "Fleet in scope" card with the number of databases and their health counts, the fleet question groups, two finders, example questions, and "Fleet findings" as a table with one database per row.
- **Finders.** "Program or executable running" takes names such as `x_run.exe, lcotp.exe, java.exe`. "Datafile larger than (GB)" takes a size. Each builds the plain-language question and asks it.
- **Database copilot** (the Copilot tab of a database): titled Database copilot, with the active-database block and the database question groups. It has no finders.
- The scope switch still moves between the fleet and any database.

## 6. API

- `POST /api/v1/chat/threads` and every answer return `guided_commands` for the thread's scope and the caller's role.
- A fleet answer has `answer.kind` `fleet`, the per-database lines in `answer.reading`, and the fleet limitation in `answer.limitations`.
- New tools in the function list: `get_fleet_incidents`, `get_fleet_compliance`, `get_fleet_tablespaces`, `find_fleet_programs`, `find_fleet_datafiles`. `get_session_programs` and `get_datafile_sizes` are the per-database readings they use, also available on a database chat.
- New settings: `AI_FLEET_MAX_DATABASES`, `AI_FLEET_CONCURRENCY`, `AI_FLEET_DATABASE_TIMEOUT_SECONDS`, editable in Admin, Language model.

## 7. Acceptance

- [x] The four example questions each run the matching fleet tool and name the right databases.
- [x] A program name with sessions but none active is reported as connected but idle; a name found nowhere is reported as not found.
- [x] The size in a datafile question is applied; megabytes and terabytes are converted.
- [x] An unreachable database is reported as not known, and the per-question limit is stated.
- [x] Fleet chat offers only fleet questions; database chat offers only database questions that can answer.
- [x] An explanation, a document question, a draft, and a change request do not scan the fleet.
- [x] With the local model, `qwen2.5-coder:14b` answered the four questions correctly from synthetic databases in 2 to 7 seconds each.
- [ ] The full live check is recorded only after `Verify_Phase21_Real.ps1` is run against Oracle. A written spec is not a pass.

## 8. Tests

- `backend/tests/ai/test_phase21_fleet.py`: 11 fleet phrasings and 5 non-fleet phrasings; program-name cleaning and nomination checks; scope-specific question lists; incidents, compliance, and tablespaces from stored rows; programs and datafiles from every database; an unread database and the limit; the model writing the answer; model nomination on and off; no scan for explanations and drafts; the chat API returning the list for the scope.
- `frontend/src/CopilotPage.test.tsx`: the fleet page with its card, groups, finders, examples, and findings table; the database page without finders.
- Live check: `test_real_phase21\Verify_Phase21_Real.ps1`. `-CheckPrerequisites` does not connect to Oracle. It asks the fleet questions as a temporary operator, requires the selected database to be among those read live, and reports whether the model stored in Admin wrote an answer. `-RequireModel` fails the run unless the local model did. The full run is a pass only after it is actually run.

## 9. Out of scope

Fleet questions about sessions by user or machine, fleet top SQL, a fleet-wide wait comparison, caching a fleet scan between questions, and scheduled fleet reports.
