# API Contracts

## Database Registry and Fleet

`GET /api/v1/databases?page=1&page_size=25&environment=production&health_status=critical`

Returns `{items, total, page, page_size, environments}`. This intentionally replaces
the Phase 1 bare list with a paginated envelope; update consumers to use `items`.
`page_size` is 1-100. Filters are optional; `enabled_only=true` is retained.
Health states: `healthy`, `warning`, `critical`, `unknown`, `offline`.
Responses include UTC timestamps, `last_seen`, `monitoring_status`, capability flags,
and current capacity `trend_snapshots` with evidence IDs. Incident counts are `null`
until the Phase 3 incident service exists, never a fabricated zero.

The portal displays every registered target in a persistent left sidebar (following
all list pages). Selecting a target opens its details on the right; Fleet overview
clears the selection and shows health across the entire registry, independent of
table pagination/filters. Colors include green healthy, yellow warning, red
critical/down, and gray unknown/stale/disabled. On small screens the database
navigation stacks above the detail pane. The portal refreshes every 30 seconds by
default; an explicitly saved refresh interval (including Off) is respected.

`GET /api/v1/databases/summary` returns `{total, healthy, warning, critical, offline,
unknown, disabled, scheduler_enabled}` for the complete registry. Disabled targets
are counted separately; expired/unavailable health is unknown, never healthy.

### Registering from the Portal

- `GET /api/v1/databases/tns-aliases`: returns `{items:[{name,registered}],
  scheduler_enabled}` from the backend's `TNS_ADMIN/tnsnames.ora`. No descriptors,
  hostnames, usernames or passwords are included. Missing/unreadable TNS
  configuration returns 503. Relative `TNS_ADMIN` paths resolve from the project
  root for both discovery and Oracle connections, regardless of the launch directory.
- `POST /api/v1/databases/test-tns-connection`: accepts `{tns_alias, display_name,
  environment, owner}` and tests a configured alias with backend credentials.
  Returns a connection result; creates no registry entry.
- `POST /api/v1/databases/register-from-tns`: accepts the same payload. Trims fields,
  validates the alias against the current server file, tests connectivity again,
  then commits an enabled target to PostgreSQL (201). A failed connection/invalid
  alias returns 422 without saving a target. An already registered alias (including
  case variants and disabled registrations) returns 409. This is separate from the
  existing general-purpose registration API, which remains backward compatible.

Use **Add database** in the sidebar, select an unregistered alias, optionally test
the connection, and choose **Register database**. The form uses the backend's
`ORACLE_USERNAME` and `ORACLE_PASSWORD` or `ORACLE_PASSWORD_SECRET_REF` settings;
it never asks the browser to retain credentials. It does not import every TNS entry:
listener/external-procedure aliases may not be databases, and multiple aliases can
refer to the same database. Only explicitly selected, successfully tested aliases
are registered. Display name, alias, environment and owner are stored in PostgreSQL;
the TNS file remains the source of connection descriptors and must remain available.

Registrations reload from PostgreSQL after application restarts. With
`SCHEDULER_ENABLED=true`, the collector schedules all enabled targets, including
new registrations after the next reconciliation (30 seconds by default).
`SCHEDULER_WORKERS` bounds simultaneous probe jobs; it is not the maximum number of
registered databases. A successful connection test alone leaves health unknown
until the required probes collect fresh evidence. When scheduling is disabled,
the portal explicitly warns that no automatic collection will occur.

This is still a local pre-authentication development portal. Do not expose registration,
connection-test or other API endpoints publicly without the planned access controls.

- `POST /api/v1/databases`: create an Oracle target (201).
- `PUT /api/v1/databases/{target_id}`: update configuration. Invalidates old current
  readings until new measurements arrive; history is retained.
- `POST /api/v1/databases/check-fleet`: a DBA or administrator recollects every enabled
  database with the same probes as the scheduler, then returns `{checked, items}` where
  each item is `{id, display_name, monitoring_status}`. A disabled database is listed
  and is not probed. A single failed connection does not disable a database; the
  seven-day rule still applies. The response has no SQL, paths, or credentials.
- `DELETE /api/v1/databases/{target_id}`: a DBA or administrator removes a database from
  the registry (204). Oracle is not changed.
- `POST /api/v1/databases/{target_id}/test-connection`: optional TNS/credential
  overrides; a successful manual check does not mark uncollected telemetry healthy.

