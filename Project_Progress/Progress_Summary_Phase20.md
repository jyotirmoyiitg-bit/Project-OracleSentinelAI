# Phase 20 Implementation Summary — Security, Navigation, and Copilot Coverage

Later work is in [Phase 21 — Fleet copilot](./Progress_Summary_Phase21.md).

**Status:** The review findings of 14 Nov 2026 are implemented: permission checks and audit rows on every changing route, operator management, revocable local sessions, sign-in throttling, production start-up guards, a database workspace in place of the always-on sidebar, wider copilot routing with four new readings, fleet answers, model-assisted routing, and knowledge upload from the portal. The live launcher was added. `-CheckPrerequisites` passed. The full Oracle run was not performed.<br>
**Phase specification:** [Phase 20 — Security, navigation, and copilot coverage](../Phase_20_Security_Navigation_Copilot.md)<br>
**Previous summary:** [Phase 19 — GraphDB wait stats](./Progress_Summary_Phase19.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head remains `0010`. No table or column was added. No Oracle grant was added. Privileged execution stays disabled. No reading SQL changed; four copilot readings reuse statements GraphDB already runs.

## What Was Implemented

### Security and audit

- **Permissions.** Registering, changing, and test-connecting a database need `database.manage` (`backend/src/sentinel/api/v1/databases.py`). Acknowledging and resolving an incident need `incident.acknowledge`, and the actor is the signed-in principal, not a name in the request body (`incidents.py`). A chat thread is readable and writable only by the principal who opened it (`chat.py`). Before this phase a viewer could register or disable a database and read another operator's chat.
- **Audit.** `record_action` in `backend/src/sentinel/observability/audit.py` writes a row for sign-in attempts, sign-out, password changes, operator management, registry changes and connection tests, incident actions, every copilot question, and knowledge upload, reindex, and ACL changes. The copilot row carries scope, intent, whether a model answered, and the tools; its correlation id matches the chat message.
- **Operators.** `GET` and `POST /api/v1/admin/operators`, plus `/enabled` and `/password` under one username, in `admin.py`. The role route now lower-cases the username. The Admin page has an Operators panel (`frontend/src/AdminOperators.tsx`): add, change role, disable or enable, reset password.
- **Passwords.** `password_problem` in `auth/passwords.py`: at least 10 characters, a letter and a digit, not containing the username. `POST /api/v1/auth/password` lets a local operator change their own; the top bar has a Change password control for local sessions.
- **Revocation.** `auth/sessions.py` ties a local session cookie to a fingerprint of the account's role, enabled flag, and password hash. The sign-in middleware checks it on each request with a ten-second cache that is cleared when an administrator changes the account. A cookie issued before this phase has no fingerprint, so local operators sign in once more after the upgrade.
- **Throttle.** `auth/throttle.py` locks one username-and-address pair after `AUTH_MAX_FAILED_LOGINS` (5) failures inside `AUTH_LOCKOUT_MINUTES` (15) and answers 429 with `Retry-After`. The counters live in the API process.
- **Production guard.** `Settings` refuses `APP_ENV=production` without sign-in, a real `SECRET_KEY` of 32 or more characters, and a Secure cookie. `/metrics` needs sign-in in production unless `METRICS_PUBLIC=true`.
- **Headers.** `SecurityHeadersMiddleware` in `main.py` adds no-sniff, no-frame, and no-referrer everywhere, and no-store plus a restrictive CSP on `/api/`.
- **Portal.** A failed session check is shown as "Oracle Sentinel is not reachable" with Try again. The Knowledge reindex call no longer sends a role header from the browser. The OIDC flag from the session response is now passed to the sign-in page, which it was not before.

### Navigation and look

- `frontend/src/App.tsx`: the database sidebar renders only inside a database workspace. The landing page, Admin, Audit, Knowledge, Proposals, and Incidents are full width.
- `frontend/src/components/DatabaseTabs.tsx`: Overview, GraphDB, WaitDB, Events, and Copilot above every database page. A tab keeps the database; the sidebar keeps the tab.
- The top bar is Fleet, Incidents, Copilot, and Knowledge, then Proposals, Audit, and Admin. It scrolls rather than overflowing and collapses to icons below 1400 px. At 1024 px the page no longer scrolls sideways and Sign out stays on screen.
- GraphDB and WaitDB were removed from the top bar on 15 Nov 2026 at the user's request. They are tabs of a database workspace only. The chooser pages at `#/graph` and `#/wait` remain reachable by address.
- `frontend/src/LoginPage.tsx`: brand, a Directory or Local choice only when a directory is configured, a full-width button, and a note that sign-ins are audited.
- The fleet heading has Add database for a DBA or administrator. The sidebar hides it from other roles.
- "Phase 3", "Incident counts are not available yet", and the `.env` instruction were removed. Fleet rows and the database overview now return and show the count of open critical incidents.
- Buttons, panel captions, messages, badges, and the sidebar step up one text size. Styles are in the Phase 20 block at the end of `frontend/src/styles.css`.
- An unused helper in `IncidentCenter.tsx` that stopped `tsc -b` was removed.

### Copilot

- **Readings.** `redo_logs`, `control_files`, `segments`, and `background` in `backend/src/sentinel/ai/activity.py`, mapped to the GraphDB statements in `adapters/oracle/readings.py`, presented in `ai/reading_text.py`, allowlisted in `ai/safety.py`, and described once in `TOOL_DESCRIPTIONS` for both the readings server and the router. `ADMIN_ONLY_READINGS` covers the user list, control files, and segments. A non-administrator receives redo groups without member paths.
- **Routing.** `match_activity` gained the phrases listed in the specification. In `ai/orchestrator.py`, a question that names a reading on a database chat is treated as live unless it asks for an explanation; an ORA code with "what does", "mean", or "explain" is an explanation; the change pattern covers add, resize, drop, restart, unlock, and similar; "with SQL" is a draft request and a concept phrase no longer blocks a draft.

| 42 review phrasings | Before | After |
|---|---|---|
| Correct fixed route | 19 | 41 |
| Wrong reading | 6 | 0 |
| No reading | 17 | 1 (goes to model nomination) |

- **Fleet.** `get_fleet_health` in `ai/tools/health.py` answers fleet chat from stored status, worst first, with last seen and open incidents.
- **Model-assisted routing.** `Orchestrator._nominate` asks the model for up to three reading names when the fixed router has no match on a database chat. Names outside the allowlist or outside the caller's role are dropped, the database is fixed by the server, and a `route_question` entry appears in the function list. `AI_ROUTER_FALLBACK` (default on, editable in Admin, Language model) turns it off. The model is offered no tools and a tool request from it is still rejected.
- **Catalog.** Q32 "Largest segments" is a reading. Q31 keeps its unsupported status with corrected wording.
- **Readings server.** `backend/src/sentinel/mcp/server.py` and `client.py` use one JSON object per line, which is the MCP stdio framing. The server still answers a `Content-Length` client.

### Knowledge upload

`POST /api/v1/knowledge/upload` in `backend/src/sentinel/api/v1/knowledge.py` saves the raw request body into the category's intake folder and runs the intake scan on that file. It checks the administrator role, a plain file name, the extension, the first bytes, and the size limit, and needs `replace=true` to overwrite. The Knowledge page shows an Add a document form and the Reindex button to an administrator only. The upload uses the raw body because `python-multipart` is not installed.

## Live launcher

`test_real_phase20\Verify_Phase20_Real.ps1` follows the Phase 17 launcher through the real Oracle connection. Step 7 then runs `real_runtime.py checks`: security headers; a wrong password and a weak password refused; a temporary `phase20-operator` created through the operator route; the operator can list the fleet and is denied a registry change, both connection tests, the account list, and reindex; the administrator's chat thread is not readable by the operator; redo-log and background-process questions run their readings on the real database; the operator is refused the control-file listing; a fleet question uses stored status; a role change and a disable each sign the operator out; and the audit trail holds the sign-in, operator, and chat rows. The temporary account is removed. Step 8 launches the portal.

```powershell
.\test_real_phase20\Verify_Phase20_Real.ps1 -CheckPrerequisites
.\test_real_phase20\Verify_Phase20_Real.ps1 -TnsAlias PDBORCL -Interactive
```

A full run stops whatever is already listening on port 8000 and the portal port (5173 by default). The language model stays off. States are printed. Passwords, cookies, file paths, answer text, and SQL are not printed. Audit rows are not deleted. The live check does not upload a document, because an upload stays in the intake folder.

## Verification

- `backend/tests/api/test_phase20_security.py` (7 tests) and `backend/tests/ai/test_phase20_routing.py` (34 cases) pass.
- Full backend suite: **400 passed, 1 skipped**. Three older test files were updated: the registry tests now act as a DBA, and the production config test supplies the settings production requires.
- `frontend/src/Phase20.test.tsx` (6 tests), the rewritten `FleetWorkspace.test.tsx`, and the new operator test in `AdminPage.test.tsx` pass. Full frontend suite: **50 passed** in 11 files. `tsc -b` is clean.
- Ruff is clean on every file changed in this phase.
- The synthetic preview was opened as `previewadmin`: sign-in without a directory toggle, fleet page with no sidebar and no sideways scroll at 1024 px, workspace tabs, sidebar keeping WaitDB when the database changed, the Operators panel, and the upload form. The preview pane was hidden, so this was checked through the page structure and computed sizes, not screenshots.
- `Verify_Phase20_Real.ps1` parsed, and `-CheckPrerequisites` passed. The full Oracle launcher was not run.

## Not done in this phase

Semantic embeddings, a model-written summary on catalog answers, stored samples for wait and memory trends, a chat history list, a daily health report, Teams or webhook alerts, maintenance windows, and a shared sign-in throttle. The specification lists them under Out of scope.

## Resume / Next-Agent Pickup

1. Restart the API. Local operators sign in again once, because older cookies carry no fingerprint.
2. Registry routes now need a DBA or administrator. A script that registered a database without a role header in a sign-in-off deployment must send `X-Sentinel-Role: dba`.
3. Before setting `APP_ENV=production`, set `AUTH_REQUIRED=true`, a 32-character or longer `SECRET_KEY`, and `AUTH_COOKIE_SECURE=true`, and decide `METRICS_PUBLIC`.
4. The sign-in throttle and the session cache are per process. Run one API process, or move both to a shared store before running several.
5. Stored `app_config` values override `.env` at start-up. `AI_ROUTER_FALLBACK` is inserted as `true` on the first start; change it in Admin, Language model.
6. Do not give the model a tool to call. Nomination returns names only, and the server validates them.
7. Do not record the full live check as passed until `Verify_Phase20_Real.ps1` is run against Oracle.
8. These edits are uncommitted. Commit only when requested.
