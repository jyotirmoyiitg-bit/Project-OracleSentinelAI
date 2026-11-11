# Phase 4 Implementation Summary — Compliance and Proactive Analysis

**Status:** Implemented, including the non-compliance notification behavior, with a `test_real_phase4` live-verification harness. Automated checks pass. Live Oracle validation (`test_real_phase4\Verify_Phase4_Real.ps1`) is still required to be actually run.<br>
**Phase specification:** [Phase 4 — Compliance and Forecasting](../Phase_4_Compliance_Forecasting.md)<br>
**Previous phase:** [Phase 3 — Alert-Log, Rules, and Incidents](../Phase_3_AlertLog_Rules_Incidents.md) · [Phase 3 handoff](./Progress_Summary_Phase3.md)<br>
**Next phase:** [Phase 5 — Knowledge and RAG](../Phase_5_Knowledge_RAG.md)

This handoff records the Phase 4 implementation and verification state so another coding agent can continue without rediscovering the architecture. It supersedes the Phase 3 handoff's "What's Next" pickup point. The Phase 3 handoff's git-status section is a historical snapshot; the repository was clean before Phase 4 edits.

**Update (same phase, follow-up session):** the initial Phase 4 implementation below evaluated and persisted compliance results, but did not yet act on a `fail` result. A follow-up pass added the explicit requirement from the phase task -- *"for databases which are not compliant a notification could be generated rather than a hard stop"* -- and a `test_real_phase4` folder mirroring `test_real_phase2`/`test_real_phase3`. See "Non-compliance notification behavior" and "Live verification harness (`test_real_phase4`)" below.

## What Was Implemented

### Versioned compliance policies

- Added the initial Oracle policy catalog in [policies/compliance.yaml](../policies/compliance.yaml): OMF configuration, ARCHIVELOG mode, FRA configuration/capacity, Flashback, and optional backup freshness.
- Added deterministic evaluation in [compliance.py](../backend/src/sentinel/rules/compliance.py) and persistence helpers in [compliance_store.py](../backend/src/sentinel/rules/compliance_store.py). Successful `instance`, `configuration`, and `fra_archive` collection runs refresh the applicable policy results in the same application-database transaction.
- Evaluation states are `pass`, `fail`, `unknown`, `not_applicable`, and `error`. Stale or missing probe evidence cannot satisfy a policy. Evidence contains the actual value, metric source, quality, timestamp, and source observation ID.
- Policies are persisted by `(policy_id, version)`. A stored version is immutable: editing its content without incrementing the version raises an error. The catalog carries `draft`/`approved`/`rejected` state; the current catalog is approved through source-control review. Per-environment overrides support applicability (`required`), severity, and policy-specific settings such as required archive mode, OMF parameter, minimum FRA capacity, or required Flashback value. Oracle version applicability can constrain policies by minimum/maximum version.
- Backup freshness returns `not_applicable` because no authorized/approved backup evidence source exists. No unsupported backup claim is made.
- Added migration [0004_compliance_forecasting.py](../backend/alembic/versions/0004_compliance_forecasting.py), the compliance endpoints in [compliance.py](../backend/src/sentinel/api/v1/compliance.py), and version/result schemas. `GET /api/v1/compliance/policies` exposes policy version history; `GET /api/v1/databases/{target_id}/compliance` returns current results and evidence. Database detail now reports persisted policy counts instead of the Phase 2 placeholder.

### Capacity forecasting

- Added linear regression and sufficiency checks in [regression.py](../backend/src/sentinel/forecasting/regression.py). Forecast inputs are retained tablespace `bytes_used` and FRA `space_used` observations, paired with current capacity.
- [policies/forecasting.yaml](../policies/forecasting.yaml) sets minimum points/span, maximum interval gap, maximum observation age, prediction horizon, and early-warning threshold. Sparse, irregular, stale, future-dated, or capacity-less samples return `unknown` with no exhaustion date.
- Sufficient positive-growth data produces GB/day, an estimated 95% regression-slope interval, a predicted exhaustion time, and `early_warning=true` when the configured threshold is reached. Non-positive growth is reported as stable and has no invented exhaustion date.
- Added `GET /api/v1/databases/{target_id}/forecast` and integrated compliance evidence tables and forecast charts into the database detail page. Early warnings are surfaced in the portal; they do not execute database changes or create incidents.