`poll_profile` supports:

```json
{"name":"standard","intervals":{"storage":300},"stale_after_seconds":{"storage":900}}
```

Profile names: `critical`, `standard`, `lightweight`. Overrides are positive integer
seconds (1-86400). A missing profile name defaults to `critical` for production,
otherwise `standard`. Secret references currently support only `env:VARIABLE`;
unknown or unpopulated references fail explicitly. Password values are not stored
in the application database or returned by the API.

## Database Detail

`GET /api/v1/databases/{target_id}` returns the target plus:

- `latest_observations`: current values with `id`, `target_id`, `metric_name`,
  `timestamp`, `value`/`value_str`, `unit`, `labels`, `quality`, `source`.
- `poll_status`: one entry per probe including attempt/success times and error class.
- `tablespaces`: name, used percentage, size, autoextend, measurement timestamp.
- `fra`: size/used bytes, used percentage, and usage-by-type observations.
- `archive`: archive mode and destination status/error observations.
- `compliance`: current Phase 4 totals for pass, fail, unknown, not-applicable, and
  evaluator-error policy results. Missing/stale evidence remains unknown.

No observations means `unknown`, not healthy. A recent failed connectivity poll
means `offline`; failures of other required probes or stale data mean `unknown`.
Historical/stale values remain inspectable and are flagged `stale` in detail.

## Compliance and Capacity Forecasting

- `GET /api/v1/compliance/policies` returns all persisted immutable policy versions,
  including approval state, applicability, evaluation configuration, and
  environment overrides. The reviewed catalog is [policies/compliance.yaml](../policies/compliance.yaml).
  Policy changes require a new version; editing an already-persisted version fails
  rather than silently changing the meaning of historical results. Catalog changes
  should be reviewed and approved through the normal source-control review process.
- `GET /api/v1/databases/{target_id}/compliance` returns the current policy result,
  version, actual metric evidence with source observation IDs and timestamps, severity, and
  recommended action. Policies are evaluated when relevant Oracle probes succeed.
  Oracle OMF, ARCHIVELOG, FRA, and Flashback checks use collected observations;
  backup freshness is not asserted without an authorized evidence source. A policy
  can be overridden per environment in the catalog, including required/not-required
  applicability, severity, and supported evaluation parameters.
- `GET /api/v1/databases/{target_id}/forecast` returns tablespace and FRA trend
  forecasts calculated from retained `bytes_used`/`space_used` observations and
  current capacity. [policies/forecasting.yaml](../policies/forecasting.yaml)
  configures the minimum sample count/span, irregular-gap limit, prediction horizon,
  and early-warning threshold. Sparse, stale-by-absence, or irregular data returns
  `status=unknown` with no predicted exhaustion date. Positive-growth forecasts
  include a 95% regression-slope interval and set `early_warning=true` when the
  predicted exhaustion falls within the configured threshold. Forecast warnings
  are surfaced in the portal; they do not execute database changes.

## Metrics and Observations

`GET /api/v1/databases/{target_id}/metrics?metric_name=storage.tablespace.used_pct&start=2026-11-09T00:00:00Z&end=2026-11-09T12:00:00Z&limit=1000`

Returns chronological observation rows, keeping the newest `limit` rows within the
requested window. `limit` is 1-5000; default window is the last 24 hours. Maximum
window is `OBSERVATION_RETENTION_DAYS` (30 by default). Invalid/reversed/oversized
ranges return 422. Empty history returns `[]`, never fabricated measurements.
Each metric/label combination is a separate series.

`GET /api/v1/databases/{target_id}/observations` supports the same time and metric
filters plus `quality=measured|estimated|stale`, `offset` (0-100000), and `limit`
(1-1000). Quality filters match stored quality; the detail endpoint additionally
computes current staleness from probe timestamps.

Dashboard reads never run Oracle probes. Fleet/detail query the indexed latest-value
projection; history reads are time- and row-bounded. Evidence IDs identify original
observation rows while within retention. Invalid UUIDs return 400; absent targets 404.

## Health

- `GET /health/live`: `{"status":"alive"}`.
- `GET /health/ready`: `{"status":"ready","database":"connected"}`, or 503 if the
  application database cannot be reached. Startup fails when migrations are absent.

## Portal Alerts and Optional Email

