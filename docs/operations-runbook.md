# Operations Runbook

## Start a development deployment

Use the repository root and the project's Python 3.14 virtual environment:

```powershell
docker compose up -d postgres redis
.\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
.\.venv\Scripts\python.exe -m uvicorn sentinel.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
Set-Location frontend
npm ci
npm run dev
```

Settings load the root `.env` regardless of working directory. Configure a reachable
`DATABASE_URL`, an absolute `TNS_ADMIN`, a read-only Oracle account, and
`SCHEDULER_ENABLED=true`. Do not expose this pre-authentication development API
publicly. AI, Redis and an LLM are not required for Phase 2 polling.

### Existing Phase 1 databases

Back up the application database first. New databases use `upgrade head` directly.
If Phase 1 created the five baseline tables with `create_all`, but there is no
Alembic version, compare that schema with migration `0001_registry.py`. Only when
it matches, adopt that baseline without recreating or dropping data:

```powershell
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini stamp 0001
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```

Do not stamp an unknown or partially migrated schema. Startup no longer uses
`create_all` as a substitute for migrations.

## Phase 2 automated validation

From the repository root, run `.\Verify_Phase2.ps1` for the automated synthetic
workflow, or `.\Verify_Phase2.ps1 -Interactive` to open the dashboard and pause for
manual checks. Use `-InstallDependencies` to explicitly install backend/frontend
dependencies first. The script requires the project `.venv` and Node/npm, fails if
port 8000 or the frontend port (5173 by default) is occupied, ignores `.env`, uses
a unique temporary SQLite database, and restores environment overrides on exit.
It checks restart persistence, staleness, scheduler recovery, API bounds, and the
frontend proxy. It stops its owned services on exit; failed runs retain diagnostic
logs and the isolated database. `-SkipTests` is a quicker smoke check, not full
verification. Live PostgreSQL/Oracle acceptance remains separate.

```powershell
.\.venv\Scripts\python.exe -m compileall -q backend\src backend\alembic
.\.venv\Scripts\python.exe -m pytest -c backend\pyproject.toml backend\tests
Set-Location frontend
npm test
npm run build
```

Backend tests migrate temporary SQLite databases, exercise the synthetic fleet,
API bounds, exact 85%/95% threshold boundaries, stale telemetry, scheduler
deduplication/execution, fenced leases, crash recovery, retention and migrations.
SQLite verifies application behavior, not PostgreSQL locking semantics. Before
production acceptance, repeat migration/restart/competing-worker validation against
a dedicated PostgreSQL database and test at least two real Oracle targets with the
documented grants. No AWR/ASH/ADDM queries are used.

## Credential-free synthetic dashboard

For **real** Oracle data with Docker PostgreSQL, use the separate live launcher:

```powershell
.\test_real_phase2\Verify_Phase2_Real.ps1 -TnsAlias YOUR_TNS_ALIAS -Interactive
```

Add `-InstallDependencies` when preparing dependencies for the first time, or use
`-CheckPrerequisites` for tool/file checks without connecting to Oracle. Without
`-TnsAlias`, the script lists aliases and prompts for one.
It loads root `.env` first for credentials, then `test_real_phase2\.env` for live
overrides. Its Python child ignores inherited model-field environment overrides
so earlier synthetic settings cannot silently redirect it. Relative `TNS_ADMIN`
is resolved against the project root. Both `.env` files remain git-ignored.

The override file targets the existing local Docker PostgreSQL `sentinel` database.
Review it before running. The launcher starts PostgreSQL, migrates the application
schema, registers/reuses the selected target, checks connectivity/probe results,
and opens the portal with `-Interactive`. It does not auto-stamp unversioned schemas,
delete data, stop PostgreSQL, or claim success when probes fail. Existing enabled
targets in that application database are also polled. Logs are retained locally
under the displayed temporary directory. Only the owned backend/frontend processes
are stopped on exit. This is separate from the synthetic verifier described below.

Use a **dedicated disposable** application database, never a production one.
In a new PowerShell terminal set overrides (these do not edit `.env`):

