# Phase 3 Implementation Summary — Alert-Log Ingestion, Rules, and Incidents

This document summarizes the steps taken to implement **Phase 3** of Oracle Sentinel AI, as defined in `Phase_3_AlertLog_Rules_Incidents.md`, building on the already-completed Phase 2 (Central Collection). It is written to be **self-sufficient for a fresh session (human or AI coding assistant)** to resume work without re-deriving context from scratch — see "How We Got Here", "Environment Setup State", "Exact Verification Commands", and "What's Next (Pickup Point)" below in addition to the implementation detail.

## Latest Changes and Current Handoff (2026-11-09)

This section supersedes the original baseline test counts, SMTP defaults, and live-verification assumptions in sections 1-12 below.

### Multi-database portal and persistent registration

- Added [DatabaseSidebar](../frontend/src/components/DatabaseSidebar.tsx): all registered targets appear on the left, with green healthy, yellow warning, red critical/down, and gray unknown/stale/disabled indicators. The list follows every API page rather than stopping at 100 targets.
- The right pane displays the selected database's existing detail view. **Fleet overview** clears the selection and shows health across the entire registry, not just the current table page or filter. Existing Incident Center, alert-log pages, and the notification bell remain available.
- Added [DatabaseRegistrationPage](../frontend/src/DatabaseRegistrationPage.tsx): **Add database** loads aliases from the backend server's `tnsnames.ora`; an operator selects one, enters display name/environment/optional owner, optionally tests connectivity, and registers it. Registration rechecks connectivity before saving; failed connections are not saved and already registered aliases return 409, including case variants.
- Database metadata is committed to PostgreSQL and reloaded after application restart. TNS connection descriptors remain in the server file; browser storage does not hold registrations or Oracle credentials. The form uses backend-configured monitoring credentials/secret references.
- TNS entries are not automatically imported. Listener/external-procedure aliases may not represent databases, and multiple aliases can refer to the same database. Four distinct database services can be registered and monitored together; the configured worker count bounds concurrent jobs, not registry size.
- Relative `TNS_ADMIN` paths now resolve from the project root for both discovery and connections. New health remains unknown until fresh monitoring evidence exists; a successful connection test alone does not turn it green.
- With `SCHEDULER_ENABLED=true`, all enabled registrations reload into the collector after restart; newly added targets are picked up at the next reconciliation (30 seconds by default). The portal warns when scheduling is disabled.
- Desktop uses a left-sidebar/right-content layout; mobile stacks database navigation above the detail pane. Auto-refresh defaults to 30 seconds and respects an explicitly saved interval, including Off.

### Portal-first notifications and optional SMTP

- Every newly opened incident creates one persistent `web` notification in the incident transaction, regardless of SMTP settings or recipient configuration. Recurrences do not create duplicate portal alerts; old incidents are not backfilled.
- Added [NotificationBell](../frontend/src/components/NotificationBell.tsx): unread count, paginated panel, mark-read actions, incident links, explicit API errors/retry, and refresh integration.
- Read status persists in PostgreSQL and is shared across operators in this pre-authentication portal. Reading an alert does not acknowledge or resolve its incident.
- `SMTP_ENABLED=false` is the current default in application settings, the root local `.env`, `.env.example`, and the Phase 3 live overrides. Disabled SMTP opens no SMTP connection and creates no email attempt records.
- To enable email later, set `SMTP_ENABLED=true`, configure relay/from-address/recipients and any required secret reference, then restart the backend. The live harness's `.env` overrides the root configuration. SMTP failures remain separate from portal alert delivery.
- Existing notification storage supports both channels; these additions require no new migration beyond `0003`.

### Live harness fixes

- Fixed `DetachedInstanceError`: the synthetic harness no longer constructs an unused Oracle adapter after its session closes; the scheduled alert runner loads target credentials before detaching, matching the metric collector.
- Fixed reruns selecting an old resolved incident while production cooldown suppressed a new firing. Each run now uses a uniquely named temporary copy of the configured YAML event rule (`verify_phase3_<UUID>_<rule id>`) and verifies the exact returned incident ID.
- Production cooldowns, database rule overrides, and historical incidents are not reset or deleted. This isolated check exercises the pipeline, not the current production override policy. The selected YAML rule must be enabled and have `hysteresis=1` for the single-event check.
- Lease retries reuse one run UUID and stable event dedup key. A fresh invocation gets a new UUID and can run immediately after the previous verification was resolved. Temporary YAML is removed; test database records remain for audit.
- The PowerShell harness checks the portal notification and paginates the bell feed. SMTP delivery is checked separately when email attempt records exist.

