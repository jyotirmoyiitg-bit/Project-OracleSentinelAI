# Phase 21 Implementation Summary — Fleet Copilot

**Status:** The copilot has a fleet version and a database version. The fleet copilot answers which databases have incidents, run named programs, hold large datafiles, exceed a tablespace percent, or fail a policy, and the local model writes the answer from the tool rows. Each version offers only its own questions. The four example questions were checked with `qwen2.5-coder:14b` on synthetic databases. No run against real Oracle databases was performed, and no live launcher was written for this phase.<br>
**Phase specification:** [Phase 21 — Fleet copilot](../Phase_21_Fleet_Copilot.md)<br>
**Previous summary:** [Phase 20 — Security, navigation, and copilot coverage](./Progress_Summary_Phase20.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head remains `0010`. No Oracle grant was added. Privileged execution stays disabled.

## What Was Implemented

### Fleet tools

- `backend/src/sentinel/ai/tools/fleet.py`: `fleet_incidents`, `fleet_compliance`, and `fleet_tablespaces` read stored rows. `fleet_programs` and `fleet_datafiles` format a live scan. Each returns one line per matching database, then a line for the databases with no match, then a line for databases that could not be read.
- `backend/src/sentinel/ai/fleet.py`: `fleet_requests` is the fixed router, `nominated_request` validates a model nomination, and `scan_fleet` runs one fixed reading on every enabled database with a concurrency limit, a per-database deadline, and a per-question cap.
- `backend/src/sentinel/adapters/oracle/readings.py`: two aggregate statements, `session_programs` (user sessions grouped by client program, with active count) and `datafile_sizes` (per tablespace: file count, largest datafile, total). They are also database readings, `get_session_programs` and `get_datafile_sizes`.
- A program name or size from the question never reaches SQL. It filters rows in Python.
- For program questions the tool states, for each name, whether it has an active session, is connected but idle, or was not found, so "active" is not guessed.

### Orchestrator

- `Orchestrator._answer_fleet` in `backend/src/sentinel/ai/orchestrator.py` runs before the usual route when the thread scope is fleet. It returns nothing for a draft, a change, an explanation, or a document question, so those follow the existing paths.
- `_nominate_fleet` asks the model for one fleet tool when the fixed router has no match. A program name the model returns is kept only if it appears in the question.
- The model writes the answer with a fleet instruction: answer only from the rows, name each matching database, say how many were checked, treat unread as unknown. With no model the tool summaries are the answer and the status is `answered`.
- `guided_commands_for(scope_type, role)` replaces the single list. `FLEET_COMMANDS` holds the fleet questions. `catalog_guided_commands` leaves out the ten questions that can only answer "not collected".

### Portal

- `frontend/src/CopilotPage.tsx`: scope-specific title, description, empty state, and placeholder; a "Fleet in scope" card with health counts; `FleetFinder` with a program-names finder and a datafile-size finder; example fleet questions; "Fleet findings" for a fleet answer.
- `frontend/src/components/ReadingTable.tsx`: with `named`, a `Database: finding` line becomes a row headed by the database, and findings separated by a semicolon become a list.
- Styles are in the Phase 21 block at the end of `frontend/src/styles.css`.

## Check with the local model

Synthetic databases, temporary SQLite, Ollama `qwen2.5-coder:14b`, three databases:

| Question | Tool | Model answer | Time |
|---|---|---|---|
| Which database has reported an incident? | `get_fleet_incidents` | Named the two databases with incidents and their severities | 7 s |
| Which database runs `x_run.exe`, `lcotp.exe`, or `java.exe`? | `find_fleet_programs` | `java.exe` active on all three; `x_run.exe` connected but idle; `lcotp.exe` not found | 3 s |
| Which database has a datafile over 10 GB? | `find_fleet_datafiles` | Named all three | 3 s |
| Which database violates organizational policy? | `get_fleet_compliance` | Named the two failing databases and their policies, and the compliant one | 3 s |
| "Is anything from sqlplus hooked up anywhere?" | nominated `find_fleet_programs` | Named all three | 3 s |

The first program answer said no database had an active `x_run.exe` without saying it was connected and idle. The per-name breakdown and a prompt line were added, and the answer above is from the rerun.

## Verification

- `backend/tests/ai/test_phase21_fleet.py`: **28 passed**. Full backend suite: **428 passed, 1 skipped**. Ruff is clean on the changed files.
- `frontend/src/CopilotPage.test.tsx` gained the fleet page test; three existing tests were updated for the new titles. Full frontend suite: **51 passed** in 11 files. `tsc -b` is clean.
- The portal pages were not opened in a browser for this phase. They are covered by the component tests.
- `Verify_Phase21_Real.ps1` parsed, and `-CheckPrerequisites` passed. Its check step was run against a synthetic API on temporary SQLite with the model off and with `qwen2.5-coder:14b` on; both passed, and `--require-model` failed as intended with the model off. The full Oracle launcher was not run.

## Live launcher

`test_real_phase21\Verify_Phase21_Real.ps1` follows the Phase 20 launcher through the real Oracle connection. It then opens a fleet chat and a database chat and compares their question lists, creates the temporary `phase21-operator`, and asks the fleet copilot as that operator: incidents, policy violations, tablespace percent, datafiles over 10 GB, the three named programs, and connected programs. The selected database must be among those read live. Another database that cannot be read is listed as not known and gives a warning, not a failure. An explanation and a typed statement must not read the fleet, a database chat must not run a fleet tool, no answer may contain SQL, and the questions must be in the audit trail. The temporary operator is removed.

The model follows what is stored in Admin, because stored configuration replaces `.env` values at startup. With the model off the tool summaries are checked. With the local model on, one free-form question checks model nomination and is reported as a warning when the model picks nothing. `-RequireModel` fails the run unless the local model wrote a fleet answer. A hosted model writing a fleet answer fails the run.

```powershell
.\test_real_phase21\Verify_Phase21_Real.ps1 -CheckPrerequisites
.\test_real_phase21\Verify_Phase21_Real.ps1 -TnsAlias PDBORCL -Interactive
.\test_real_phase21\Verify_Phase21_Real.ps1 -TnsAlias PDBORCL -RequireModel
```

`test_real_phase21\.env` is gitignored and holds no secret. Each unreachable enabled database costs up to `AI_FLEET_DATABASE_TIMEOUT_SECONDS` on each of the three live questions, so disable databases that are known to be down before a run.

## Resume / Next-Agent Pickup

1. Restart the API to pick up the fleet tools and the scope-specific question lists.
2. A fleet program or datafile question logs on to every enabled database, up to the limit. On a large fleet, lower `AI_FLEET_MAX_DATABASES` or raise `AI_FLEET_CONCURRENCY` in Admin, Language model.
3. Outside synthetic mode each database read starts the readings server once, so a fleet question costs one process start and one Oracle logon per database.
4. Do not pass a program name, a size, or any other question text into a statement. Filter in Python.
5. Do not record the full live check as passed until `Verify_Phase21_Real.ps1` is run against Oracle. `Verify_Phase20_Real.ps1` still covers sign-in, permissions, and the earlier copilot routes.
6. In a database chat the fixed router sends "Which programs are connected to this database?" to `get_sessions`. `get_session_programs` and `get_datafile_sizes` are reached there only when the model nominates them; the fleet tools use the same two statements for every database.
7. These edits are uncommitted. Commit only when requested.