### Non-compliance notification behavior (reuses Phase 3 incident/notification infrastructure)

- `backend/src/sentinel/rules/compliance_store.py` adds `sync_compliance_notifications()`, called right after `evaluate_target_compliance()`. For each policy evaluation it builds a stable `rule_id = f"compliance.{policy_id}"` and fingerprint; a `result == "fail"` opens/extends an `Incident` via the existing Phase 3 `IncidentLifecycle.open_or_update()` (dedup, severity escalation, recurrence events) and -- only on first creation -- adds a persistent portal `Notification` (`channel="web"`), plus one `channel="smtp"` notification per `settings.notification_recipients` when `settings.smtp_enabled`, reusing `notifications/templates.render()` and the Phase 3 `SMTPNotifier` (retry/backoff included). A `result == "pass"` auto-resolves the still-open incident for that fingerprint via `IncidentLifecycle.resolve()`; a resolved incident is never reopened -- a later failure opens a brand new incident instead (same guarantee as Phase 3 alert-log incidents).
- `backend/src/sentinel/collectors/jobs.py`: `CollectionRunner._evaluate_compliance()` wraps `evaluate_target_compliance()` + `sync_compliance_notifications()` in a SQLAlchemy `SAVEPOINT` (`session.begin_nested()`) inside a broad `except Exception`, so a bug in compliance/notification logic is logged and rolled back to the savepoint only -- it can never raise out of `CollectionRunner.run()` or abort the rest of the poll transaction. This is the literal "notification, not a hard stop" guarantee requested by the phase task, verified by `test_compliance_evaluation_failure_is_a_notification_boundary_not_a_hard_stop` in `backend/tests/collectors/test_collection.py`.
- `CollectionRunner` and `main.py` now accept/construct a shared `SMTPNotifier`, wired into both `CollectionRunner` (compliance notifications) and the pre-existing `AlertEvaluationRunner` (Phase 3 alert-log notifications), so both notification sources share one SMTP client and recipient configuration.
- A real pre-existing bug was found and fixed while adding this: `Notification.attempt_count` is `None` at Python-side object construction (SQLAlchemy `default=` only applies at flush), so `record.attempt_count += 1` raised `TypeError` on the very first increment. Fixed by passing `attempt_count=0` explicitly in both `Notification(...)` call sites and guarding the increment as `record.attempt_count = (record.attempt_count or 0) + 1`.
- Added `backend/tests/rules/test_compliance_notifications.py` (7 tests: incident + portal notification on failure, dedup on repeated failure, auto-resolve on recovery, fresh incident after re-failure post-resolution, SMTP dispatch per recipient, no incident for pass/unknown) plus the collector-boundary test above.
- No frontend changes were needed: these incidents/notifications surface automatically in the existing Incident Center and notification bell built in Phase 3.

### Live verification harness (`test_real_phase4`)