### Added API surfaces

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/databases/summary` | Full-registry health counts and scheduler-enabled flag |
| `GET /api/v1/databases/tns-aliases` | Alias names and registration state, without connection descriptors |
| `POST /api/v1/databases/test-tns-connection` | Test selected alias using backend credentials without registering |
| `POST /api/v1/databases/register-from-tns` | Validate/test the alias and persist its registration |
| `GET /api/v1/portal-notifications` | Paginated portal inbox and global unread count |
| `POST /api/v1/portal-notifications/{id}/read` | Idempotently persist shared read status |

The existing notification log also accepts `channel=smtp` or `channel=web`. See [API contracts and configuration](../docs/api-contracts.md) for request/response details.

### Latest verification and remaining checks

- **60 targeted backend tests passed** across API, collectors, and both live-launcher test modules. These cover four registrations reloading into fresh database sessions/scheduler instances, alias validation, persistence, duplicate rejection, connection failures, path resolution, full-fleet summaries, portal notifications, and repeatable synthetic runs. This is not a new full-backend-suite count.
- **All 23 frontend tests passed**, and the TypeScript/Vite production build succeeded. The existing bundle-size warning remains.
- Focused registration API/test lint, Python syntax checks, patch whitespace checks, and the earlier PowerShell harness syntax check passed.
- An isolated synthetic backend verified connection testing, registration, and reload through the portal proxy. Browser checks confirmed desktop side-by-side and mobile stacked layout without horizontal overflow. Automated frontend tests cover registration interactions and sidebar selection.
- User-provided live runs established Oracle connectivity and successful synthetic polling, but exposed the session-detachment and old-incident-selection failures above. **No complete successful live harness run has been confirmed after the latest fixes.** Real multi-Oracle/PostgreSQL validation and actual SMTP delivery remain unverified.
- Changes remain uncommitted; preserve the working tree and commit only when requested. Temporary verification servers/configuration/data were cleaned up.

### Resume / run commands

Run from the project root:

```powershell
# Latest targeted backend coverage (60 passed in the last run)
.\.venv\Scripts\python.exe -m pytest backend\tests\api backend\tests\collectors backend\tests\test_real_launcher.py backend\tests\test_phase3_real_launcher.py -q

# Complete frontend validation (23 tests in the last run)
Push-Location frontend
npm run build
npx vitest run
Pop-Location

