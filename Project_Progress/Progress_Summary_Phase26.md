# Phase 26 Implementation Summary — Copilot fixes

**Status:** The database copilot no longer answers a question about the schemas of a database with the previous answer of the chat. The question is read from the database, the reading tells application schemas from the ones Oracle maintains, a copied answer is asked for again, and an explanation with no manual passage is marked low confidence. Automated checks pass. The changed statement was run once against the local Oracle database, and the conversation that went wrong was asked again with the real local model. The live launcher was added and its check step passed against a synthetic API. The full launcher run was not performed.<br>
**Phase specification:** [Phase 26 — Copilot fixes](../Phase_26_Copilot_Fixes.md)<br>
**Previous summary:** [Phase 25 — Database sections](./Progress_Summary_Phase25.md)<br>
**Assistant policy:** [docs/assistant-policy.md](../docs/assistant-policy.md)<br>
**Architecture:** [docs/architecture.md](../docs/architecture.md)

Migration head remains `0012`. No table, column, setting, or Oracle grant was added. No portal code was changed. Privileged execution stays disabled.

## What was reported

In a database chat, as an administrator:

1. "what is the combined size of the log files?" was answered from the redo log reading: 150.0 MB.
2. "which schema in the database is not a system schema?" was answered "The combined size of the redo log files is 150.0 MB", labelled Oracle knowledge, confidence high.

## What the stored chat showed

The thread was read from the application database. The second answer had the same summary, limitations, and next steps as the first, word for word. The supervisor had chosen Oracle knowledge, and the document search had found five passages, two of which were sent to the model.

| Cause | Kind |
|---|---|
| The supervisor chose Oracle knowledge for a question about this database | The local model, helped by instructions that did not name users or schemas |
| The fixed patterns behind it did not know the wording | Code |
| The model returned the previous answer and nothing checked for that | The local model, and code that trusted it |
| The user reading could not tell an application schema from a system one | Code |

So the local model made two of the mistakes, and the code would not have produced a correct answer with any model.

## What Was Implemented

### Reading

- `backend/src/sentinel/adapters/oracle/readings.py`: `USER_LIST_SQL` also selects `ORACLE_MAINTAINED` and orders application schemas first. Same view, same 80-row cap.
- `backend/src/sentinel/ai/reading_text.py`: `_database_users` lists application schemas, then Oracle-maintained schemas grouped by status, with counts. `MONITORING_ACCOUNT` is named in the list. Rows without the flag are one plain list.
- `backend/src/sentinel/ai/activity.py`: the description of `list_database_users`; the synthetic rows now have three application schemas, including the monitoring account, and two Oracle-maintained ones.
- `scripts/oracle/03_verify_gsystem.sql`: the `DBA_USERS` check selects the new column.

### Routing

- `backend/src/sentinel/ai/activity.py`: `_SCHEMA_NAMED` and `_SCHEMA_OTHER_TOPIC`, used at the end of `match_activity`.
- `backend/src/sentinel/ai/supervisor.py`: `SUPERVISOR_PROMPT` names users, accounts, and schemas, and says that what exists in the database is read from it.
- `backend/src/sentinel/ai/orchestrator.py`: `merge_decision` returns the user reading for a non-administrator who asks which schemas exist, so the existing refusal answers.

### Answer

- `backend/src/sentinel/ai/orchestrator.py`:
  - `_assess`: one model call with the existing JSON repair, split out of `_compose` so it can be repeated.
  - `repeats_earlier` and `_REPEAT_NOTICE`: the copy check and the note sent with the first retry. The second retry leaves the earlier messages out.
  - `_compose`: confidence is low for an explanation with no manual passage. `_turn_note` has a cited and an uncited note.
  - `_advice_prompt`: the Oracle-knowledge and general specialists are told what the earlier messages are for. `_system_prompt`: a database answer names every matching row.

## Checks with the real model and the real database

| Check | Result |
|---|---|
| Supervisor routing, `qwen2.5-coder:14b`, 63 labelled questions | 61 routed as expected, about 0.8 seconds each. The 53 questions from Phase 24 gave 51 as before; all ten new questions about schemas and users were right. The fixed patterns alone gave 44 |
| The changed statement on `PDBORCL` through the readings server | 45 users in 0.5 seconds: 10 application schemas, 35 maintained by Oracle, the monitoring account named, application rows first. Counts only were printed |
| The conversation that went wrong, real model, synthetic readings, as an administrator, three times | The schema question was read with `list_database_users` and the answer named both application schemas each time, in about 3 seconds |
| The same as an operator | "Authorization failed. Listing database users is limited to an admin operator." in under a second, with no model call for the answer |
| "What is a schema?" and "Why does Oracle keep SYS and SYSTEM separate?" | Stayed Oracle knowledge with manual passages cited |

The copy itself could not be made to happen again on demand. With the same two earlier messages the model did not copy in seven tries: four with no manual passage and three with two passages written for the test. It is covered by a test with a stand-in model that copies, and the stored chat is the record that it happened.

## Verification

- `backend/tests/ai/test_phase26_copilot_fixes.py`: **33 passed**. Full backend suite: **579 passed, 1 skipped**. Ruff is clean on the changed files.
- One existing test was changed: an explanation with no manual passage is now low confidence, not the model's "high".
- Full frontend suite, unchanged: **73 passed** in 14 files.
- `Verify_Phase26_Real.ps1` parsed and `-CheckPrerequisites` passed. Its check step passed against a synthetic API with the model off (the fixed patterns routed) and with the real local model and `--require-model`. The full launcher was not run.

## Live launcher

`test_real_phase26\Verify_Phase26_Real.ps1` follows the Phase 25 launcher through the real Oracle connection, then asks the two questions again as an administrator and checks that the second answer was read with the user reading, lists both groups, and is not the first answer. It asks for a list of schemas, asks what a schema is, and asks the schema question as a temporary operator, who must be refused. It turns the supervisor on for the checks and puts the setting back.

```powershell
.\test_real_phase26\Verify_Phase26_Real.ps1 -CheckPrerequisites
.\test_real_phase26\Verify_Phase26_Real.ps1 -TnsAlias PDBORCL -RequireModel -Interactive
```

## Decisions taken

- The list of users stays limited to an administrator. Whether an operator should see the counts without the names was left open.
- The confidence of an uncited explanation is capped at low, not medium. This is my choice, made after the model named SCOTT and HR as system schemas at high confidence with no passage to go on.
- The copy check is limited to turns without a reading, so that a legitimate repeat of the same measurement is not asked for twice.
- A follow-up such as "can you say more about that?" still uses the earlier messages. It is only asked again when the reply is the earlier answer itself.

## Resume / Next-Agent Pickup

1. No migration and no grant. Restart the API. The portal needs no rebuild.
2. A database that was read by an agent before this phase has a stored user list without the flag. It is shown as a plain list until the next snapshot.
3. A new subject the copilot should read from the database needs three things: wording in `match_activity`, a mention in `SUPERVISOR_PROMPT`, and a labelled question in the routing evaluation. Run the evaluation with the real model after any change to the prompt.
4. When an answer looks wrong, read the thread from `chat_messages` first. The `tool_trace` of the answer shows the route and the readings, and the previous messages show what the model was sent.
5. Known and not fixed: the supervisor does not see earlier messages; sources are listed once per document, so the model gets one passage per document; Oracle 11g has no `ORACLE_MAINTAINED` column.
6. Do not record the full live check as passed until `Verify_Phase26_Real.ps1` is run.
7. These edits are uncommitted. Commit only when requested.