```powershell
$env:DATABASE_URL = "sqlite:///phase2-demo.sqlite"
$env:SYNTHETIC_MODE = "true"
$env:SCHEDULER_ENABLED = "true"
.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
.\.venv\Scripts\python.exe -m sentinel.collectors.demo --database-url $env:DATABASE_URL
.\.venv\Scripts\python.exe -m uvicorn sentinel.main:app --host 127.0.0.1 --port 8000
```

The demo command is repeatable and creates four named synthetic targets:
healthy, critical (97% tablespace usage), offline and missing-permissions (unknown).
`SYNTHETIC_MODE=true` never connects to Oracle. Do not mix synthetic and live targets
in the same application database. The demo command ignores `.env`; normal application
startup uses local settings plus the process overrides above.

With the frontend running at `http://localhost:5173`, verify:

1. Four targets have distinct, labelled health states. Environment/status filters
   and pagination work; incident counts correctly say unavailable.
2. Open the critical target: version, role/PDB, tablespace size/autoextend,
   FRA breakdown, archive mode and historical chart are populated.
3. Change chart range and metric. Empty windows display no history, not zero values.
4. Refresh rapidly: no new collection runs are caused by HTTP reads.
5. Stop/restart the backend. Checkpoints retain their versions; jobs resume from
   their last successful run rather than creating duplicate jobs.
6. Stop collection and wait past probe staleness thresholds; previously healthy
   telemetry becomes `unknown`. A known offline state also expires to unknown.
7. In a disposable PostgreSQL instance, kill a worker during a poll; after lease
   expiry another worker must recover it and mark the old run `interrupted`.

## Polling and recovery

Default standard intervals in seconds:

| Probe | Interval | Stale after |
|---|---:|---:|
| connectivity | 60 | 180 |
| instance | 300 | 900 |
| storage | 300 | 900 |
| fra_archive | 180 | 540 |
| resources | 180 | 540 |
| configuration | 1800 | 5400 |

`critical` halves intervals; `lightweight` doubles them. Production targets without
an explicit profile name use `critical`. Global `POLL_INTERVALS` and
`STALE_AFTER_SECONDS` are JSON maps containing all six probes. Per-target
`poll_profile.intervals` and `poll_profile.stale_after_seconds` allow partial overrides.
Otherwise the stale threshold is at least three polling intervals. The legacy
`SCHEDULER_DEFAULT_INTERVAL_SECONDS` setting is superseded by `POLL_INTERVALS`.

`SCHEDULER_WORKERS` bounds worker threads. `SCHEDULER_RECONCILE_SECONDS` controls
registration/update/disable reconciliation (30 seconds by default).
`COLLECTOR_LEASE_SECONDS` defaults to 1800; keep it longer than the worst-case
probe/connect/statement duration. Oracle TCP connect timeout and per-call deadline
are configured separately. Leases are atomic and version-fenced: expired workers
cannot commit old observations. Shutdown waits for active jobs; restart recovers
expired leases and records interrupted runs. Target configuration changes invalidate
current data and fence results from the old configuration.

An hourly maintenance job deletes at most 10,000 expired observations, completed
poll runs and poll-failure events per table per run. Default retention is 30 days.
The bounded latest-value projection remains available with timestamps/stale flags;
it does not extend historical retention. Tune maintenance throughput for large fleets.

## Diagnostics

- `/health/live` checks process liveness; `/health/ready` returns 503 when DB access fails.
- Inspect `poll_runs` for success/failure/interruption, error class, duration and lease version.
- Inspect `collector_checkpoints` for last attempt, success, cursor and lease expiry.
- Poll failures generate `diagnostic_events` with source `poll_failure`, code
  `POLL_FAILED` and a poll-run evidence ID, distinct from Oracle diagnostic events.
  Rule evaluation, incident correlation and notifications belong to later phases.
- API detail `poll_status` exposes permission/probe failures without driver secrets.
- The compliance card stays unknown until Phase 4 policy evaluation is implemented.

## Phase 7 proposals, audit, and sign-in

Apply migration `0008` before starting an API that includes proposals. Proposals are approval records only. Nothing in that workflow connects to Oracle or runs the preview SQL.

