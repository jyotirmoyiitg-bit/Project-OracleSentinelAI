# Phase 20 — Security, Navigation, and Copilot Coverage

**Phase:** 20  
**Status:** Implemented  
**Prerequisites:** [Phase 19 — GraphDB wait stats](Phase_19_GraphDB_Waits.md), [Phase 17 — GraphDB for every operator](Phase_17_GraphDB_Access.md)  
**Next phase:** [Phase 21 — Fleet copilot](Phase_21_Fleet_Copilot.md)

## 1. Overview

Phase 20 acts on the product review of 14 Nov 2026. It closes the authorization and audit gaps, gives administrators a way to manage operators, makes a local session revocable, replaces the always-on database sidebar with a database workspace, widens what the copilot can answer, and lets an administrator add knowledge documents from the portal. There is no migration. No Oracle grant is added. Nothing in this phase changes an Oracle database.

## 2. Security rules

| Rule | Behaviour |
|---|---|
| Registry changes | `POST /databases`, `POST /databases/register-from-tns`, `PUT /databases/{id}`, `POST /databases/test-tns-connection`, and `POST /databases/{id}/test-connection` need `database.manage`. A viewer, operator, or auditor receives 403. Reading the fleet stays open to every signed-in role. |
| Incident actions | Acknowledge and resolve need `incident.acknowledge`. The actor is the signed-in principal. A name in the request body is used only when sign-in is off. |
| Chat threads | A thread belongs to the principal who opened it. Any other caller receives 404 on read and on post. |
| Operators | An administrator (`role.manage`) lists, creates, changes the role of, disables, and resets the password of local accounts. An operator cannot change their own role or disable themselves there. Directory accounts stay in the directory. |
| Passwords | A new local password has at least 10 characters with a letter and a digit and does not contain the username. A local operator changes their own password with the current one. Hashes are salted PBKDF2. |
| Sign-in throttle | After `AUTH_MAX_FAILED_LOGINS` failures inside `AUTH_LOCKOUT_MINUTES` for one username and client address, sign-in for that pair returns 429 with `Retry-After` for the same number of minutes. Counters are in the API process. |
| Revocation | A local session cookie carries a fingerprint of the account's role, enabled flag, and password hash. Disabling the account, changing its role, or changing its password signs every open session out on its next request. Directory and OIDC sessions end at their expiry. |
| Production guard | `APP_ENV=production` refuses to start without `AUTH_REQUIRED=true`, a `SECRET_KEY` of at least 32 characters that is not the default, and `AUTH_COOKIE_SECURE=true`. `/metrics` needs sign-in in production unless `METRICS_PUBLIC=true`. |
| Headers | Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer`. `/api/` responses add `Cache-Control: no-store` and a restrictive `Content-Security-Policy`. HSTS is added when the cookie is Secure. |
| Unreachable API | The portal shows an error with Try again when the API does not answer. It is never treated as a signed-in operator. |

## 3. Audit

These actions now write an append-only `audit_events` row with the principal, target, outcome, and correlation id.

| Action | When |
|---|---|
| `auth.login`, `auth.logout`, `auth.password` | Every sign-in attempt (success, denied, locked), sign-out, and own-password change |
| `operator.create`, `operator.role`, `operator.enabled`, `operator.password` | Operator management |
| `database.register`, `database.update`, `database.remove`, `database.test_connection`, `database.check_fleet` | Registry changes and connection tests |
| `incident.acknowledge`, `incident.resolve`, `incident.clear` | Incident actions |
| `chat.question` | Every copilot question, with scope, intent, whether a model answered, and the tools that ran. The correlation id matches the chat message. The question text stays in the chat tables. |
| `knowledge.upload`, `knowledge.reindex`, `knowledge.acl` | Knowledge changes |

Existing rows for configuration, credential references, proposals, and privileged attempts are unchanged.

## 4. Navigation

- The top bar is Fleet, Incidents, Copilot, Knowledge, then Proposals, Audit, and Admin for the roles that have them. GraphDB and WaitDB are not in the top bar; they are tabs of a database. It scrolls instead of pushing the signed-in operator off the screen and collapses to icons below 1400 px.
- The landing page is the fleet table at full width. The database list no longer sits beside it a second time. A DBA or administrator adds a database from the fleet heading.
- Opening a database enters its workspace: the database list on the left and the tabs Overview, GraphDB, WaitDB, Events, and Copilot above the page. Choosing a tab keeps the database. Choosing another database keeps the tab.
- GraphDB and WaitDB are reached by opening a database and choosing the tab. `#/graph` and `#/wait` still show a database chooser when opened by address.
- Admin, Audit, Knowledge, Proposals, and Incidents have no database list.
- Sign-in offers Directory or Local only when a directory is configured. Otherwise it is a local sign-in with no toggle.
- Operator-facing text no longer names phases or `.env` settings. The fleet table and the database overview show the real count of open critical incidents.

## 5. Copilot

