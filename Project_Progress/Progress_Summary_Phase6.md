# Phase 6 Implementation Summary — AI Copilot with LLM Gateway

**Status:** Implemented. The gateway, chat, ask-time readings, and short-lived readings process are in the tree. Backend tests on 12 Nov 2026: **285 passed, 1 skipped**. Frontend vitest: **27 passed**. Live checks used hosted Anthropic, an admin user-list reading on PDBORCL, and a PDBORCL critical-error question after the alert-log scope fix. The formal live harness has not been rerun.<br>
**Phase specification:** [Phase 6 — AI Copilot](../Phase_6_AI_Copilot.md)<br>
**Illustrated architecture:** [docs/architecture.html](../docs/architecture.html)<br>
**Previous phase:** [Phase 5 — Knowledge and RAG](../Phase_5_Knowledge_RAG.md) · [Phase 5 handoff](./Progress_Summary_Phase5.md)<br>
**Next phase:** [Phase 7 — Operations, Auth, and Audit](../Phase_7_Operations_Auth_Audit.md) is recorded in [the Phase 7 handoff](./Progress_Summary_Phase7.md). This file remains the Phase 6 record.

This handoff records the Phase 6 copilot and the follow-on readings work that landed with it. It does not replace the Phase 5 live-harness notes. `test_real_phase5\Verify_Phase5_Real.ps1` is still a separate step. The committed baseline is `89bd6d1`. Hosted OpenAI and Anthropic, the sectioned copilot answer, the role-gated user list, the signed-in header, and the database-alert filter are in the working tree and are not committed.

## What Was Implemented

### Gateway

- The copilot lives under [backend/src/sentinel/ai/](../backend/src/sentinel/ai/). A deterministic workflow chooses tools. The language model does not choose them, and a reply that asks for native tool calls is discarded.
- Local chat speaks Ollama's HTTP API (`POST {base}/api/chat`). Hosted chat speaks either OpenAI or Anthropic. Both use the existing `httpx` dependency. The `ollama` package was not added. Clients do not trust environment proxies and do not follow redirects.
- Code defaults keep AI off: `AI_ENABLED=false` or `AI_PROVIDER=disabled`. `AI_FALLBACK_PROVIDER` must stay `disabled`. A local failure never calls the hosted provider, and hosted is never a fallback.
- Hosted generation runs only when `AI_HOSTED_APPROVED=true`, a model name, a `secret://` key reference, and a privacy disclosure are set. An empty `AI_HOSTED_API_BASE` uses `https://api.openai.com/v1` or `https://api.anthropic.com`. The vendor is `openai` or `anthropic`.
- OpenAI uses a Bearer token, `POST /v1/chat/completions`, and `response_format: {type: json_object}`. Anthropic uses `POST /v1/messages` with `x-api-key` and `anthropic-version: 2023-06-01`. The system text is sent separately, and temperature is omitted. The key is read from the process environment first, then from the one named line in the project `.env`. The secret-ref name must be a Python identifier, such as `secret://OPENAI_API_KEY` or `secret://ANTHROPIC_API_KEY`. The key is not stored in settings and is not written to logs.
- A reply that is fenced, or that has text around a JSON object, is reduced to that object before assessment parsing. A hosted misconfiguration does not stop the application from starting.
- The running portal `.env` selects hosted Anthropic: `AI_PROVIDER=hosted`, `AI_HOSTED_VENDOR=anthropic`, `AI_HOSTED_MODEL=claude-sonnet-4-5`, and `secret://ANTHROPIC_API_KEY`. A signed-in fleet question returned `llm_used` true with provider `hosted` and that model. The model narrates rows. It does not write SQL. `GET /api/v1/admin/ai/providers` includes `hosted_vendor` and does not return the key or the provider URL.

### Collected-evidence tools

These tools read PostgreSQL, not Oracle, at question time: `get_database_health`, `get_tablespace_status`, `get_fra_status`, `get_events`, `get_compliance`, and `search_knowledge`. Document ACL still applies. A tool argument that names a different database than the thread scope is denied. Evidence is redacted and bounded before it is shown or sent to a model.