Back up the application PostgreSQL database before an upgrade:

```powershell
pg_dump -Fc -f sentinel-backup.dump "<application database url>"
```

Restore into an empty database with `pg_restore`, then run `alembic upgrade head`. A restore drill was not run as part of this implementation. Do not put the database URL or a password in a ticket or log.

OIDC is off until `AUTH_OIDC_ISSUER` and `AUTH_OIDC_CLIENT_ID` are set. The client secret stays in `OIDC_CLIENT_SECRET` and is referenced by `secret://OIDC_CLIENT_SECRET`. If the identity provider cannot be reached, use the local account. TLS belongs on the reverse proxy; the development API listens on `127.0.0.1`.

Audit rows are append-only. The application refuses to purge them. Credential rotation stores a `secret://` reference and a timestamp, not the password. Prometheus metrics are at `/metrics`.

## Phase 20 production settings and operators

Production start-up. `APP_ENV=production` starts only with `AUTH_REQUIRED=true`, a `SECRET_KEY` of at least 32 characters that is not the default, and `AUTH_COOKIE_SECURE=true` behind TLS. `/metrics` then needs sign-in unless `METRICS_PUBLIC=true`; point the scraper at a signed-in session or expose metrics on a network only the scraper can reach.

Operators. An administrator adds local operators in Admin, Operators. A password needs at least 10 characters with a letter and a digit. Disabling an account, changing its role, or resetting its password signs its open sessions out on their next request. A local operator changes their own password from the key icon in the top bar. Directory accounts are managed in the directory.

Failed sign-ins. Five failures inside fifteen minutes for one username and client address lock that pair for fifteen minutes (`AUTH_MAX_FAILED_LOGINS`, `AUTH_LOCKOUT_MINUTES`). The counters live in the API process, so a restart clears them and several API processes each count on their own.

Audit. Sign-ins, operator changes, registry changes, connection tests, incident actions, copilot questions, and knowledge changes are in Audit. The audit table rejects updates and deletes.

Knowledge. An administrator uploads a PDF, DOCX, TXT, or Markdown file on the Knowledge page. The file lands in `knowledge-intake/ora-19c-manual` or `knowledge-intake/Policy` and is scanned at once. A manual above `KNOWLEDGE_MAX_PAGES` is quarantined by that scan.

Model-assisted routing. With a model enabled, a database question the fixed router cannot place is routed by the model to at most three approved readings. Set `AI_ROUTER_FALLBACK=false` in Admin, Language model to keep routing fixed only.

## Phase 27 tablespace timeline

- No migration, grant, or setting. Restart the API and rebuild or reload the portal.
- The Overview timeline shows one tablespace at a time, the fullest first. Pick another tablespace, or "All tablespaces" for one line each, in the picker beside the metric.
- The "Tablespace usage" card names the fullest tablespace.
- "Of maximum" in the Tablespaces table is used space against the most the datafiles may autoextend to. A tablespace can be 94 percent of its allocated space and 3 percent of its maximum; the first is what health and the rules still use.
- The new values appear after the next storage collection. Rows stored before it have no "Of maximum".
- `GET /api/v1/databases/{id}/metrics?metric_name=storage.tablespace.used_pct&label=tablespace_name=USERS` returns one tablespace's history.

## Phase 26 copilot fixes

- No migration, grant, or setting. Restart the API; the portal needs no rebuild.
- "Which schema is not a system schema?" and "list the schemas" are read from the selected database for an administrator. The reading lists application schemas first, then the ones Oracle maintains. Anyone else is told that the list of database users is limited to an administrator.
- The monitoring account appears among the application schemas, marked as the Sentinel monitoring account. That is correct: Oracle does not maintain it.
- An Oracle-knowledge answer marked low confidence with "No manual passage was found" means the manuals are not indexed or the search found nothing. Index them with `test_real_phase24\Verify_Phase24_Real.ps1 -ReindexOnly`.
- When an answer looks wrong, read the thread in `chat_messages`, oldest first. The `tool_trace` of the answer names the route and the readings, and the messages before it are what the model was sent.
- An answer that repeats an earlier one is asked for again automatically. That turn takes one or two more model calls.
- The user reading needs Oracle Database 12.1 or later. On 11g `DBA_USERS` has no `ORACLE_MAINTAINED` column and the reading reports that it could not be read.
- Live check: `test_real_phase26\Verify_Phase26_Real.ps1`. Add `-RequireModel` to fail unless the local model routed and answered.