Each newly opened incident creates one persistent `web` notification in the same
transaction, regardless of SMTP configuration or email recipients. Recurrences do
not create duplicate notifications. Existing incidents are not backfilled.

- `GET /api/v1/portal-notifications?page=1&page_size=10`: returns
  `{items, total, unread_count, page, page_size}`. `page_size` is 1-100;
  `unread_count` covers all pages. Items contain `id`, `incident_id`, `target_id`,
  `target_name`, `severity`, `message`, `status` (`unread` or `read`), and
  `created_at` (UTC), ordered newest first.
- `POST /api/v1/portal-notifications/{id}/read`: idempotently marks a portal alert
  read and returns it. Invalid UUIDs return 400; absent/non-web notifications 404.
  Reading does not acknowledge or resolve the incident.
- `GET /api/v1/notifications?incident_id={id}&channel=smtp`: existing delivery-log
  endpoint; omit `channel` to include portal records as well as SMTP attempts.

`GET /api/v1/incidents` returns `{items, total, page, page_size}`. Each item includes
`target_id` and `target_name`, the registered database display name. Optional filters
are `state`, `severity`, `owner`, and `target_id`. An invalid `target_id` returns 400.
The Incident Center shows that database name, a Refresh control that reloads the latest
stored incidents, and a database filter. When `target_name` is empty, the page uses the
registered database name for the same `target_id`. Refresh does not query Oracle.
`DELETE /api/v1/incidents` is administrator-only and returns `{deleted}`. It removes
incident records and their portal notifications. Oracle is not changed. An operator
or DBA receives 403.

The header bell shows unread counts, a paginated panel, mark-read actions and links
to the Incident Center. It follows the selected auto-refresh interval; opening the
panel, its refresh button, and the header refresh button also reload notifications.
Read state is stored in PostgreSQL and shared by all operators in this local,
pre-authentication portal, not tracked per user. Do not expose it publicly.

SMTP is opt-in. The root `.env` and `test_real_phase3/.env` explicitly disable it:

```dotenv
SMTP_ENABLED=false
SMTP_HOST=
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_FROM_ADDRESS=
SMTP_USERNAME=
SMTP_PASSWORD_SECRET_REF=
NOTIFICATION_RECIPIENTS=[]
NOTIFICATION_MAX_RETRIES=3
NOTIFICATION_RETRY_BACKOFF_SECONDS=5
```

To enable email later, set `SMTP_ENABLED=true`, fill in the relay and from-address,
and set `NOTIFICATION_RECIPIENTS` to a JSON array of addresses. If authentication is
required, set `SMTP_PASSWORD_SECRET_REF=env:SENTINEL_SMTP_PASSWORD` and populate that
process environment variable separately. Restart the backend after changes.
The live harness's `.env` overrides the root file, so enable SMTP there when using
that harness. Disabled SMTP creates no email attempts or SMTP connections. When
enabled but missing a host/from-address, delivery is recorded as
`SMTPNotConfigured`; SMTP failures do not remove the portal alert.

### Repeatable Phase 3 Live Verification

The Phase 3 harness copies the selected YAML ORA-code rule to a temporary directory
under a unique `verify_phase3_<run UUID>_<original rule id>` ID. This isolates each
synthetic run from existing production cooldowns and incidents, without deleting or
resetting either. The YAML rule must be enabled and use `hysteresis=1` for the
single-event check. Production database rule overrides are deliberately not copied
or modified; this verifies the pipeline, not the current production override policy.

`simulate-alert` returns `run_id`, `rule_id`, and the exact `incident_id` alongside
`target_id` and `run_outcome`. The PowerShell harness validates that incident's target,
rule and initial `new` state before exercising acknowledge/resolve. It no longer
assumes a target has only one historical incident for the production rule.
Lease retries reuse `--run-id`, giving the event a stable dedup key. A new invocation
uses a fresh UUID and can run immediately after a prior resolved verification.
Portal-feed verification follows pagination rather than checking only the first page.
Temporary YAML files are removed automatically; test database records remain for audit.

## Knowledge search

Document search does not call a language model. Monitoring, rules, and incidents keep running when embeddings or the optional knowledge scan are unavailable.