# Live verification and interactive portal
.\test_real_phase3\Verify_Phase3_Real.ps1 -Interactive
```

In the portal, use **Add database** to register additional database service aliases. Press **Enter in the launcher's PowerShell window** to stop its backend/frontend; PostgreSQL remains running. `docker compose stop` also stops PostgreSQL without deleting its data.

Before starting the next phase, confirm a successful live rerun, register/test the intended real database services, and verify sidebar health and portal notifications. SMTP can remain disabled for this phase. The original implementation history follows.

## How We Got Here (Phase 0 → 1 → 2 → 3)

- **Phase 0 (Bootstrap)** — repo scaffolding, `backend/`/`frontend`/`scripts`/`tns` layout, initial `pyproject.toml`, base FastAPI app skeleton. See `Phase_0_Bootstrap.md`.
- **Phase 1 (Oracle Connection)** — `python-oracledb` thin-mode connectivity, `tnsnames.ora` parsing, encrypted-credential support (Fernet via `SECRET_KEY`), the `GSYSTEM` read-only provisioning SQL scripts, and the standalone `scripts/test-oracle-connection.py` CLI diagnostic tool. See `Phase_1_Oracle_Connection.md`.
- **Phase 2 (Central Collection)** — `DatabaseTarget` registry, `CollectionRunner`/checkpoint-lease scheduler (`collectors/jobs.py`, `collectors/checkpoints.py`), metric normalization and persistence, dashboard API (`api/v1/dashboard.py`), the fleet/detail React frontend, and the `test_real_phase2/` live-verification harness (`.env` + `real_runtime.py` + `Verify_Phase2_Real.ps1`). This phase was already complete and verified when Phase 3 work began in this session.
- **Phase 3 (this phase — Alert-Log Ingestion, Rules, and Incidents)** — built directly on top of Phase 2's collector/checkpoint/adapter infrastructure: reused the same lease-based scheduling pattern for a new `alert_log` checkpoint type, added a rules engine + incident lifecycle + notifications on top of the existing `DiagnosticEvent`/`DatabaseTarget` domain model, and mirrored `test_real_phase2/` into a new `test_real_phase3/` harness. Full detail of what was built is in sections 1–10 below.

## 1. Persistence Layer

- Added new SQLAlchemy models to `backend/src/sentinel/persistence/models.py`:
  - `RuleDefinition` — DB-stored override/enablement state for rules (YAML files remain source of truth for new rules).
  - `RuleState` — per rule/target (and optional scope key, e.g. tablespace name) hysteresis and cooldown bookkeeping.
  - `Incident` — deduplicated incident grouping recurring rule firings by fingerprint, with `new -> acknowledged -> resolved` lifecycle state.
  - `IncidentEvent` — immutable audit trail of incident state transitions (opened, recurrence, acknowledged, resolved).
  - `Notification` — delivery attempt record for incident notifications, with `dedup_id` to prevent repeat notification storms.
- Added relationships from `DatabaseTarget` to `RuleState`/`Incident` with cascading deletes.
- Created Alembic migration `0003_alert_rules_incidents.py` for the new tables; verified zero schema drift against the ORM metadata via `compare_metadata` (upgrade/downgrade path `0001 -> 0002 -> 0003` validated).

## 2. Configuration

- Extended `backend/src/sentinel/config/settings.py` with Phase 3 settings:
  - `notification_recipients`, `notification_max_retries`, `notification_retry_backoff_seconds`.
  - `rules_path`, `alert_poll_interval_seconds`, `alert_max_lookback_hours`.

## 3. Rules Engine (`backend/src/sentinel/rules/`)

- `definitions.py` — `RuleCondition` (safe `metric operator value` comparison; never `eval()`) and `RuleDefinition` dataclasses, including an `is_event_rule` property for ORA-code/event-sourced rules.
- `yaml_loader.py` — parses rule expressions with a regex + `ast.literal_eval` grammar (rejects anything unsafe, e.g. function calls or `__import__`), loads a YAML document's `rules:` list, rejects duplicate rule ids, and loads all `*.yaml`/`*.yml` files from a directory.
- `engine.py` — pure, DB-free `RulesEngine.evaluate()`: supports hysteresis (fires only after N consecutive matches), cooldown (suppresses re-firing within `cooldown_minutes`), per-scope independent state (e.g. per-tablespace), event-code rules, and full suppression during maintenance windows.
- `store.py` — `sync_rule_definitions()` reconciles YAML-defined rules with DB-stored operator overrides (`source="yaml"` rows resync every call; `source="override"` rows keep DB-controlled `enabled`/`severity`/`cooldown_minutes`/`hysteresis`).
- `rules/default.yaml` — shipped initial rule set (tablespace/FRA thresholds, ORA-00257/01653/04031).

## 4. Incidents (`backend/src/sentinel/incidents/`)

- `fingerprinting.py` — deterministic SHA-256 fingerprint from `rule_id + target_id + scope_key`.
- `lifecycle.py` — `IncidentLifecycle` state machine: `open_or_update()` creates a new incident or extends an existing non-resolved one (escalating severity only when higher-ranked, writing a `recurrence` audit event); `acknowledge()`/`resolve()` enforce valid transitions (`new -> acknowledged -> resolved`, `new -> resolved`) and raise `InvalidTransitionError` on invalid ones without mutating state. Resolved incidents never reopen — a new fingerprint match after resolution creates a fresh incident.

## 5. Notifications (`backend/src/sentinel/notifications/`)

- `templates.py` — renders subject/body (severity, target name, rule id, message, incident id).
- `smtp.py` — `SMTPNotifier` with retry/backoff on injectable `smtp_client_factory`/`sleep_fn`, short-circuiting to `SMTPNotConfigured` when SMTP isn't set up, and deduplicating sends via `dedup_id`.

## 6. Collector Integration

- Added `collectors/alert_flow.py` (`AlertEvaluationRunner`), wiring: alert-log polling (via the adapter's `poll_events`) → new `DiagnosticEvent` persistence (deduped by `(target_id, source_event_key)`) → rule evaluation → incident open/update → per-recipient notification dispatch.
- Registered the new runner alongside existing probes in `collectors/scheduler.py`, reusing the same checkpoint-lease pattern as `CollectionRunner`.
- Fixed a real bug discovered via testing: `Notification.attempt_count` was `None` at object construction (SQLAlchemy Python-side column defaults only apply at flush), which crashed the first increment in `_notify`; fixed by passing `attempt_count=0` explicitly.

## 7. API Layer

- Added `api/v1/incidents.py` (list/detail/acknowledge/resolve incidents) and `api/v1/events.py` (alert-log timeline with linked incidents), registered in `main.py` with a Phase 3 schema startup check.

## 8. Frontend

- Added an **Incident Center** (`IncidentCenter.tsx`, route `#/incidents`): filterable/paginated incident list, detail view with evidence, audit trail, and Acknowledge/Resolve actions (with inline 409-error handling).
- Added a **Logs/Events page** (`EventsPage.tsx`, route `#/databases/:id/events`): searchable alert-log event table with code/severity/date filters and links to associated incidents.
- Added supporting components (`IncidentStatusBadge.tsx`, `SeverityBadge.tsx`), new API client functions in `api.ts`, new types in `types.ts`, new nav entry in `AppHeader`, and new CSS rules in `styles.css`, all following existing flat-file/hash-router conventions.