### Ask-time readings

Operator questions about the live database run one named reading. The question text is never bound into SQL. An operator prompt that contains a SQL statement is refused before any tool or model runs.

The API starts `python -m sentinel.mcp` for that question, passes the TNS alias, reads the rows from stdin/stdout, and the process exits. It does not listen on a port and it is not started by the PowerShell harnesses. Port 8000 is the Sentinel API. The portal is Vite on port 5173. The database comes from the thread scope (`#/copilot/databases/{id}`), not from the sidebar selection alone.

Readings cover availability, critical alert lines, the latest database alert lines, tablespace pressure, retained growth, performance since startup, blocking and session limits, cursor-cache SQL, memory and parameters, backup and archive evidence, Data Guard, locked accounts, database users, invalid objects, failed scheduler jobs, and a current-issues bundle. The original datafile, memory, top SQL, session, running-backup, and running-job readings use the same path. Fleet chat does not open Oracle. Fast Recovery Area and compliance questions still use the collected-evidence tools.

Alert lines come from `V$DIAG_ALERT_EXT` through SQL. The server file is not opened. That view also returns listener and client traces, and a client trace on this machine contained a failed connection to `FREEPDB1` even while the session was PDBORCL. Every alert reading now keeps rows whose filename is the database alert (`rdbms` … `alert/log.xml`) and drops listener (`tnslsnr`) and client (`clients`) traces. The latest-log reading returns the newest 30 lines from the last 7 days. The critical-error reading stays the watched ORA list for the last 24 hours. A `Fatal NI` handshake line is not treated as one of those watched errors. A question containing `ora error` still selects the critical-error reading; `alert log` selects the latest lines.

Listing database users is a separate reading, `list_database_users`. It runs only for an admin operator. An operator, dba, or auditor receives `Authorization failed. Listing database users is limited to an admin operator.` and Oracle is not queried. An admin receives username and account status from `SYS.DBA_USERS`, capped at 80 rows, with no password columns. The existing roles stay `operator`, `dba`, `admin`, and `auditor`. Admin is the admin-type operator. The other three are non-admin operators.

Growth uses retained Sentinel samples, not AWR. On PDBORCL the samples were not yet sufficient, so the reading said no exhaustion forecast was available. CPU, waits, and parse counts are cumulative since startup. SQL is the current cursor cache. No recovery-point or recovery-time objective is stored. Recent privilege changes are not read from the audit trail.

The grant script is [scripts/oracle/02_grant_readonly_monitoring.sql](../scripts/oracle/02_grant_readonly_monitoring.sql). PDBORCL answered the new readings with the grants already present, including `SYS.V_$DIAG_ALERT_EXT`. Another PDB that reports a missing view needs that script as SYSDBA, then `03_verify_gsystem.sql` as GSYSTEM.

### Chat API and schema

- Migration [0006_ai_copilot.py](../backend/alembic/versions/0006_ai_copilot.py) adds `chat_threads`, `chat_messages`, and `tool_executions`. Every allowed and denied tool call is stored. Startup checks for `chat_threads` and does not call a model.
- Migration [0007_operator_accounts.py](../backend/alembic/versions/0007_operator_accounts.py) adds `operator_accounts` for the sign-in gate. Head is `0007`. It was applied on the application PostgreSQL.
- `POST /api/v1/chat/threads` opens a fleet, database, or incident thread. Fleet rejects a scope id. A missing or disabled database, and a missing incident, are rejected.
- `POST /api/v1/chat/threads/{id}/messages` stores the answer, then returns `text/event-stream` events: `status`, `tool`, `citation`, `message`, and `done`. When the model is disabled, unreachable, or returns invalid JSON, `llm_used` is false and the answer still lists the reading and guided commands.
- `GET /api/v1/admin/ai/providers` is administrator-only and reports provider status without the secret or the provider URL. There is no portal page that changes the provider.

### Portal