- `GET /api/v1/knowledge/documents?page=1&page_size=25&category=Policy&status=indexed&q=archive`
  returns `{items, total, page, page_size}` for documents the caller may see. The caller role
  defaults to `operator` from `X-Sentinel-Role`. An empty ACL denies access. The `admin` role
  can see every document so an ACL can be repaired. Items include name, category, version,
  ingestion status, last indexed time, embedding provider, and the local-or-remote disclosure.
- `POST /api/v1/knowledge/search` accepts `{query, limit, category, rerank}` and returns
  `{query, retrieval: "hybrid", llm_used: false, prioritization, items}`. Each item has an
  excerpt and a citation with document, version, page, and section. Passages are filtered by
  ACL before they are returned. Policy documents are labeled `authoritative_policy` and are
  not given a hidden rank boost; set `category` to search one collection.
- `POST /api/v1/knowledge/reindex` is administrator-only (`X-Sentinel-Role: admin`). An optional
  `{paths: ["ora-19c-manual/file.pdf"]}` limits the scan to those files and does not treat absent
  files as deletions. A full scan with no paths reflects additions, changes, and deletions.
  Paths that leave `ora-19c-manual/` or `Policy/` are rejected. A scan already in progress returns 409.
- `POST /api/v1/knowledge/documents/{id}/acl` with `{roles, principals}` is administrator-only
  and copies the new grant onto every chunk of that document.

Scheduled scans run only when `KNOWLEDGE_ENABLED=true`. Embeddings stay local unless
`EMBEDDING_PROVIDER=remote`, `EMBEDDING_REMOTE_APPROVED=true`, a URL, and a privacy disclosure
are all set. There is no automatic failover from local to remote. The vector width is fixed at
128 until a migration changes the pgvector column.

## Copilot

The assistant policy is [docs/assistant-policy.md](assistant-policy.md). The model may explain an Oracle concept and draft SQL. It is never given a tool to call and never runs SQL, PL/SQL, a shell command, or a database change. When the fixed router has no reading for a question on a database chat, the model may nominate up to three allowlisted reading names, which the server validates against the allowlist and the caller's role; the answer lists this as `route_question`. A database measurement must come from an approved reading. A company requirement must come from an approved policy document or a compliance result. If neither is available, the answer says the company requirement could not be established. An empty manual search does not block a general explanation. A question may contain more than one of these tasks, and a supported part is still answered when another part has no approved reading. In `APP_ENV=dev` or `test`, the local admin principal `sentinel` can choose the local model, hosted Claude, or hosted OpenAI for one answer. The privacy sentence follows that choice. Each choice also uses its own base URL, key reference, model, timeout, and output limit: `AI_LOCAL_*`, `AI_ANTHROPIC_*`, or `AI_OPENAI_*`. An empty Anthropic field keeps the configured Claude value. An empty local key reference sends no API key. The key values stay in `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `LOCAL_AI_API_KEY` and are not returned. Every other operator keeps the configured provider, and `APP_ENV=production` hides the choice. A missing OpenAI key is reported as not configured and is never returned by the API. An operator prompt that contains a SQL statement is refused before any tool or model runs. On a database chat, availability, error, alert log, tablespace, growth, performance, blocking, SQL, memory, backup, Data Guard, account, and job questions call the local readings MCP server, which runs one fixed read-only reading for the database alias. The latest alert log lines come from this database's alert in `V$DIAG_ALERT_EXT` for the last 7 days. Listener and client traces are excluded, so a failed connection to another database does not appear. A question such as “Is there a problem with this database?” lists only `ORA-` errors from that alert log for the last 24 hours, or says there are no Oracle errors reported in that period. A critical-error question still uses the shorter watched-error list. Listing database users is a separate reading. An admin operator receives the usernames and account status. An operator, dba, or auditor receives an authorization error, and the database is not queried. The answer lists those rows. The 50 PDB and non-CDB operator questions are a separate catalog. Asking one of those prompts runs its approved tool and answers with a status, the matching rows, a limitation, and a next step. The first answer line is `Label. Status X. Health Y.` without the question id; the id stays in the stored envelope. `guided_commands` lists each prompt once: the Common entries are the questions with no catalog equivalent, and the catalog groups carry the rest. The language model is not asked to rewrite that answer. A question that needs history this deployment does not keep returns INSUFFICIENT_DATA or UNSUPPORTED instead of a zero or a forecast. Fleet chat does not run those single-database questions and says to open a database. A disabled or unreachable language model still leaves `/health/live`, fleet collection, and this chat available. The no-model answer includes guided commands and citations from the tools that ran.

