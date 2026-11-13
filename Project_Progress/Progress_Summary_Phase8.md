# Phase 8 Implementation Summary — Privileged Operations

**Status:** Implemented as a disabled placeholder. Release 1 does not execute a privileged change. The reserved table exists and is not written. A live enterprise review was not performed.<br>
**Phase specification:** [Phase 8 — Privileged Operations](../Phase_8_Privileged_Operations.md)<br>
**Previous phase:** [Phase 7 handoff](./Progress_Summary_Phase7.md)<br>
**Security review template:** [docs/privileged-operations-security-review.md](../docs/privileged-operations-security-review.md)<br>
**Architecture:** [docs/architecture.html](../docs/architecture.html) includes the proposal path and the disabled privileged path. The written companion is [docs/architecture.md](../docs/architecture.md). Updated 13 Nov 2026.

Migration head is `0009`. It adds `privileged_operations`. The application checks for that table at startup.

## What Was Implemented

`OPERATIONS_EXECUTION_ENABLED` defaults to false. Turning it on without `OPERATIONS_EXECUTION_APPROVED` prevents startup. Turning both on still does not send a statement to Oracle. `OPERATIONS_REQUIRE_DUAL_APPROVAL` stays true for a future review. A query parameter cannot turn execution on.

`GET /api/v1/operations/privileged` and `POST /api/v1/operations/privileged/{id}/execute` return HTTP 501. A caller without `privileged.read` or `privileged.execute` receives HTTP 403. Each attempt is written to `audit_events` with `statement_sent` false. The existing proposal execute route still returns `Release 1 does not execute proposals.`

The Proposals page states that privileged operations are disabled in Release 1 and has no execution control. The platform summary keeps `execution_allowed` and `privileged_execution_enabled` false.

No privileged Oracle account is created. The disabled module does not import a database driver, a shell, SQL*Plus, or RMAN.

## Live launcher

`test_real_phase8\Verify_Phase8_Real.ps1` follows the earlier stage scripts.

1. Check Python 3.14, Node, and Docker. `-CheckPrerequisites` stops there.
2. Load the project-root `.env`, then `test_real_phase8\.env`. The phase file keeps sign-in on, the language model off, and both execution flags false.
3. Start Docker PostgreSQL and upgrade Alembic to head `0009`.
4. Stop a listener already on port 8000 or the portal port, then start the API.
5. Sign in with the local bootstrap account. The password stays in the root `.env`.
6. Reuse or register the selected TNS alias and test the Oracle connection.
7. Confirm proposal execution is refused, both privileged endpoints return 501, and `privileged_operations` has no rows.
8. Start the portal. `-Interactive` opens `#/operations`.

```powershell
.\test_real_phase8\Verify_Phase8_Real.ps1 -CheckPrerequisites
.\test_real_phase8\Verify_Phase8_Real.ps1 -TnsAlias PDBORCL -Interactive
```

## Documentation

The architecture page now shows four boundaries. The language-model box is local, Claude, or OpenAI, with no failover. The portal boxes include New chat, the model list, and `#/operations`. PostgreSQL includes proposals, `audit_events`, and `privileged_operations`. Section 4 shows the proposal API returning 403 for execution and the privileged endpoints returning 501, with no connection to Oracle.

## Verification

- Privileged tests: **5 passed**. They cover the default-off flag, the rejected enablement without approval, HTTP 501, the viewer denial, the empty reserved table, and the absence of an execution tool in the disabled module. The existing proposal tests still pass, including the refused proposal execute route.
- The proposals page test checks the disabled sentence and the absence of an Execute button.
- `Verify_Phase8_Real.ps1 -CheckPrerequisites` passed. The full launcher was not run against Oracle. A live security review was not performed. The full backend suite was not rerun.

## Resume / Next-Agent Pickup

1. Do not add an executor, a privileged Oracle account, or a portal control that runs a change. The review template has to be completed first.
2. Head is `0009`. The Phase 7 launcher accepts `0008` or a later revision. The Phase 8 launcher requires `0009` and an empty `privileged_operations` table.
3. These edits are uncommitted. Commit only when requested.
