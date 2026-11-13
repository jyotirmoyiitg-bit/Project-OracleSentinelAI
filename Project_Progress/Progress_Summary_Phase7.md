# Phase 7 Implementation Summary — Proposals, Auth, and Audit

**Status:** Implemented for Release 1. Proposals are stored and approved. They are not executed. Backend tests for the Phase 7 core on 12 Nov 2026: **293 passed, 1 skipped**. Frontend vitest at that point: **28 passed**. The live launcher is `test_real_phase7\Verify_Phase7_Real.ps1`. Its prerequisite check passed. The full launcher has not been run. If ports 8000 and 5173 are already in use, the script stops those listeners and then starts its own API and portal. A live enterprise identity provider was not connected. A production fleet load run and a database restore drill were not run.<br>
**Phase specification:** [Phase 7 — Operations, Auth, and Audit](../Phase_7_Operations_Auth_Audit.md)<br>
**Previous phase:** [Phase 6 handoff](./Progress_Summary_Phase6.md)<br>
**Next phase:** [Phase 8 — Privileged Operations](../Phase_8_Privileged_Operations.md) is recorded in [the Phase 8 handoff](./Progress_Summary_Phase8.md). Execution stays unimplemented.

Migration head is `0008`. It adds `operation_proposals`, `database_entitlements`, `audit_events`, and `database_targets.maintenance_restricted`. `target_credentials` already stored a secret reference and a rotation timestamp. The rotation API updates that reference and still refuses a raw password.

## What Was Implemented

### Proposals

The only accepted request is `Extend <tablespace> by <size> GB|MB`. `Extend USERS by 10 GB` becomes the fixed statement `ALTER TABLESPACE USERS ADD DATAFILE SIZE 10240M`. The tablespace name must be an Oracle identifier. The size must be a whole number of megabytes from 1 to 32768. The request text is not copied into the statement.

Before the row is stored, the service checks the database is enabled, it is not in a maintenance restriction, the caller is entitled to that database or its environment, `db_create_file_dest` has a measured value, and the measured free recovery area can hold the file. A platform administrator, including the existing `admin` role, bypasses the entitlement list. A viewer cannot read proposals. An operator can read them and cannot create them. A DBA can create and decide. The requester cannot approve their own proposal. A pending proposal expires after `PROPOSAL_EXPIRY_HOURS` (24 by default) and then cannot be approved. `POST .../execute` returns `Release 1 does not execute proposals.` The stored execution log stays empty.

### Auth and RBAC

The roles are `viewer`, `operator`, `dba`, `platform_admin`, and `security_auditor`. Existing `admin` keeps platform-administrator permissions, so the local `sentinel` account still lists database users and can administer the platform. Existing `auditor` keeps security-auditor permissions. Permissions are checked in the service and again at the API.

OIDC is an authorization-code flow with PKCE. It starts at `GET /api/v1/auth/oidc/start` when `AUTH_OIDC_ISSUER` and `AUTH_OIDC_CLIENT_ID` are set. The client secret is a `secret://` reference. The identity token is checked against the provider JWKS, issuer, audience, and nonce. An unknown role becomes `viewer`. If the provider cannot be reached, the start call returns 503 and local sign-in still works. The portal shows **Sign in with organization** only when OIDC is configured.

### Audit, administration, and metrics

`audit_events` records the principal, action, target, correlation id, outcome, and a SHA-256 evidence digest. SQLAlchemy rejects an update or delete, and the database trigger does the same for a direct SQL statement. The application refuses to purge audit rows.

`GET /api/v1/admin/platform` shows the scheduler, model, notification, role, retention, and rule settings. It does not show a key or a password. `GET /metrics` exposes request and proposal counters. OpenTelemetry spans are created with the API package and are not exported. Set `LOG_JSON=true` only when JSON logs are wanted; the default remains the current text logs.

The portal adds Proposals, Audit, and Admin. The proposal page states that Release 1 does not run the change. Navigation follows the signed-in role. The API still enforces the same rules when the navigation is bypassed.

## Live launcher

`test_real_phase7\Verify_Phase7_Real.ps1` follows the same path as the earlier stage scripts.