When `AUTH_REQUIRED=true`, every API route except health and `/api/v1/auth/login`, `/api/v1/auth/session`, and `/api/v1/auth/logout` requires the `sentinel_session` cookie. The role comes from that session. `X-Sentinel-Role` is ignored while sign-in is required. Directory sign-in uses an LDAP bind and an allowed group. A local account is accepted only when the sign-in request sets `local` to true. With `AUTH_REQUIRED=false`, the portal stays in the pre-authentication mode used by the test harness.

- `POST /api/v1/chat/threads` accepts `{scope_type, scope_id}`. `scope_type` is `fleet`, `database`, or `incident`. Fleet rejects a scope id. A database or incident id must exist, and a disabled database returns 404. The response includes `id`, `provider`, `model`, `llm_enabled`, `disclosure`, and `guided_commands`. It does not include an API key or a provider URL.
- `POST /api/v1/chat/threads/{id}/messages` accepts `{content}` of 1 to 2000 characters and returns `text/event-stream` after the answer is stored. Events are `status`, `tool`, `citation`, `message`, and `done`. The `message` event includes `llm_used`, `provider_status`, `citations`, `tools`, and `guided_commands`. `llm_used` is false when the model is disabled, unreachable, or returns an invalid reply.
- `GET /api/v1/chat/threads/{id}/messages` returns the stored user and assistant messages for that thread.
- `GET /api/v1/admin/ai/providers` requires `X-Sentinel-Role: admin`. It reports the selected provider, whether hosted use is approved, the hosted vendor (`openai` or `anthropic`), and whether a hosted secret reference is configured. It does not return the secret or the provider URL. Hosted use requires approval, a model name, a `secret://` reference such as `secret://OPENAI_API_KEY` or `secret://ANTHROPIC_API_KEY`, and a privacy disclosure. An empty base URL uses `https://api.openai.com/v1` or `https://api.anthropic.com`. The key is read from the process environment or the matching line in the project `.env` and is not stored in settings or logs. A local outage does not call the hosted model.

`X-Sentinel-Role` is used only when `AUTH_REQUIRED=false`. With sign-in required, the role comes from the session cookie. `admin` has platform-administrator permissions and `auditor` has security-auditor permissions. The Phase 7 roles are `viewer`, `operator`, `dba`, `platform_admin`, and `security_auditor`.

## GraphDB

GraphDB is a graphical view of one registered database. It is not a separate database. `GET /api/v1/graph` and the picture endpoints need `dashboard.read`, which every signed-in role has. The response field `detail` is `true` for an administrator (`admin` or `platform_admin`) and `false` for an operator, DBA, auditor, or viewer. The shell route does not connect to Oracle. Its response still lists the original four panel names and does not include measurements. The page does not render those names. The page is five columns in the performance-home arrangement: Service and host, Server processes, rate meters and SGA, Background processes, and Disk storage. Wait events are on WaitDB.

Each picture endpoint opens one Oracle session for the request and runs every fixed reading on it. A failed logon is reported as a limitation on every card and still returns HTTP 200. A reading that fails on its own names its view and leaves the other cards.

`GET /api/v1/databases/{id}/graph/activity` returns `service`, `host`, `server_processes`, `counters`, `collected_at`, and `limitations`. Uptime comes from `V$INSTANCE`. The database name comes from `V$DATABASE`. The pluggable database name comes from `V$PDBS`; no row means `Not a pluggable database.` User counts and the dedicated, shared, dispatcher, parallel query, and total ovals come from `V$SESSION`. The job-queue oval comes from `DBA_SCHEDULER_RUNNING_JOBS`. Response and average active stay `Not collected.` CPU is `BUSY_TIME / (BUSY_TIME + IDLE_TIME)` from `V$OSSTAT`, captioned since instance startup. Run queue and free memory appear only when `V$OSSTAT` returns `LOAD` and `FREE_MEMORY_BYTES`. A missing session grant names `SYS.V_$SESSION` on those ovals and leaves PGA on the memory endpoint. There is no Enterprise Manager agent oval. `counters` carries the current `V$SYSSTAT` values for physical reads, redo entries, parse count (total), and execute count. The page computes `/s` from two of its own responses and draws them as the flow meters above the SGA card. The first response says a second sample is required. A counter that moves backwards, a missing statistic, or a gap under one second says `Not collected.` The rate is not stored in PostgreSQL and is not shared between browsers or databases. The response does not include SQL.

