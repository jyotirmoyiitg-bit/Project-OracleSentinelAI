# Phase 26 — Copilot fixes

**Phase:** 26
**Status:** Implemented
**Prerequisites:** [Phase 24 — Copilot supervisor](Phase_24_Copilot_Supervisor.md), [Phase 20 — Security, navigation, and copilot coverage](Phase_20_Security_Navigation_Copilot.md)

## 1. Overview

During testing the database copilot answered "which schema in the database is not a system schema?" with "The combined size of the redo log files is 150.0 MB", marked high confidence and labelled Oracle knowledge. The stored chat showed what happened.

| Step | What went wrong |
|---|---|
| Routing | The supervisor chose Oracle knowledge. Its instructions named files, logs, space, memory, sessions, backups, jobs, and errors as database subjects, but not users or schemas |
| Fixed patterns | The patterns behind the supervisor knew "database users" and "schema list", not this wording, so no reading was added |
| Answer | The model was sent the earlier messages of the chat and returned the previous answer word for word, although two manual passages had been found. Nothing compared the new answer with the earlier ones |
| Reading | The user reading returned only a name and a status, so it could not have told an application schema from a system one |

Phase 26 fixes each step. No migration, no new Oracle grant, no new setting, and no change to the portal code.

## 2. The user reading

`list_database_users` now reads the `ORACLE_MAINTAINED` column of `DBA_USERS` beside the name and the status. The statement is still one fixed `SELECT` with an 80-row cap, and application schemas are returned first so the cap cannot hide one behind the Oracle accounts.

The reading is shown in two groups:

```
Application schemas, not maintained by Oracle: 3
APPUSER OPEN
GSYSTEM OPEN (the Sentinel monitoring account)
REPORTING LOCKED
Schemas maintained by Oracle (system schemas): 35
OPEN: SYS, SYSTEM
EXPIRED & LOCKED: XDB, WMSYS, ...
```

- The summary gives the counts: "45 database users: 10 application schemas and 35 maintained by Oracle."
- Oracle-maintained accounts are grouped by status, twelve names to a line, so the whole list stays in view.
- The monitoring account is not maintained by Oracle, so it is listed with the application schemas and named for what it is.
- A snapshot stored before this phase has no flag and is shown as one plain list.
- The column exists from Oracle Database 12.1. `scripts/oracle/03_verify_gsystem.sql` checks it.

The reading stays limited to an administrator, as before.

## 3. Routing

- **Fixed patterns.** A question that names a schema, an Oracle-maintained account, or system, application, built-in, default, or internal users is the user reading. A question about a schema's size, objects, tables, SQL, privileges, roles, or passwords is not; it is left to the other rules or to the supervisor.
- **Supervisor instructions.** Users, accounts, and schemas are named as database subjects. A question that says "in the database", "in this database", "on this instance", or "here" and asks which, how many, or what exists is a database question. Which schemas exist, and which are application or system schemas, is read from the database and not explained from memory.
- **Behind the supervisor.** When the supervisor chooses Oracle knowledge for a question the patterns recognise, and the question does not ask for an explanation, the reading is added. This rule existed; it now also covers schema wording.
- **A caller who may not read the list.** An operator who asks which schemas exist is told "Authorization failed. Listing database users is limited to an admin operator." This applies whichever specialist or reading the supervisor chose, and it is what the fixed patterns already answered with the supervisor off. Before this phase such a question could be answered from another reading or from a general explanation, either of which looks like an answer about the database.
- "What is a schema?", "Explain what a system schema is for", and "Why does Oracle have system schemas?" stay explanations for every role.

## 4. An earlier answer is not repeated

Before an answer is accepted it is compared with the earlier answers in the same chat.

- The first 300 characters are compared, ignoring case and spacing. An answer that is the same, or at least 90 percent the same, as the start of an earlier answer to a different question is a copy.
- A copy is asked for again with the earlier messages kept and a note that the reply repeats an earlier answer. A reply that is still a copy is asked for a last time without the earlier messages.
- Asking the same question again is not a copy. An answer shorter than 24 characters is not compared.
- Only a turn that took no reading of its own is checked. The same reading asked for in different words may honestly give the same answer.
- The Oracle-knowledge and general specialists are also told that earlier messages only show what the operator refers to, and that a figure from an earlier answer was read for another question.

In the normal case this costs nothing. A copy costs one or two more model calls.

## 5. An explanation with no source

| The Oracle-knowledge answer | Confidence shown | Note under the answer |
|---|---|---|
| Used at least one manual passage | The model's own | "This explanation is general Oracle guidance, with the manual passages it used listed as sources. The selected database was not queried." |
| Found no manual passage | Low, whatever the model said | "No manual passage was found for this question, so this is the model's general Oracle knowledge without a source. The selected database was not queried." |

With no passage the local model was seen to name SCOTT and HR as system schemas at high confidence, which is why the cap is low and not medium.

A database answer is also told to name every matching row when the question asks which or asks for a list. Before this, the model answered the schema question with one of two application schemas.

## 6. Acceptance

- [x] The user reading returns the Oracle-maintained flag, lists application schemas first, and stays one fixed statement with a row cap on a view already granted.
- [x] The reading is shown as application schemas and Oracle-maintained schemas with counts, and names the monitoring account.
- [x] A question about which schemas exist reads the user reading for an administrator, whichever specialist the supervisor chose.
- [x] An operator who asks it is refused, and no model is called.
- [x] A question that asks what a schema is stays an explanation.
- [x] An answer copied from an earlier turn is asked for again, and is not shown.
- [x] An explanation with no manual passage is low confidence and says so.
- [x] With `qwen2.5-coder:14b` the supervisor routed 61 of 63 labelled questions as expected, against 51 of 53 before. The ten new questions about schemas and users were all routed correctly, and the two misses are the two from Phase 24.
- [x] The changed statement was run once against the local Oracle database through the monitoring account: 45 users, 10 application schemas, 35 maintained by Oracle.
- [x] The conversation that went wrong was asked again with the real local model and synthetic readings, three times as an administrator and once as an operator. The administrator was given both application schemas each time; the operator was refused.
- [ ] The full live check is recorded only after `Verify_Phase26_Real.ps1` is run. A written spec is not a pass.

## 7. Tests

- `backend/tests/ai/test_phase26_copilot_fixes.py`: the statement; the two groups and the plain list for an older snapshot; which wording is and is not the user reading; the rules behind the supervisor for both roles; the administrator and operator answers end to end; the copy check and both retries; the confidence cap and the two notes.
- `backend/tests/ai/test_chat.py`: one existing test expected "high" for an explanation with no manual passage; it now expects "low".
- `backend/tests/test_oracle_permission_scripts.py`: the verification script checks the new column.
- Live check: `test_real_phase26\Verify_Phase26_Real.ps1`. `-CheckPrerequisites` does not connect to Oracle. `-RequireModel` fails unless the local model routed and answered.

## 8. Out of scope

- Showing a non-administrator the count of application schemas without the names. The list of users is still limited to an administrator.
- Sending the supervisor the earlier messages, so that "and what about the other one?" is routed by what it refers to. The supervisor still sees only the current question.
- One manual passage per document reaches the model. Five passages were found for the question above and two were sent, because sources are listed once per document.
- A reading of a schema's size, objects, or privileges.
- Oracle Database 11g, which has no `ORACLE_MAINTAINED` column.