## Phase 25 database sections

- No migration and no grant. Restart the API and reload the portal. A database workspace then has MemoryDB, StorageDB, ParameterDB, and AlertDB tabs.
- Each section opens one Oracle session when it is opened and on each refresh. AlertDB reads `V$DIAG_ALERT_EXT`, which is the slowest of the four on a large alert log.
- A section that shows "Grant SELECT on ..." names the view the monitoring account is missing. All views used here are in `scripts/oracle/02_grant_readonly_monitoring.sql`.
- An operator who needs a parameter value that is held back, or a file path in the alert log, must ask an administrator. The rule is fixed.
- Nothing can be changed from these pages. ParameterDB has no modify control.
- Live check: `test_real_phase25\Verify_Phase25_Real.ps1`. It changes no setting.

## Phase 24 embeddings and supervisor

- Run `alembic upgrade head` first. Revision `0012` clears the stored passage vectors and marks every document for embedding again. Keyword search keeps working until the indexing step has run.
- Index once with `test_real_phase24\Verify_Phase24_Real.ps1 -ReindexOnly`. It pulls `nomic-embed-text` into Ollama when it is missing, stores the local model as the embedding provider, raises the stored page, size, and parse limits when they are too low, and embeds every file. Twelve manuals take about fifteen minutes on a GPU.
- Run it again after adding, replacing, or removing a manual. Unchanged files are skipped, so it takes seconds. A stopped run continues with the unfinished files.
- Ollama must be running for a search by meaning. When it is not, search uses keywords only and the copilot still answers.
- An embedding model must return 768 numbers. Another length is refused with a message.
- The supervisor needs the local language model stored in Admin, Language model. `ai_supervisor_enabled` turns it off; the fixed patterns then route as before.
- Answers from snapshots need agents on. `ai_snapshot_max_age_seconds` is 900 by default; 0 always reads live.
- Each answer in the portal shows who answered. Hover the badges to see whether the supervisor or the fixed patterns chose.

## Phase 23 graph history

- No migration. Restart the API. The sampler starts with the collection scheduler, so it needs `SCHEDULER_ENABLED=true`.
- It is on by default and logs on to each enabled database once every 5 minutes to read five fixed readings. Change the interval (60 to 3600 seconds), the retention (1 to 168 hours), or turn it off in Admin, Graph history. A changed interval applies within 30 seconds.
- Samples are rows in `observations` with `metric_name = 'activity.sample'`. At the defaults one database holds about 288 rows.
- GraphDB and WaitDB read again 3 seconds after they open. That adds one Oracle logon per page open.
- A page that shows "Rate needs a second sample" for more than a few seconds means the quick reading failed; check the database connection.
- Live check: `test_real_phase23\Verify_Phase23_Real.ps1`. It sets a 60 second interval for the run and puts every setting back.

## Phase 22 agents

- Run `alembic upgrade head` first. The API does not start without the agent tables of revision `0011`.
- Agents are off after the upgrade. An administrator turns them on in Admin, Agents (`agents_enabled`). A database can be left out, or given its own packs, retention, minimum gap, and automatic investigation, in the Snapshot panel of its Overview page.
- A snapshot logs on to the database once and runs up to 15 fixed readings for the default packs. Keep the minimum gap above zero in normal use.
- Automatic investigation of a new critical incident (`agent_auto_investigate`) and snapshot retention run on the agent timer, which starts only when `SCHEDULER_ENABLED=true`.
- Snapshots are deleted after `agent_snapshot_retention_days` (14 by default, 30 at most). Runs and findings are kept.
- The local model writes the cause in a finding. Without it, findings show the evidence only.
- Live check: `test_real_phase22\Verify_Phase22_Real.ps1`. It turns agents on for the run when they are off and puts every changed setting back.