`GET /api/v1/databases/{id}/graph/memory` returns `sga`, `pga`, `background_processes`, `hit_ratio`, `shared_pool_used`, `in_memory`, `memory_management`, `data_guard`, `result_cache`, `collected_at`, `limitations`, and `snapshot`. SGA bars come from `V$SGAINFO`. The SGA target caption comes from `sga_target` and `sga_max_size`. The buffer-cache hit ratio is `(consistent gets + db block gets - physical reads) / (consistent gets + db block gets)` from `V$SYSSTAT`, captioned since instance startup. A missing statistic leaves that meter at `Not collected.` Shared-pool used percent is the shared-pool size minus free memory from `V$SGASTAT`. Until that grant works, the size meter stays and the used meter says `V$SGASTAT` is not granted. An in-memory row with no bytes says the in-memory area is not in use. `sga_target` and `memory_target` say whether Automatic Shared Memory Management and Automatic Memory Management are on. Result cache stays `Not collected.` PGA comes from `V$PGASTAT` and `pga_aggregate_target`. Background processes come from `V$SESSION` where `TYPE` is `BACKGROUND`. The page shows the database writer, recovery writer, redo log writer, and archiver as pills named from the session program, and keeps the full list below. Data Guard stats come from `V$DATAGUARD_STATS`; no rows means `Not a Data Guard database.` Sizes are labeled in MB or GB. A missing grant is named on that card and the other cards stay. The response does not include SQL. A missing or disabled database returns 404. `V$BGPROCESS` and `V$PROCESS` are not queried.

## WaitDB

WaitDB is a page for one registered database. `#/wait` asks for that database. `#/wait/databases/{id}` opens it. Every signed-in role can read it. The page does not change Oracle.

`GET /api/v1/databases/{id}/graph/waits` returns `events`, `cpu_micro`, `collected_at`, `limitations`, and `caption`. Events are the 15 non-idle `V$SYSTEM_EVENT` rows with the most `time_waited_micro`. Each row has `event`, `wait_class`, `total_waits`, and `time_waited_micro`. `cpu_micro` is `DB CPU` from `V$SYS_TIME_MODEL`. Totals are since instance startup. The page computes waits/s, wait time/s, and the chart from two of its own responses. The first response says a second sample is required. A counter that moves backwards or a gap under one second says `Not collected.` Samples stay in the page. They are not written to PostgreSQL and are not an ASH, AWR, or ADDM history. No new grant is required. The response does not include SQL.

`GET /api/v1/databases/{id}/wait/activity` returns `locking`, `blocking`, `parsing`, `buffer_busy`, `io`, and `collected_at`. Lock activity is up to 12 enqueue names from `V$ENQUEUE_STATISTICS`, with gets, waits, and cumulative wait time. Blocking locks are up to 20 current user sessions in `V$LOCK` with `request > 0`, joined to `V$SESSION` and, for TM locks, `DBA_OBJECTS`. An empty blocking list says `No data`. Parsing returns parse counts from `V$SYSSTAT`, parse times from `V$SYS_TIME_MODEL`, and library-cache gets from `V$LIBRARYCACHE`. Buffer busy waits returns the four buffer-busy events from `V$SYSTEM_EVENT`, block class from `V$WAITSTAT`, per-session waits from `V$SESSION_EVENT`, and tablespaces of sessions waiting now from `V$SESSION` and `DBA_DATA_FILES`. `io` returns block-activity counters from `V$SYSSTAT` and data-file reads, writes, and times from `V$FILESTAT`. Totals are since instance startup. The page computes per-second columns and charts from two of its own responses. The first response says a second sample is required. Samples stay in the page. They are not an ASH, AWR, or ADDM history. A failed section names its view and leaves the other sections. `V$ENQUEUE_STATISTICS`, `V$LOCK`, `V$WAITSTAT`, and `V$SESSION_EVENT` are in the grant script. The response does not include SQL. A missing or disabled database returns 404.

