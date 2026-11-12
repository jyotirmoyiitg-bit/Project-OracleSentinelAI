# Phase 6 Implementation Summary — AI Copilot with LLM Gateway

**Status:** Implemented. Automated checks pass. Browser verification used a temporary SQLite database with the language model disabled.<br>
**Phase specification:** [Phase 6 — AI Copilot](../Phase_6_AI_Copilot.md)<br>
**Previous phase:** [Phase 5 — Knowledge and RAG](../Phase_5_Knowledge_RAG.md) · [Phase 5 handoff](./Progress_Summary_Phase5.md)<br>
**Next phase:** [Phase 7 — Operations, Auth, and Audit](../Phase_7_Operations_Auth_Audit.md)

This handoff records the Phase 6 implementation and verification state. It does not replace the Phase 5 live-harness notes. `test_real_phase5\Verify_Phase5_Real.ps1` is still a separate step.

## What Was Implemented

### Gateway

- The copilot lives under [backend/src/sentinel/ai/](../backend/src/sentinel/ai/). A deterministic workflow chooses tools. The language model does not choose them, and a reply that asks for native tool calls is discarded.
- Local chat speaks Ollama's HTTP API (`POST {base}/api/chat`). Hosted chat speaks an OpenAI-compatible chat-completions URL. Both use the existing `httpx` dependency. The `ollama` package was not added. Clients do not trust environment proxies and do not follow redirects.
- `AI_ENABLED=false` or `AI_PROVIDER=disabled` is the default. `AI_FALLBACK_PROVIDER` must stay `disabled`. A local failure never calls the hosted provider.
- Hosted generation runs only when approval, base URL, model name, a `secret://` key reference, and a privacy disclosure are all set. The reference names an environment variable. The key is not stored in settings and is not written to logs. A hosted misconfiguration does not stop the application from starting.

### Read-only tools

The allowlist is `get_database_health`, `get_tablespace_status`, `get_fra_status`, `get_events`, `get_compliance`, and `search_knowledge`. There is no SQL execution tool. Tools read the application database: current observations, diagnostic events, stored compliance results, and `POST /api/v1/knowledge/search` through the existing retrieval function. Document ACL still applies. A tool argument that names a different database than the thread scope is denied. Evidence is redacted and bounded before it is shown or sent to a model.

### Chat API and schema

- Migration [0006_ai_copilot.py](../backend/alembic/versions/0006_ai_copilot.py) adds `chat_threads`, `chat_messages`, and `tool_executions`. Downgrade returns to `0005`. Every allowed and denied tool call is stored. Startup checks for `chat_threads` and does not call a model.
- `POST /api/v1/chat/threads` opens a fleet, database, or incident thread. Fleet rejects a scope id. A missing or disabled database, and a missing incident, are rejected.
- `POST /api/v1/chat/threads/{id}/messages` stores the answer, then returns `text/event-stream` events: `status`, `tool`, `citation`, `message`, and `done`. When the model is disabled, unreachable, or returns invalid JSON, `llm_used` is false and the answer lists guided commands plus citations from the tools that ran.
- `GET /api/v1/admin/ai/providers` is administrator-only and reports provider status without the secret or the provider URL. There is no portal page that changes the provider. Provider choice stays in configuration.
- `X-Sentinel-Role` remains a pre-authentication stand-in until Phase 7.

### Portal

- `#/copilot` is the fleet-scoped chat. `#/copilot/databases/:id` and `#/copilot/incidents/:id` scope the thread. Database detail and incident detail have an **Ask copilot** action.
- The page shows a provider badge, the disclosure, guided commands, the answer, the tool transcript, and citations. With AI disabled the badge reads **No language model**.
- Below 900px the shared top bar wraps so the Copilot link does not push the refresh controls outside the window.

## Verification

Run from the repository root in PowerShell:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m pytest tests -q

Set-Location ..
..\.venv\Scripts\ruff.exe check backend\src\sentinel\ai backend\src\sentinel\api\v1\chat.py backend\src\sentinel\main.py backend\src\sentinel\config\settings.py backend\src\sentinel\persistence\models.py backend\alembic\versions\0006_ai_copilot.py backend\tests\ai backend\tests\test_migrations.py

Set-Location backend
..\.venv\Scripts\python.exe -m mypy --disable-error-code var-annotated src\sentinel\ai src\sentinel\api\v1\chat.py src\sentinel\main.py src\sentinel\config\settings.py src\sentinel\persistence\models.py

Set-Location ..\frontend
npm run build
npx vitest run
```

Latest results:

- Backend: **184 passed, 1 skipped**. The skip is the existing Windows symlink test from Phase 5. The copilot tests cover the same prompt through local and hosted mocks, no failover, secret-reference rejection, injection that cannot allow `execute_sql`, an audit row for that denial, a cited local FRA answer, and `/health/live` plus the fleet API while AI is disabled.
- Ruff: passed for the Phase 6 files listed above.
- Mypy: passed for those modules with the existing `var-annotated` suppression.
- Frontend: production build succeeded. Vite still reports the existing large-chunk advisory. Vitest: **25 passed**.
- Browser, against `%TEMP%\sentinel-phase6.db`, with both schedulers off and AI disabled: `#/copilot` showed the no-model badge, disclosure, and guided commands. An empty Send showed "Enter a question." FRA status on the empty fleet called `search_knowledge`, returned no matching evidence, and kept `llm_used` false. Fleet overview still loaded. At 390px the document scroll width matched the viewport. The temporary API and Vite processes were stopped afterward.
- **Not run:** a live Ollama server, a hosted model API, and live Oracle. Local and hosted providers were exercised with mock HTTP transports only.

## Resume / Next-Agent Pickup

1. Apply the migration before using the copilot against the application database:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
   ```

2. Leave `AI_ENABLED=false` and `AI_PROVIDER=disabled` unless a reviewer chooses a provider. A local model also needs `AI_LOCAL_URL` and `AI_MODEL_NAME`. A hosted model needs `AI_PROVIDER=hosted`, `AI_HOSTED_APPROVED=true`, `AI_HOSTED_API_BASE`, `AI_HOSTED_MODEL`, `AI_HOSTED_API_KEY_SECRET_REF=secret://...`, and `AI_HOSTED_PRIVACY_DISCLOSURE` together. Do not put the API key in the reference field, and do not add a fallback from local to hosted.
3. The copilot must keep calling knowledge search rather than reading the intake folders itself. A disabled model must not block that search or Oracle collection.
4. Phase 7 should replace `X-Sentinel-Role` with an authenticated principal. Do not add privileged DDL or a tool that executes SQL.
5. `test_real_phase5\Verify_Phase5_Real.ps1 -Interactive` can be rerun when convenient. An earlier run applied migration `0005` and then stopped before reindex; the fixture path and collection-wait checks were corrected after that run and have not been rerun. Phase 4 live verification remains separate.
6. Changes are uncommitted. Commit only when requested.

## Known Limitations

- The model never selects tools. Answer quality depends on the configured model, and the no-model path is a guided reading of collected evidence.
- Fleet scope has no database id, so health, FRA, tablespace, and incident tools are not planned until the thread is opened from a database or incident.
- Provider selection is configuration plus `GET /api/v1/admin/ai/providers`. The portal does not edit it.
- Tool authorization uses the pre-authentication role header. It is not a login.
- Chat history is stored per thread. Refreshing the portal opens a new thread.