- `#/copilot` is the fleet-scoped chat. `#/copilot/databases/:id` and `#/copilot/incidents/:id` scope the thread. Database detail and incident detail have an **Ask copilot** action. Sidebar selection does not attach the database.
- Each answer is split into sections: the question, an Answer in a serif face, Limitations, numbered Next steps, the Database reading, Collected evidence, Functions, and Evidence. Function names use a monospace face and a decision chip. Evidence uses a kind chip. Status labels are answered, unavailable, disabled, and Not authorized.
- Guided commands include health, errors, alert log, blocking, Data Guard, and database users, in addition to FRA, tablespaces, datafiles, memory, SQL, sessions, backups, jobs, incidents, compliance, and runbooks. The prompt placeholder says SQL is not accepted.
- With AI disabled the badge reads **No language model**. With a provider enabled it shows the provider and model name. An old thread keeps the disclosure from when it was created; a new question after changing the provider needs a new thread.
- When sign-in is required, the header shows a face icon, the username, and the role. The local bootstrap account appears as `sentinel` / `ADMIN`, with the accessible name `Signed in as sentinel, Admin`.
- Below 900px the shared top bar wraps so the Copilot link does not push the refresh controls outside the window.

### Sign-in gate

This is a slice in front of Phase 7, not Phase 7 itself.

- When `AUTH_REQUIRED=true`, routes other than health and the auth endpoints require the `sentinel_session` cookie. The role comes from that session. `X-Sentinel-Role` is ignored while sign-in is required.
- The sign-in form has a **Local** checkbox. Unchecked uses Active Directory. Checked uses only the local operator account. A wrong directory password does not fall through to the local account. If the directory is not configured, the operator must check **Local**.
- The running portal has sign-in required and the directory unset, so local sign-in is the path in use. The bootstrap username is `sentinel` and the bootstrap role is admin. OIDC, proposals, and the role model are in the Phase 7 handoff. The admin-only user list still uses the existing `admin` role.

## Verification

Run from the repository root in PowerShell:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m pytest tests -q

Set-Location ..
..\.venv\Scripts\python.exe -m ruff check backend\src\sentinel\ai backend\src\sentinel\mcp backend\src\sentinel\auth backend\src\sentinel\api\v1\chat.py backend\src\sentinel\api\v1\auth.py backend\src\sentinel\adapters\oracle\activity.py backend\src\sentinel\adapters\oracle\readings.py backend\tests\ai backend\tests\test_auth.py backend\tests\test_oracle_permission_scripts.py

Set-Location frontend
npx vitest run
```

Latest results on 12 Nov 2026:

- Backend: **285 passed, 1 skipped**. The skip is the existing Windows symlink test from Phase 5. Added coverage includes the Anthropic messages call, the OpenAI default URL, a key read from the named `.env` line, no local-to-hosted failover, the admin user-list reading, the denial for operator, dba, and auditor before Oracle is called, the alert-log match, and the database-alert filename filter.
- Frontend vitest: **27 passed**. Added coverage includes the sectioned copilot answer and the signed-in header.
- Portal, earlier with the local model: a database-scoped question listed the live USERS tablespace. The allocated size was 5.0 MB (5,242,880 bytes), about 26 percent used, with autoextend on. The model then added a limitation and a suggestion to enlarge the tablespace. Those two sentences were narration. The reading itself was current and did not say the tablespace was full.
- Portal, with hosted Anthropic: a signed-in fleet question returned `llm_used` true, provider `hosted`, model `claude-sonnet-4-5`. The API log did not contain the key. A PDBORCL FRA question rendered the serif answer, amber limitations, numbered next steps, function rows, and evidence cards.
- Direct readings calls against PDBORCL returned rows for availability, tablespace pressure, blocking, critical errors, performance, SQL workload, memory configuration, recovery, Data Guard, security posture, and current issues. Failed-job history was empty. Growth returned six resources with status `unknown` because retained samples were not sufficient. The admin user-list reading returned 40 accounts. The latest alert reading returned database alert lines, including a connect record at 2026-11-12 18:10:53.
- PDBORCL versus FREEPDB1: a connection with alias PDBORCL reported container PDBORCL, database `orcl12c`, service `pdborcl`. FREEPDB1 itself refused the connection. The networking line was in a client trace (`diag\clients\...`), and the old critical-error predicate had promoted `Fatal NI connect error 12541` because the text contained `FATAL` and `SERVICE_NAME=FREEPDB1`. After the filename filter, the same question on the PDBORCL thread called `get_critical_errors`, did not mention FREEPDB1, and reported no watched critical ORA error in the last 24 hours.
- **Not a green formal run:** `test_real_phase6\Verify_Phase6_Real.ps1 -Interactive` reached migration `0006` and then failed because “fast recovery area” did not match the FRA intent. That match, and an alert-log checkpoint `KeyError`, were fixed afterward. The script has not been rerun. It forces AI off in `test_real_phase6\.env`. Root `.env` sets `AUTH_REQUIRED=true` and the live file does not override it, so the next run may need a session.

## Resume / Next-Agent Pickup

1. Apply migrations before using the copilot against a new application database:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
   ```

   Phase 6 added revision `0007`. The current head is `0008`; see the [Phase 7 handoff](./Progress_Summary_Phase7.md).