## 9. Testing

- **Backend**: added `backend/tests/rules/`, `backend/tests/incidents/`, `backend/tests/notifications/`, and `backend/tests/integration/test_alert_flow.py`, covering hysteresis/cooldown/scoping behavior, the incident state machine, SMTP retry logic, and an end-to-end ORA-00257 scenario (including dedup on overlapping polls and zero-AI-dependency verification).
- **Frontend**: added `IncidentCenter.test.tsx` (list → detail navigation, acknowledge flow) following the existing Vitest/Testing Library conventions.
- Final verification: `pytest backend/tests -q` → **95 passed**; `npm run build` and `vitest run` → build succeeds, **8/8 tests pass**; `create_app()` constructs successfully with the new routers registered.

## 10. Live Verification Harness

- Created `test_real_phase3/` mirroring `test_real_phase2/`:
  - `.env` — live Postgres/Oracle overrides plus Phase 3 settings (rules path, notification recipients, SMTP left unconfigured by default with instructions to enable a real relay).
  - `real_runtime.py` — adds a `simulate-alert` action that injects one synthetic ORA-coded event through the real pipeline (since forcing a genuine Oracle failure on demand is unsafe).
  - `Verify_Phase3_Real.ps1` — mirrors Phase 2's interactive/non-interactive flow, extended to verify dedup incident creation, notification recording, and the acknowledge/resolve lifecycle via the live REST API.

## 11. Detailed Phase 3 Testing Results

- **Backend unit/integration tests** (`backend/tests/rules/`, `backend/tests/incidents/`, `backend/tests/notifications/`, `backend/tests/integration/test_alert_flow.py`), run via `pytest backend/tests -q`:
  - Final result: **95 passed, 0 failed** (52 of these are new Phase 3 tests; the rest are the pre-existing Phase 0–2 suite, confirmed still green — i.e. Phase 3 caused no regressions).
  - `rules/test_engine.py` — hysteresis (fires only on the Nth consecutive match), cooldown suppression while still matching, reset of `consecutive_matches` on a non-match, independent state per `scoped_metrics` key (e.g. per-tablespace), event-code (`diagnostic_events.code`) rule firing, and full suppression during `maintenance=True`.
  - `rules/test_yaml_loader.py` — safe expression parsing vs. rejection of unsafe/malformed input (`__import__`, function calls, missing operator), duplicate-id rejection, directory-wide `*.yaml`/`*.yml` loading.
  - `rules/test_store.py` — YAML-sourced rows resynced every call; override-sourced rows keep DB-controlled `enabled`/`severity`/`cooldown_minutes`/`hysteresis`.
  - `incidents/test_fingerprinting.py` / `test_lifecycle.py` — deterministic fingerprinting, recurrence vs. new-incident creation, severity escalation only when higher-ranked, valid/invalid state transitions (`InvalidTransitionError` without mutation), and resolved incidents never reopening.
  - `notifications/test_smtp.py` / `test_templates.py` — `SMTPNotConfigured` short-circuit, retry/backoff attempt counts and timing (via injected fakes, no real network/sleep), template content checks.
  - `integration/test_alert_flow.py` — end-to-end synthetic ORA-00257 scenario: one event in → one `DiagnosticEvent` + one `Incident` + one sent `Notification`; a repeated/overlapping poll produces no duplicates; confirmed zero AI/LLM dependency (default `Settings` with AI fields disabled).
  - **A real production bug was caught by this test run before being fixed**: `Notification.attempt_count` was `None` immediately after `Notification(...)` construction (SQLAlchemy Python-side `default=0` only applies at flush, not at object construction), so `record.attempt_count += 1` in `collectors/alert_flow.py`'s `_notify` raised `TypeError` on every brand-new notification — this silently rolled back the *entire* transaction (checkpoint, new `DiagnosticEvent` rows, rule state, and the incident itself), violating the module's "one failing target must never stop other jobs" contract. Fixed by passing `attempt_count=0` explicitly in the constructor. Initial test run before the fix: 50 passed / 2 failed (both failures in `test_alert_flow.py`, both traced to this bug); after the fix: all 95 tests pass.
