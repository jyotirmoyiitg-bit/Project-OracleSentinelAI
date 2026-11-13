# Phase 9 Implementation Summary — Assistant Policy

Later work is in [Configuration and knowledge intake](./Progress_Summary_Configuration.md). Alembic head is now `0010`.

**Status:** The copilot policy is in place. A general Oracle explanation may come from the model. A database fact must come from an approved reading. A company requirement must come from an approved policy or a compliance result. SQL is a draft, and a database change is not executed. No new migration was added. A live Oracle run of the Phase 9 launcher was not performed.<br>
**Policy:** [docs/assistant-policy.md](../docs/assistant-policy.md)<br>
**Previous phase:** [Phase 8 handoff](./Progress_Summary_Phase8.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head remains `0009`. Privileged execution stays disabled, and `privileged_operations` is still not written.

## What Was Implemented

The assistant no longer treats every question as a document search that the model may only quote. A message can contain more than one kind of claim, and a supported part is still answered when another part has no approved reading.

- **General Oracle.** A question such as "what is a chain block?" is explained from Oracle knowledge. The document search also tries row chaining, chained rows, and row migration. An empty or unrelated search does not replace the explanation. The answer says the selected database was not queried.
- **Live database.** A question such as "how big is the USERS tablespace?" uses the approved tablespace reading. The model may explain those rows and may not replace them.
- **Company policy.** Retrieval is mandatory. If no approved policy document and no compliance result are available, the answer is `I could not establish the company requirement from an approved policy document.`
- **Compliance.** A finding remains the approved rule result plus database evidence. The model may explain it and may not override it.
- **SQL draft.** Any operator may receive one read-only `SELECT`. The statement is not executed. A changing statement is removed from a draft. SQL typed into the prompt is still refused.
- **Database change.** "Enable ARCHIVELOG" or "Kill this session" is explained as a runbook. `execute_sql` is denied. Nothing is sent to Oracle.
- **Mixed question.** "What is row chaining, and does FINDB have it?" explains row chaining and says there is no approved reading for the database check.
- **Alert window.** The approved alert-log reading still covers the last 24 hours. A request for the last hour says that the collected window is 24 hours.
- **Portal default.** For the local admin `sentinel` in dev or test, a new Copilot thread starts on the local model when that endpoint is ready.

The OMF check still means `DB_CREATE_FILE_DEST` is set. It does not prove that every existing file is Oracle-managed. A failed collection is not reported as compliant.

## Live launcher

`test_real_phase9\Verify_Phase9_Real.ps1` follows the Phase 8 script and adds the policy checks. The language model stays off, so this run does not call a hosted model.

1. Check Python 3.14, Node, and Docker. `-CheckPrerequisites` stops there.
2. Load the project-root `.env`, then `test_real_phase9\.env`. The phase file keeps sign-in on, the language model off, and both execution flags false.
3. Start Docker PostgreSQL and upgrade Alembic to head `0009`.
4. Stop a listener already on port 8000 or the portal port, then start the API.
5. Sign in with the local bootstrap account. The password stays in the root `.env`.
6. Reuse or register the selected TNS alias and test the Oracle connection.
7. Confirm proposal execution is refused, both privileged endpoints return 501, and `privileged_operations` has no rows.
8. Ask the copilot, with the model off, to refuse pasted SQL, keep a concept question off the database, keep a company-policy question on evidence, refuse a change, and answer a tablespace question from an approved reading.
9. Start the portal. `-Interactive` opens the Copilot for the selected database.

```powershell
.\test_real_phase9\Verify_Phase9_Real.ps1 -CheckPrerequisites
.\test_real_phase9\Verify_Phase9_Real.ps1 -TnsAlias PDBORCL -Interactive
```

## Verification

- Copilot policy tests in `backend/tests/ai/test_chat.py`: **20 passed**. They cover concept answers, SQL drafts that are not executed, the company-policy gap, a mixed question, a refused database change, pasted SQL, and the existing readings.
- The full Phase 9 launcher was not run against Oracle. The full backend suite was not rerun.

## Resume / Next-Agent Pickup

1. Do not add a tool that accepts SQL text. Do not add an executor for proposals or privileged operations.
2. Head is `0009`. The Phase 9 launcher requires `0009` and an empty `privileged_operations` table.
3. The Phase 9 live run keeps the model off. Start the normal API, with the configured local model, before expecting a concept answer in the portal.
4. These edits are uncommitted. Commit only when requested.