`GET /api/v1/databases/{id}/graph/storage` returns `data_files`, `temp_files`, `control_files`, `redo_logs`, `segments`, `recovery_area`, `archive`, `backup`, `collected_at`, `limitations`, `detail`, and `detail_note`. Data files and temp files come from `DBA_DATA_FILES`, `DBA_FREE_SPACE`, and `DBA_TEMP_FILES`. Used percent is allocated bytes minus free bytes. Control files come from `V$CONTROLFILE`; `control_files.count` is the number returned. Redo groups and member paths come from `V$LOG` and `V$LOGFILE`. The largest 20 segments come from `DBA_SEGMENTS`. The recovery cylinder comes from `V$RECOVERY_FILE_DEST` and file types from `V$FLASH_RECOVERY_AREA_USAGE`. No destination says `No recovery area was returned.` and the cylinder is not drawn as zero. Archive shows `V$DATABASE` log mode, `V$ARCHIVE_DEST_STATUS`, and the latest `V$ARCHIVED_LOG` when one was returned. Free minutes and time to failure stay `Not collected.` Backup is the latest `V$RMAN_BACKUP_JOB_DETAILS` row. No row says `No RMAN job was returned.` A failed status is marked critical. A missing grant is named on that list and the other lists stay. Flash cache is `Not collected.` A non-administrator receives the summary: the cylinders, counts, used percent, recovery, archive, and backup stay, while `data_files.files`, `temp_files.files`, `control_files.files`, `redo_logs.members`, and `segments.rows` are empty, `detail` is `false`, and `detail_note` says the listings are shown to an administrator. An administrator receives the listings and `detail` is `true`. The response does not include SQL. A file path is text and is not executed. `V$TEMPSEG_USAGE` is not queried. There is no control that starts a backup.

The portal route `#/graph` asks the signed-in operator to choose a registered database. `#/graph/databases/{id}` shows that database's display name and TNS alias, an **Ask copilot** button that opens the database-scoped chat, and a **Database detail** button. An administrator sees the file, control-file, redo-member, and segment listings in closed sections under the Disk storage card. There is no execute, backup, or SQL control.

## Phase 20 security and operators

Registry changes need `database.manage`: `POST /api/v1/databases`, `POST /api/v1/databases/register-from-tns`, `PUT /api/v1/databases/{id}`, `POST /api/v1/databases/test-tns-connection`, and `POST /api/v1/databases/{id}/test-connection` return 403 to a viewer, operator, or auditor. `DELETE /api/v1/databases/{id}` and `POST /api/v1/databases/check-fleet` already did. Fleet rows and the database detail return `critical_incident_count` as the number of open critical incidents.

`POST /api/v1/incidents/{id}/acknowledge` and `/resolve` need `incident.acknowledge`. The actor recorded is the signed-in principal. The `actor` field in the body is used only when sign-in is off.

`GET` and `POST /api/v1/chat/threads/{id}/messages` return 404 unless the caller is the principal who opened the thread.

`GET /api/v1/auth/session` and the sign-in response include `directory_enabled`. `POST /api/v1/auth/login` returns 429 with `Retry-After` after `AUTH_MAX_FAILED_LOGINS` failures inside `AUTH_LOCKOUT_MINUTES` for one username and client address. `POST /api/v1/auth/password` takes `current_password` and `new_password` for a local session and reissues the cookie. A local session is rejected with 401 on the next request after its account is disabled, its role is changed, or its password is changed.

`GET /api/v1/admin/operators` lists local accounts as `username`, `role`, `enabled`, and `created_at`. `POST /api/v1/admin/operators` takes `username`, `password`, and `role` and returns 201, 409 for an existing name, or 422 for a refused password. `POST /api/v1/admin/operators/{username}/role`, `/enabled`, and `/password` change one account; the caller cannot target their own account on those routes. All need `role.manage`. A password is never returned.

`POST /api/v1/knowledge/upload?filename=&category=&replace=` is administrator-only and takes the raw file as the body with any content type. It returns 201 with `relative_path`, `category`, `size_bytes`, `replaced`, the scan counts, `errors`, and `note`; 409 when the name exists and `replace` is not true; 413 above `KNOWLEDGE_MAX_FILE_BYTES`; and 422 for a folder in the name, an extension outside PDF, DOCX, TXT, and Markdown, an empty body, or content that does not match the extension.

Every response carries `X-Content-Type-Options`, `X-Frame-Options`, and `Referrer-Policy`. `/api/` responses add `Cache-Control: no-store` and `Content-Security-Policy`. `/metrics` is open in dev and test and needs sign-in in production unless `METRICS_PUBLIC=true`.