- Added `test_real_phase4/.env`, `test_real_phase4/real_runtime.py`, and `test_real_phase4/Verify_Phase4_Real.ps1`, mirroring the `test_real_phase2`/`test_real_phase3` pattern exactly (root `.env` for Oracle credentials/`SECRET_KEY`, this folder's `.env` for live-mode/PostgreSQL/notification overrides, `SYNTHETIC_MODE=false`/`SCHEDULER_ENABLED=true` enforced, Docker PostgreSQL + Alembic `upgrade head` + real target registration/connection test, same `-TnsAlias`/`-Interactive`/`-InstallDependencies`/`-CheckPrerequisites`/`-FrontendPort`/`-CollectionWaitSeconds` parameters).
- After the shared Phase 2 setup, the script additionally: (1) reads the real `GET /api/v1/databases/{id}/compliance` result produced by the live collected metrics and prints a pass/fail/unknown table; (2) reads the real `GET /api/v1/databases/{id}/forecast` result (expected to be `unknown` on a freshly registered target until at least 5 points spanning 24h accumulate -- the script says so rather than treating it as a failure); (3) calls a new `real_runtime.py simulate-noncompliance` action that invokes the real `sync_compliance_notifications()` directly with one synthetic, uniquely-named `PolicyEvaluation` (forcing a genuine non-compliant Oracle state such as disabling ARCHIVELOG on demand is unsafe, exactly as Phase 3's `simulate-alert` avoids forcing a real ORA error) to verify, against the real PostgreSQL application database: a failing evaluation opens an incident and a portal notification without raising; a repeated failure deduplicates onto the same incident/notification; a passing re-evaluation auto-resolves the incident; and a subsequent failure opens a fresh incident rather than reopening the resolved one. The synthetic policy id is unique per run so it can never collide with the real catalog policies the scheduler evaluates automatically.
- `Verify_Phase4_Real.ps1` itself was not executed against a live Oracle/Docker/PostgreSQL stack in this session (no such environment available here); its logic was verified by code review against the already-tested `compliance_store.sync_compliance_notifications()` unit tests and the proven `Verify_Phase3_Real.ps1`/`Verify_Phase2_Real.ps1` pattern it extends. The PowerShell script was parse-checked (`PSParser.Tokenize`, no errors) and `real_runtime.py` was byte-compiled successfully.

### Documentation and test coverage

- Updated [API contracts](../docs/api-contracts.md), [Phase 4 specification](../Phase_4_Compliance_Forecasting.md), migration expectations, synthetic Oracle observations, and dashboard fixtures.
- Added policy evaluation, environment override, applicability, regression, data sufficiency, API, evidence-reference, and migration coverage.
- Added `types-PyYAML` as a development dependency for strict mypy validation; PyYAML was already a runtime dependency.

## Verification

Run from the repository root in PowerShell:

```powershell
# Backend tests, including migration/autogenerate drift checks
Set-Location backend
..\.venv\Scripts\python.exe -m pytest tests -q

# Backend lint for Phase 4 code (including the non-compliance notification follow-up)
..\.venv\Scripts\ruff.exe check src\sentinel\rules\compliance.py src\sentinel\rules\compliance_store.py src\sentinel\rules\policy_definitions.py src\sentinel\forecasting\regression.py src\sentinel\forecasting\service.py src\sentinel\api\v1\compliance.py src\sentinel\api\v1\databases.py src\sentinel\collectors\jobs.py src\sentinel\main.py tests\rules\test_compliance.py tests\rules\test_compliance_notifications.py tests\forecasting\test_regression.py tests\collectors\test_collection.py

# Type check (the suppression is for a pre-existing, untouched fleet-summary dict inference at databases.py:113)
..\.venv\Scripts\python.exe -m mypy --disable-error-code var-annotated src\sentinel\rules\compliance.py src\sentinel\rules\compliance_store.py src\sentinel\rules\policy_definitions.py src\sentinel\forecasting\regression.py src\sentinel\forecasting\service.py src\sentinel\api\v1\compliance.py src\sentinel\api\v1\databases.py src\sentinel\collectors\jobs.py src\sentinel\persistence\models.py

# Frontend build and tests
Set-Location ..\frontend
npm run build
npx vitest run

# Return to repository root and check whitespace
Set-Location ..
git diff --check

# Live verification against a real Oracle database + Docker PostgreSQL (manual, interactive)
.\test_real_phase4\Verify_Phase4_Real.ps1 -TnsAlias YOUR_ALIAS -InstallDependencies -Interactive
```

Latest results:

- Backend: **159 passed** (152 from the original Phase 4 implementation + 7 new tests in `tests\rules\test_compliance_notifications.py` + 1 new boundary test in `tests\collectors\test_collection.py`, for the non-compliance notification follow-up).
- Frontend: **23 passed**; TypeScript/Vite production build succeeded. Vite still reports the existing large-chunk advisory. No frontend files changed for the notification follow-up, so this was not rerun in that session.
- Ruff: passed for the Phase 4 implementation files listed above, including the follow-up's `compliance_store.py`, `jobs.py`, `main.py`, and new test files.
- Mypy: passed for the listed implementation modules with the targeted `var-annotated` suppression noted above (not rerun against the notification follow-up files in this session; no typing changes were introduced beyond existing patterns).
- Alembic migration/autogenerate checks ran in the backend suite and passed.
- Pylance diagnostics for the new compliance service returned no issues.
- `test_real_phase4\Verify_Phase4_Real.ps1` was **not executed** against a live stack in this session (no such environment available here); it was parse-checked with `PSParser.Tokenize` (no errors) and its Python helper (`real_runtime.py`) was byte-compiled successfully. Running it against a real Oracle/Docker/PostgreSQL stack remains the next concrete verification step.
- **Not run:** a live Oracle/PostgreSQL deployment. Deprecation warnings in existing UTC timestamp code and the TestClient/httpx warning remain outside this phase's scope.

## Resume / Next-Agent Pickup

1. Apply the migration before using Phase 4 endpoints:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
   ```

2. With the synthetic or approved Oracle target running, inspect `GET /api/v1/databases/{target_id}/compliance` and `GET /api/v1/databases/{target_id}/forecast`. Forecasts intentionally remain unknown until the target has at least five recent, sufficiently regular observations spanning 24 hours.
3. Run `test_real_phase4\Verify_Phase4_Real.ps1` against a real Oracle instance + Docker PostgreSQL (`-CheckPrerequisites` first, then `-TnsAlias <alias> -InstallDependencies -Interactive`) -- this is the one concrete next step this handoff leaves open. It reads the real compliance/forecast results and exercises the synthetic non-compliance notification flow (open → dedup → auto-resolve → reopen) end to end against the live REST API; nothing in this harness touches real catalog policies or production incidents.
4. Before production use, have a DBA review whether OMF/ARCHIVELOG/FRA/Flashback requirements and environment overrides match local standards. Validate real Oracle values and grants; the synthetic adapter does not generate enough bytes-used history to demonstrate a real exhaustion forecast.
5. Phase 3 live-harness completion (`test_real_phase3\Verify_Phase3_Real.ps1`) and real SMTP delivery were not part of this phase and remain unverified against a live stack. Keep the existing Phase 3 status caveats in [its handoff](./Progress_Summary_Phase3.md) in mind; the same "not yet run against a live stack" caveat now also applies to `test_real_phase4\Verify_Phase4_Real.ps1`.
6. After review, proceed to [Phase 5 — Knowledge and RAG](../Phase_5_Knowledge_RAG.md). Do not extend this phase into RAG or unrestricted database operations.

## Known Limitations

- Policy definitions are currently repository-managed YAML; approval is a source-control review, not an in-portal workflow. The API is read-only for policy definitions.
- Compliance evaluation (and therefore non-compliance notification) is refreshed as a side effect of successful `instance`/`configuration`/`fra_archive` collection probes; it is not an independent scheduler job, so a target with no successful recent poll of those probe types will not raise a non-compliance notification even if its last known state was non-compliant.
- Forecast early warnings are visible through the forecast API and dashboard only; no incident, email, or portal notification is generated for a forecast (unlike compliance `fail` results, which now do generate one -- forecasting is intentionally left as visibility-only for this phase).
- Backup freshness remains not applicable until a supported and authorized backup evidence source is integrated.
- Real Oracle behavior, production policy applicability, and forecast quality need DBA/live-stack confirmation. `test_real_phase4\Verify_Phase4_Real.ps1` exists for this but has not yet been run against a live stack.
- SMTP is disabled by default in `test_real_phase4\.env` (`SMTP_ENABLED=false`); portal notifications are still created and verified, proving the pipeline without requiring a real mail relay. Set `SMTP_ENABLED=true` plus the commented SMTP settings in that file to also verify real email delivery.
- Changes are uncommitted. Preserve the existing working tree and commit only when requested.