- **Frontend tests**: `npm run build` (tsc + vite) succeeds with zero TypeScript/build errors; `npx vitest run` → **8/8 passed** (6 pre-existing `App.test.tsx` tests still green + 2 new `IncidentCenter.test.tsx` tests: list→detail navigation, acknowledge flow with inline success message). No lint script exists in `frontend/package.json` (only `dev`/`build`/`test`), so no separate lint step applies.
- **Not yet executed**: `test_real_phase3/Verify_Phase3_Real.ps1` against a real live Oracle/PostgreSQL/Docker stack — this is the one genuinely untested piece of Phase 3 (see "What's Next" below). It was validated only by code review against the proven `test_real_phase2` pattern.

## 12. Key Decisions & Trade-offs

- **YAML rules + DB override table, instead of DB-only rules.** Rules are authored as readable YAML (`rules/default.yaml`) and parsed with a regex + `ast.literal_eval` grammar — never `eval()` — so operators can review/diff rule changes in version control and no arbitrary code execution path exists. The DB `RuleDefinition` table exists only to let an operator enable/disable or tune severity/cooldown/hysteresis per rule at runtime *without* editing files; on every sync, YAML remains the source of truth for `scope`/`expression`/`message`, while DB-sourced overrides win for `enabled`/`severity`/`cooldown_minutes`/`hysteresis`. Trade-off: two places to look when debugging "why didn't this rule fire" (the YAML file and the DB override row).
- **Hysteresis + cooldown baked into a pure, DB-free `RulesEngine.evaluate()`.** Keeping the engine pure (no DB access, explicit `RuleContext`/state objects in and out) made it trivially unit-testable (`backend/tests/rules/test_engine.py`) and keeps all persistence/transaction concerns in the collector layer (`alert_flow.py`). Trade-off: the collector must carefully load/save `RuleState` rows around each `evaluate()` call — an extra integration seam, exercised by `test_alert_flow.py`.
- **Fingerprint-based incident dedup (`rule_id + target_id + scope_key` → SHA-256) instead of a time-window heuristic.** This guarantees the same recurring condition (e.g. the same tablespace breaching the same threshold) always maps to the same incident deterministically, and resolved incidents are never reopened — a fresh fingerprint match after resolution always creates a new incident, which is important for audit correctness (an incident's full history should map to one problem occurrence, not blur multiple).
- **SMTP left unconfigured by default, rather than requiring a real mail server for Phase 3 to be considered "done".** `SMTPNotifier` short-circuits to a recorded `SMTPNotConfigured` failure rather than raising, so the full pipeline (event → rule → incident → notification *attempt*) is provable end-to-end in both the automated test suite and the `test_real_phase3` live harness without needing real SMTP credentials in this environment. Trade-off: actual email delivery is unverified; enabling a real relay (commented instructions are in `test_real_phase3/.env`) is a follow-up, not a blocker.
- **`test_real_phase3/` mirrors `test_real_phase2/` exactly (same `.env` + `real_runtime.py` + `Verify_*_Real.ps1` shape)** rather than introducing a new harness pattern, to keep operational knowledge (how to run live verification) consistent across phases. Because forcing a genuine Oracle alert-log failure on demand isn't safe/practical, `real_runtime.py` adds a `simulate-alert` action that injects one synthetic ORA-coded event through the real pipeline instead.

## Environment Setup State

- **Python virtual environment**: `.venv` exists at the project root (`D:\Projects\Project-OracleSentinelAI\.venv`), using **Python 3.14.6**. The `sentinel` backend package is already installed into it (editable install); `import sentinel` succeeds via `.\.venv\Scripts\python.exe`.
- **Backend dependencies**: installed via `pip install -e backend[dev]` (installs `fastapi`, `sqlalchemy`, `alembic`, `python-oracledb`, `apscheduler`, `pytest`, `ruff`, `mypy`, etc. — see `backend/pyproject.toml`); `pyyaml` was additionally required for the Phase 3 rules loader and is installed.
- **Frontend dependencies**: `frontend/node_modules` is populated (standard `npm install` was run against `frontend/package.json`); `npm run build` and `npm run test` (vitest) both run clean from `frontend/`.
- **No system-wide Oracle Instant Client is required** — `python-oracledb` runs in thin mode throughout.
- A fresh session should **not** assume any of the above needs to be redone — only re-verify with the commands below if something seems broken (e.g. after a long gap or a `git pull`).

## Exact Verification Commands (run these first in a new session)

```powershell
# 1. Confirm the venv and backend package are intact
cd D:\Projects\Project-OracleSentinelAI
.\.venv\Scripts\python.exe --version              # expect Python 3.14.6
.\.venv\Scripts\python.exe -c "import sentinel"    # expect no output / no ImportError

# 2. If the above fails, reinstall:
.\.venv\Scripts\python.exe -m pip install -e backend[dev]
.\.venv\Scripts\python.exe -m pip install pyyaml

# 3. Re-run the full backend test suite (expect 95 passed)
cd D:\Projects\Project-OracleSentinelAI\backend
..\.venv\Scripts\python.exe -m pytest tests -q

# 4. Re-run just the Phase 3 suites in isolation (expect 52 passed)
..\.venv\Scripts\python.exe -m pytest tests\rules tests\incidents tests\notifications tests\integration\test_alert_flow.py -q

# 5. Re-run the frontend build + tests (expect build success, 8/8 tests)
cd D:\Projects\Project-OracleSentinelAI\frontend
npm run build
npx vitest run

# 6. Verify Alembic migrations apply cleanly (0001 -> 0002 -> 0003, no drift)
cd D:\Projects\Project-OracleSentinelAI\backend
..\.venv\Scripts\python.exe -m pytest tests\test_migrations.py -q

# 7. Live end-to-end check (latest successful completion still needs confirmation)
cd D:\Projects\Project-OracleSentinelAI
.\test_real_phase3\Verify_Phase3_Real.ps1 -Interactive
.\test_real_phase3\Verify_Phase3_Real.ps1   # smoke check; no -NonInteractive switch exists
```

## Git Status Note

- Repository is on branch `master`, tracking `origin/master`.
- **Nothing from Phase 3 has been committed yet** — all changes are sitting as uncommitted working-tree modifications/untracked files:
  - Modified: `backend/pyproject.toml`, `backend/src/sentinel/api/v1/schemas.py`, `backend/src/sentinel/collectors/scheduler.py`, `backend/src/sentinel/config/settings.py`, `backend/src/sentinel/main.py`, `backend/src/sentinel/persistence/models.py`, `backend/tests/test_migrations.py`, `frontend/src/App.tsx`, `frontend/src/api.ts`, `frontend/src/styles.css`, `frontend/src/types.ts`, `requirements.txt`.
  - Untracked (new): `Project_Progress/`, `backend/alembic/versions/0003_alert_rules_incidents.py`, `backend/src/sentinel/api/v1/events.py`, `backend/src/sentinel/api/v1/incidents.py`, `backend/src/sentinel/collectors/alert_flow.py`, `backend/src/sentinel/incidents/`, `backend/src/sentinel/notifications/`, `backend/src/sentinel/rules/`, `backend/tests/incidents/`, `backend/tests/integration/`, `backend/tests/notifications/`, `backend/tests/rules/`, `frontend/src/EventsPage.tsx`, `frontend/src/IncidentCenter.test.tsx`, `frontend/src/IncidentCenter.tsx`, `frontend/src/components/IncidentStatusBadge.tsx`, `frontend/src/components/SeverityBadge.tsx`, `rules/`, `test_real_phase3/`.
- A fresh session should treat all of this as **live, uncommitted work** — i.e. safe to keep editing directly, but remember no rollback point exists via git if something goes wrong; commit explicitly once satisfied (only when asked to, per project convention).

## Outcomes Summary

- Original Phase 3 baseline: 95/95 backend tests passed, frontend built and 8/8 tests passed, routers were wired, and migrations applied/rolled back with zero model/schema drift. For the latest enhancements and validation scope, use **Latest Changes and Current Handoff** above.
- One real production bug was found by the test suite and fixed during this phase (see section 11) — a good example of the test-first approach catching a genuine runtime defect before it reached a live system.
- **Live-stack completion remains open**: user-run Oracle/PostgreSQL verification reached the synthetic pipeline but uncovered errors that have since been fixed. A complete successful rerun after these fixes, and real multi-database validation, have not been confirmed.

## What's Next (Pickup Point)

1. **Next unstarted phase: `Phase_4_Compliance_Forecasting.md`** — open that file first to scope the next implementation unit; it is the next sequential phase per `Oracle_Sentinel_AI_Implementation_Plan.md` and has not been started.
2. **Optional first step before Phase 4**: run `test_real_phase3/Verify_Phase3_Real.ps1` against a real stack if one becomes available, to close the one open Phase 3 verification gap (and if you want real email delivery proof, configure SMTP in `test_real_phase3/.env` per the comments there).
3. **Before writing any new code**, re-run the "Exact Verification Commands" above once to confirm the environment is still green, since the Phase 3 changes are uncommitted (git status note above) and could in principle be lost or altered outside of this workflow.

## Known Limitations / Notes

- Multi-database portal: Add database discovers aliases from the backend TNS file, tests connectivity and persists the selected target in PostgreSQL. All registrations appear in a left sidebar with truthful health colors; the right pane shows selected details or a fleet-wide summary on Home. Existing Incident Center, events and notification bell remain available. Scheduler reconciliation reloads all enabled targets after restart; scheduling must be enabled in backend settings. Credentials stay on the backend, TNS descriptors stay in the server file, and no aliases are auto-imported. See [registration/API details](../docs/api-contracts.md#registering-from-the-portal).
- Multi-database validation: 60 targeted backend tests and all 23 frontend tests passed; the TypeScript/Vite build passed. Tests verify four persisted targets reloading into fresh scheduler/session instances, all-page sidebar loading, registration failures/duplicates, and full-fleet summaries. An isolated synthetic backend also passed registration through the portal proxy, and browser layout checks confirmed desktop side-by-side and mobile stacked panes without horizontal overflow. Real Oracle/PostgreSQL multi-database validation was not run.
- Fixed live-verification reruns selecting an old resolved incident during the production rule cooldown: each invocation now evaluates a uniquely named, temporary YAML rule copy and verifies its exact returned incident ID. Production cooldowns, overrides and historical records remain unchanged; test records remain for audit. Lease retries share one UUID/event dedup key. Regression tests cover an old resolved incident without a portal notification, a still-active production cooldown, repeated injection, a new immediate run, and explicit failure when no incident is produced (12 focused harness/collector tests passed).
- Portal-first notifications now replace the previous unconfigured-SMTP default: every newly opened incident persists one `web` notification, and the header bell exposes a shared, paginated unread/read inbox with incident links. SMTP is opt-in via `SMTP_ENABLED=true`; root `.env`, `.env.example`, and the Phase 3 live overrides default to `false`. Disabled SMTP creates no email attempts. Existing incidents are not backfilled. See [API/configuration details](../docs/api-contracts.md#portal-alerts-and-optional-email).
- Portal-first validation: 27 targeted backend tests and 15 frontend tests passed; the TypeScript/Vite production build and PowerShell harness syntax check passed. A browser check with mocked API responses verified the panel and mark-read interaction at desktop/mobile sizes. Live Oracle/PostgreSQL/SMTP verification was not rerun.
- Fixed the live harness's `DetachedInstanceError` during `simulate-alert`: it no longer constructs an unused Oracle adapter after closing the target's database session. The scheduled alert runner also loads target credentials before detaching, matching the existing metric collector. Regression tests exercise synthetic injection in live mode and scheduled adapter creation both with and without target-specific credentials, without contacting Oracle or SMTP.
- Historical test counts and SMTP-failure expectations above describe the original implementation baseline; the live harness now checks a persisted portal alert, with email delivery checked separately only when SMTP attempts exist.
- User-provided live execution logs now supplement the original code-review-only harness evidence. They prove Oracle connectivity and pipeline execution, not successful completion after the latest fixes; do not treat them as a completed live acceptance run.