- **New readings.** `get_redo_logs`, `list_control_files`, `get_largest_segments`, and `get_background_processes` reuse the fixed GraphDB statements. Control files and segments are admin-only, like the user list. A non-administrator receives redo groups and sizes without member paths.
- **Wider fixed routing.** Redo logs, control files, segments and largest tables, background processes, locks, waits, the query using the most CPU, connected users, application programs and executables, disk space, failing jobs, "is everything ok", and the plain "database users" forms now reach a reading. Of 42 review phrasings, 41 are handled by fixed rules; before this phase 19 were.
- **Explanations and changes.** "What does ORA-01555 mean?" is answered as an explanation, not as an alert-log listing. "What is the SGA size?" on a database chat runs the reading instead of a general explanation. Add, resize, drop, restart, unlock, and similar requests are recognised as changes and are explained, never executed. "With SQL" asks for a draft.
- **Fleet questions.** `get_fleet_health` answers fleet chat from stored status: each enabled database, worst first, with last seen and open incidents. No database is queried.
- **Model-assisted routing.** When the fixed router finds no reading on a database chat and a model is available, the model may nominate up to three readings from the allowlist. The server keeps only allowlisted names the caller's role may read, fixes the database, runs them, and records a `route_question` entry in the answer's function list. The model is still given no tool to call and never runs SQL. `AI_ROUTER_FALLBACK=false` turns this off.
- **Catalog.** "Largest segments" is now a reading. "Schema storage" stays unsupported.
- **Readings server.** `python -m sentinel.mcp` now uses the MCP stdio framing of one JSON object per line. A client that sends a `Content-Length` header is still answered in that framing.

## 6. Knowledge upload

`POST /api/v1/knowledge/upload?filename=&category=&replace=` takes the raw file as the request body. Administrator only. The name must be a plain file name with a `.pdf`, `.docx`, `.txt`, or `.md` extension; the first bytes must agree with the extension; the size limit is `KNOWLEDGE_MAX_FILE_BYTES`. The file is written into `ora-19c-manual/` (Knowledge) or `Policy/`, then the existing intake scan parses, quarantines, or indexes it. An existing name needs `replace=true`. The Knowledge page shows the form and the Reindex button to an administrator only.

## 7. API changes

- `GET /api/v1/auth/session` and the sign-in response gain `directory_enabled`.
- `POST /api/v1/auth/password`.
- `GET /api/v1/admin/operators`, `POST /api/v1/admin/operators`, `POST /api/v1/admin/operators/{username}/enabled`, `POST /api/v1/admin/operators/{username}/password`. `POST /api/v1/admin/operators/{username}/role` now matches the username without regard to case.
- `POST /api/v1/knowledge/upload`.
- Fleet rows and the database detail return `critical_incident_count` as a number.
- New settings: `AUTH_MAX_FAILED_LOGINS`, `AUTH_LOCKOUT_MINUTES`, `METRICS_PUBLIC`, `AI_ROUTER_FALLBACK`.

## 8. Acceptance

- [x] A viewer or operator cannot register, change, or test-connect a database, and can still read the fleet.
- [x] A chat thread is readable only by its owner.
- [x] Incident actions need the permission and record the signed-in actor.
- [x] An administrator adds an operator, changes a role, disables an account, and resets a password from the Admin page.
- [x] A role change, a disable, and a password change each sign open local sessions out.
- [x] Repeated failed sign-ins are locked with 429 and every attempt is audited.
- [x] Production refuses to start with sign-in off, the default key, or an insecure cookie.
- [x] The landing page has no database sidebar; a database workspace has the list and the five tabs; switching database keeps the tab.
- [x] Redo logs, control files, segments, and background processes are answerable; listings are admin-only.
- [x] A fleet question is answered from stored status.
- [x] An administrator uploads a document from the portal and it is indexed.
- [ ] The full live check is recorded only after `Verify_Phase20_Real.ps1` is run against Oracle. A written spec is not a pass.

## 9. Tests

- `backend/tests/api/test_phase20_security.py`: registry permissions and audit rows; incident permission and actor; chat ownership; sign-in audit, throttle, operator management, and revocation; security headers; production guard; knowledge upload checks.
- `backend/tests/ai/test_phase20_routing.py`: 23 operator phrasings; every reading allowlisted, described, and presentable; explanation, change, and draft claims; fleet plan; model nomination limited to allowlisted readings the caller may read; nomination off; admin-only listings; fleet answer; MCP newline framing and the older framing.
- `frontend/src/Phase20.test.tsx`: local and directory sign-in, lockout message, unreachable API, own-password dialog, knowledge upload, and no upload for an operator.
- `frontend/src/FleetWorkspace.test.tsx`: landing page without sidebar; workspace tabs; sidebar keeps the view; GraphDB and WaitDB are absent from the top bar. `frontend/src/AdminPage.test.tsx`: operator management.
- Live check: `test_real_phase20\Verify_Phase20_Real.ps1`. `-CheckPrerequisites` does not connect to Oracle. The full run is a pass only after it is actually run.

## 10. Out of scope

Semantic embeddings (the 128-dimension hashing vector stays until a migration changes the column), a model-written summary on catalog answers, stored wait and memory samples for trends, a chat history list, a daily health report, Teams or webhook alerts, maintenance windows, per-database entitlements for reading, a shared sign-in throttle for several API processes, and revoking one directory or OIDC session before its expiry.