1. Check Python 3.14, Node, and Docker. `-CheckPrerequisites` stops there.
2. Load the project-root `.env`, then `test_real_phase7\.env`. The root file keeps credentials and keys. The phase file forces sign-in on, OIDC off, the scheduler on, synthetic mode off, and the language model off. It does not print those values.
3. Start Docker PostgreSQL and upgrade Alembic to head `0008`.
4. If something is already listening on `127.0.0.1:8000` or the portal port, stop that process, then start the API.
5. Sign in with the local bootstrap account. The password stays in the root `.env`. The session cookie is written to a temp file, loaded, and deleted. A proposal list before sign-in must return 401.
6. Reuse or register the selected TNS alias, test the Oracle connection, and wait for one collection that starts during the run.
7. List proposals, confirm `POST .../execute` returns 403, list audit events, and confirm `execution_allowed` is false on the platform summary.
8. Start the portal. With `-Interactive` it opens `#/operations` and waits until Enter. Without that switch it stops the API and portal it started. PostgreSQL and existing rows stay.

Run from the repository root:

```powershell
.\test_real_phase7\Verify_Phase7_Real.ps1 -CheckPrerequisites
.\test_real_phase7\Verify_Phase7_Real.ps1 -TnsAlias PDBORCL -Interactive
```

An API or portal already using ports 8000 and 5173 is stopped by the script before it starts its own.

## Copilot follow-on recorded with this handoff

These landed after the Phase 7 core and are part of the current tree. The live launcher above keeps the model disabled so it does not call a provider.

- “Is there a problem with this database?” reads this database’s alert file for `ORA-` lines in the last 24 hours. If none are present, the answer is exactly `There are no Oracle errors reported in the last 24 hours.` The model is not called. A date and time in evidence stay on one line.
- The 50 PDB and non-CDB operator questions are a catalog of fixed readings. A question the deployment cannot answer returns `INSUFFICIENT_DATA` or `UNSUPPORTED` instead of a made-up number. Guided commands are one row of category tabs. A tab opens its questions and a second click closes them.
- In `APP_ENV=dev` or `test`, the local admin principal `sentinel` can choose the local model, Claude, or OpenAI for one answer. Production and every other operator keep the configured provider. Each choice has its own model, base URL, key reference, privacy sentence, timeout, and output limit: `AI_LOCAL_*`, `AI_ANTHROPIC_*`, and `AI_OPENAI_*`. The configured default still uses `AI_HOSTED_*`. `AI_FALLBACK_PROVIDER` stays `disabled`. Key values stay on `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `LOCAL_AI_API_KEY`.
- The copilot has **New chat**, which clears the visible answers and opens another thread. Answers scroll inside the panel. The question box and **Send** stay at the bottom.

## Verification

- Backend: **293 passed, 1 skipped**. The skip is the existing Windows symlink test. New tests cover the 10 GB proposal, refused execution, entitlement denial, self-approval, expiry, role denial, unsafe request text, audit immutability, secret-reference rotation, OIDC session issuance, and an identity-provider outage that leaves local sign-in working.
- Frontend vitest: **28 passed**, including the proposal page showing the fixed SQL and no execute action. `tsc -b` passed.
- Ruff and mypy passed for the Phase 7 modules.
- Not run: a live enterprise IdP, a production-sized load test, and a restore of the application database.

## Resume / Next-Agent Pickup

1. Start the stage from the repository root. Listeners already on ports 8000 and 5173 are stopped first:

   ```powershell
   .\test_real_phase7\Verify_Phase7_Real.ps1 -TnsAlias PDBORCL -Interactive
   ```

   The script applies the migration before it starts the API. Head is `0008`.

2. Apply the migration alone when the API is already running and only the schema is missing:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
   ```

   Head is `0008`.

3. Do not add an execute endpoint or a privileged Oracle connection. Phase 8 is the gated execution phase and stays off.
4. Leave OIDC unset unless an identity provider is available. Do not put the client secret in `AUTH_OIDC_CLIENT_SECRET_REF`; use `secret://OIDC_CLIENT_SECRET`.
5. A DBA who is not a platform administrator needs a `database_entitlements` row before creating a proposal. The proposal also needs a measured `db_create_file_dest` and free recovery-area space.
6. The portal model list is only for the local admin `sentinel` when `APP_ENV` is `dev` or `test`. A missing OpenAI key leaves that option disabled. Restart the API after changing a key or a disclosure.
7. These edits are uncommitted. Commit only when requested.