2. Leave the code defaults AI-off unless a reviewer chooses a provider. The current portal `.env` selects hosted Anthropic (`claude-sonnet-4-5`) with `AI_FALLBACK_PROVIDER=disabled`. The key belongs on `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, referenced by `secret://`. Do not add a fallback from local to hosted. Do not put the key in a setting field, a log, or this note.
3. The readings process starts inside the API for one question and then exits. Do not add a Windows service for it. Do not give it a tool that accepts SQL. Alert readings stay on the selected database's `rdbms` alert file. Do not read listener or client traces back in, and do not open the alert file on disk.
4. Rerun `test_real_phase6\Verify_Phase6_Real.ps1 -Interactive` when convenient. Expect AI to be disabled by that harness. If sign-in rejects the harness, the live `.env` has to allow the harness to authenticate; do not print secrets while doing that. `test_real_phase5\Verify_Phase5_Real.ps1 -Interactive` is still unre-run after its fixture-path and collection-wait fixes.
5. On a PDB that has not been re-granted, run `scripts\oracle\02_grant_readonly_monitoring.sql` as SYSDBA in that PDB, then `03_verify_gsystem.sql` as GSYSTEM.
6. Phase 7 is recorded separately. Do not add proposal execution or privileged DDL in a Phase 6 change. Phase 8 stays disabled.
7. The hosted-provider, copilot-layout, user-list, header, and alert-scope edits are uncommitted, together with this progress file. Commit only when requested.

## Known Limitations

- The model never selects tools. It can add advice the rows do not support. The listed database reading is the evidence. Limitations and next steps in the sectioned answer are narration.
- Fleet scope has no database id, so live readings and the database tools are refused until the thread is opened with **Ask copilot** on a database or incident.
- CPU, waits, parses, and file latency are cumulative since startup. They are not a baseline and they are not AWR.
- Tablespace growth needs regular retained samples spanning at least 24 hours. A short history returns status `unknown`.
- No recovery objective is stored. Data Guard lag is empty when the database has no standby.
- The critical-error reading is a watched list for 24 hours. It is not every `ORA-` code in the alert log. The latest-log reading is the newest 30 database alert lines for 7 days.
- `V$DIAG_ALERT_EXT` can see the instance alert together with listener and client traces. The readings keep the database alert file. A PDB session can still show instance-level lines from that file.
- Listing database users is limited to the admin role. The denial for operator, dba, and auditor is unit-tested. The only local account is admin, so that denial was not exercised through the portal.
- Provider selection is configuration plus `GET /api/v1/admin/ai/providers`. The portal does not edit it.
- Sign-in is a local account or an LDAP bind plus group check. It is not the Phase 7 OIDC and RBAC design. With sign-in off, tests still use the pre-authentication role header.
- Chat history is stored per thread. Refreshing the portal opens a new thread.
