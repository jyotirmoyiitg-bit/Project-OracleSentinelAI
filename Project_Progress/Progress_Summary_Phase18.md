Later work is in [Phase 19 — Wait stats](./Progress_Summary_Phase19.md).

# Phase 18 Implementation Summary — Structured AI Answers and Readable Formatting

**Status:** Copilot answers now travel as structured data end-to-end. The summary renders as Markdown, confidence is shown, database readings render as a table, and the portal no longer re-parses display text. Fleet health uses one status for the counts and each row. A database the monitoring account cannot reach is not registered. One that stays unreachable for 7 days is disabled, and an administrator can recheck the fleet or remove a database. Automated checks pass (backend 349, frontend 37, production build) for the answer work; the fleet checks below passed on their own. The live launcher was added. `-CheckPrerequisites` passed. The full Oracle run was not performed.<br>
**Phase specification:** [Phase 18 — Structured AI answers and readable formatting](../Phase_18_AI_Response_Formatting.md)<br>
**Previous summary:** [Phase 17 — GraphDB for every operator](./Progress_Summary_Phase17.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head remains `0010`. Privileged execution stays disabled. No new grant was added. No table or column was created; the structured answer is carried on the SSE `message` event, and `chat_messages.content` keeps the plain-text rendering.

## What Was Implemented

### Structured answer contract

- `backend/src/sentinel/ai/types.py` gains `StructuredAnswer` (`kind`, `summary`, `confidence`, `limitations`, `next_steps`, `verdict`, `reading`, `citation_note`, `evidence_lines`, `notes`) and `Answer.structured`. `ASSESSMENT_SCHEMA` lives there so the gateway, providers, and orchestrator share one schema.
- `Orchestrator._compose` in `backend/src/sentinel/ai/orchestrator.py` returns the `Assessment` fields separately. One `_render_text()` builds the stored `content` string from them, so stored text keeps its shape (`Limitations:` / `Next steps:` / `Database reading:` blocks unchanged for history and existing readers).
- Catalog answers build `structured` from the stored `CatalogEnvelope` (verdict from label/status/health, `reading` from the selected lines, limitations and next step from the question) instead of from the rendered first line.
- A model answer whose summary contains the word `Limitations:` or whose next step contains `;` now survives intact. Before, the portal split on those markers and broke both.
- `POST /api/v1/chat/threads/{id}/messages` adds `answer` to the SSE `message` event in `backend/src/sentinel/api/v1/chat.py`. `GET …/messages` is unchanged.

### Provider schema and repair

- `ChatGateway.generate` in `backend/src/sentinel/ai/gateway.py` forwards `ASSESSMENT_SCHEMA` instead of `structured_schema=None`. Ollama receives it as `format` (`providers/local.py`); OpenAI receives `response_format: json_schema` (`providers/hosted.py`); Anthropic keeps the prompt contract.
- One invalid model reply gets one repair retry (`Return only valid JSON matching this schema…`) before the no-model fallback. A provider error does not retry.
- The last turns of the thread are sent as bounded, sanitized conversation history, so a follow-up has context. Tool choice stays deterministic; history is data, not instructions.

### Portal

- `parseCopilotAnswer`, `splitVerdict`, and the `VERDICT` regex are deleted from `frontend/src/CopilotPage.tsx`. The card renders `answer` when present and falls back to `content` as the summary for answers stored before Phase 18 (`legacyStructured`, fallback kind only).
- `frontend/src/components/Markdown.tsx` renders the summary (headings, paragraphs, bold, inline code, fenced code, lists, pipe tables) as React elements only — no raw model HTML is injected. A draft SQL statement renders in a code block and is never executed; `omit_unexecuted_sql` / `neutralize` still run first.
- `frontend/src/components/ReadingTable.tsx` renders reading lines as a table; a `… percent used …` line becomes name/detail rows.
- The answer card shows verdict chips (catalog), a confidence chip (model), Limitations, Next steps (each step intact, `;` included), the citation note, Collected evidence (fallback), the Database reading table, collapsed Functions and Evidence (`<details>`), and a provider/model footer with a Copy button.
- `sendChatMessage` in `frontend/src/api.ts` consumes SSE incrementally with a reader. `status`/`tool` events update a `Working…` progress line, and a Stop control aborts the request. The final `message` event still carries the complete answer.

### Fleet health, registration, and removal

The fleet table and the summary now share one status. A stored healthy, warning, offline, or critical label without a fresh probe is unknown. A disabled database shows Disabled, including in the health filter. Health is still not inferred from a connection test alone.

- **Registration.** `POST /api/v1/databases` requires a TNS alias and a successful monitoring-account connection before the row is saved. A missing alias or a failed connection returns 422 and does not register the database. `register-from-tns` already refused a failed connection.
- **Seven days unreachable.** After a connectivity probe, if `last_connection_at` (or `created_at` when that is empty) is at least 7 days old, the database is disabled. One unread portal notification says GSYSTEM could not connect for 7 days and asks the operator to remove it. A second probe does not send a second notification. A failure whose last successful connection is only 1 day old stays enabled.
- **Check fleet status.** An administrator or a DBA sees this control at the top of Database fleet. `POST /api/v1/databases/check-fleet` runs the scheduler probes for every enabled database, then the page reloads the table, the counts, and the sidebar. A disabled database is listed and is not probed. One failed connection does not disable a database.
- **Remove.** Each fleet row has Remove for an administrator or a DBA. Confirmation is required. `DELETE /api/v1/databases/{target_id}` removes the registry row. Oracle is not changed. An operator can view the fleet and does not see either control.

No migration was added. Alembic head remains `0010`. No new Oracle grant was added.

## Live launcher

`test_real_phase18\Verify_Phase18_Real.ps1` follows the Phase 17 launcher. Steps 1 to 6 are the same: tools, live configuration, Docker PostgreSQL, Alembic head `0010`, local administrator sign-in, and the real target. Step 7 opens a database-scoped copilot thread with the model disabled, asks a guided FRA question and the catalog reachability question, and confirms the SSE `message` event carries a structured `answer` (fallback kind for the model question, catalog kind with a verdict for the catalog question), that no `execute_sql` tool was allowed, and that the stored `content` is non-empty. Step 8 launches the portal at the copilot page for that database.

```powershell
.\test_real_phase18\Verify_Phase18_Real.ps1 -CheckPrerequisites
.\test_real_phase18\Verify_Phase18_Real.ps1 -TnsAlias PDBORCL -Interactive
```

A full run stops whatever is already listening on port 8000 and the portal port (5173 by default). The language model stays off, so this run proves the structured fallback and catalog contract against real Oracle readings; a hosted or local model answer is covered by the automated tests with mocked providers. Counts and states are printed. File paths, cookies, passwords, and SQL are not printed.

## Verification

- `backend/tests/ai/test_chat.py`: structured fields on the SSE `message` event (`answer.kind`), an invalid-then-valid model reply repaired with the step containing `;` intact, and a follow-up turn sending the prior turn as history. `backend/tests/ai/test_gateway.py`: Ollama `format` and OpenAI `json_schema` carry the assessment schema.
- Full backend suite: **349 passed**.
- `frontend/src/CopilotPage.test.tsx`: structured rendering (verdict and confidence chips, Markdown table and code block, `;` step intact, reading table), and the pre-Phase-18 fallback from `content` alone. **4 passed**. Full frontend suite: **37 passed**. `tsc -b && vite build` clean. Ruff clean.
- `Verify_Phase18_Real.ps1` parsed, and `-CheckPrerequisites` passed. The full Oracle launcher was not run.
- Fleet registration and the 7-day disable: `backend/tests/api/test_dashboard.py`, `backend/tests/api/test_tns_registration.py`, and `backend/tests/collectors/test_collection.py` passed. The pytest process then failed only while deleting its Windows temp directory.
- Fleet check and remove: `test_check_fleet_recollects_enabled_databases_and_remove_requires_a_manager` passed. An operator receives 403. An administrator recollects the enabled database to healthy and leaves the disabled database disabled. A DBA can delete the enabled database.
- `frontend/src/FleetWorkspace.test.tsx`: an administrator can check the fleet and remove a database after confirmation. An operator does not see those controls. **10 passed**.

## Resume / Next-Agent Pickup

1. Restart the API before expecting `answer` on the SSE `message` event, the registration refusal, the 7-day disable, or Check fleet status from a process that was already running. Reload the fleet page after that restart.
2. Keep `content` as the stored plain-text rendering. Do not reintroduce display-text parsing in the portal; extend `StructuredAnswer` instead.
3. Token-by-token model streaming is still out of scope: providers are called non-streaming and SSE progress is event-level.
4. Do not record the full live check as passed until `Verify_Phase18_Real.ps1` is run against Oracle.
5. Do not mark a database healthy from Check fleet status until the probes return. Do not disable a database on one fresh connection failure.
6. These edits are uncommitted. Commit only when requested.