`GET /api/v1/audit/events` now also lists `auth.*`, `operator.*`, `database.*`, `incident.*`, `chat.question`, and `knowledge.*` actions.

The copilot tool list adds `get_redo_logs`, `list_control_files`, `get_largest_segments`, `get_background_processes`, and `get_fleet_health`. Control files and segments are denied to a non-administrator. Fleet chat answers a fleet question with `get_fleet_health` from stored status.

## Phase 21 fleet copilot

`POST /api/v1/chat/threads` and each answer return `guided_commands` for the thread scope and the caller's role. A fleet thread receives the Health, Incidents, Programs, Storage, Policy, and Knowledge groups. A database or incident thread receives the common database questions and the catalog groups, without the ten questions that can only answer "not collected" and, for a non-administrator, without Database users, Control files, and Largest segments.

On a fleet thread a question about incidents, connected programs, datafile sizes, tablespace percent, policy violations, or health runs a fleet tool: `get_fleet_incidents`, `find_fleet_programs`, `find_fleet_datafiles`, `get_fleet_tablespaces`, `get_fleet_compliance`, or `get_fleet_health`. Incidents, compliance, tablespace percent, and health come from stored rows. Programs and datafile sizes are read from every enabled database with one fixed aggregate statement each, at most `AI_FLEET_MAX_DATABASES` databases per question, `AI_FLEET_CONCURRENCY` at a time, each within `AI_FLEET_DATABASE_TIMEOUT_SECONDS`. A program name or a size in the question filters the returned rows and is never part of a statement. A database that could not be read is listed as not known.

The answer has `answer.kind` `fleet`. `answer.summary` is written by the model from the tool rows, or is the tool summaries when no model answers. `answer.reading` holds one line per matching database in the form `Database: finding`, then the databases with no match, then those not read. `answer.limitations` states which facts are stored and which are live. When the fixed router has no match and a model is available, the model may nominate one fleet tool; the function list then shows `route_question`. An explanation, a document question, a SQL draft, and a change request on a fleet thread do not run a fleet tool.

## Operation proposals

Release 1 accepts one change: `Extend <tablespace> by <size> GB|MB`, for example `Extend USERS by 10 GB`. The API stores a fixed statement, `ALTER TABLESPACE USERS ADD DATAFILE SIZE 10240M`, and does not connect to Oracle to run it. `POST /api/v1/operations/proposals/{id}/execute` is refused. A proposal stays pending until a different DBA or platform administrator approves or rejects it. The requester cannot approve it. An expired pending proposal cannot be approved. Creation also checks that the caller is entitled to the database, the database is not in a maintenance restriction, `db_create_file_dest` is set, and the measured free recovery area can hold the request. A platform administrator bypasses the entitlement list.

- `POST /api/v1/operations/proposals` with `{target_id, request_text}` creates a pending proposal. An optional `Idempotency-Key` header returns the same proposal for a repeated request.
- `GET /api/v1/operations/proposals` and `GET /api/v1/operations/proposals/{id}` list proposals. `viewer` is denied. `operator` can read.
- `POST /api/v1/operations/proposals/{id}/approve` and `POST /api/v1/operations/proposals/{id}/reject` take `{reason}`.
- `GET /api/v1/audit/events` is limited to a platform administrator or a security auditor. Audit rows cannot be updated or deleted.
- `GET /api/v1/admin/platform` reports scheduler, model, notification, role, retention, and rule settings without secrets.
- `GET /api/v1/admin/config` lists the non-secret settings stored in `app_config`, grouped by section, and marks deployment, sign-in, execution, and filesystem settings as not editable. `PUT /api/v1/admin/config/{key}` with `{value}` changes one editable stored setting. Only an administrator can change it. A password, API key, `secret://` reference, fixed setting, or unknown setting is rejected. The collector and knowledge scan keep the timer they started with until the API restarts.
- `POST /api/v1/admin/targets/{id}/credential-rotation` accepts only a `secret://` reference.
- `GET /api/v1/auth/oidc/start` redirects to the configured IdP. If the issuer is empty, OIDC is off. If the IdP cannot be reached, local sign-in still works.
- `GET /metrics` exposes Prometheus counters and does not include secrets.

Full request/response schemas are available at `/docs`.
