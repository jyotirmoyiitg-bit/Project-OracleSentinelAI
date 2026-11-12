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
- `DELETE /api/v1/databases/{target_id}`: delete a target (204).
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
  `{paths: ["Knowledge/file.md"]}` limits the scan to those files and does not treat absent
  files as deletions. A full scan with no paths reflects additions, changes, and deletions.
  Paths that leave `Knowledge/` or `Policy/` are rejected. A scan already in progress returns 409.
- `POST /api/v1/knowledge/documents/{id}/acl` with `{roles, principals}` is administrator-only
  and copies the new grant onto every chunk of that document.

Scheduled scans run only when `KNOWLEDGE_ENABLED=true`. Embeddings stay local unless
`EMBEDDING_PROVIDER=remote`, `EMBEDDING_REMOTE_APPROVED=true`, a URL, and a privacy disclosure
are all set. There is no automatic failover from local to remote. The vector width is fixed at
128 until a migration changes the pgvector column.

## Copilot

The model never writes SQL and never chooses tools. An operator prompt that contains a SQL statement is refused before any tool or model runs. On a database chat, availability, error, alert log, tablespace, growth, performance, blocking, SQL, memory, backup, Data Guard, account, and job questions call the local readings MCP server, which runs one fixed read-only reading for the database alias. The latest alert log lines come from this database's alert in `V$DIAG_ALERT_EXT` for the last 7 days. Listener and client traces are excluded, so a failed connection to another database does not appear. A critical-error question still uses the shorter watched-error list. Listing database users is a separate reading. An admin operator receives the usernames and account status. An operator, dba, or auditor receives an authorization error, and the database is not queried. The answer lists those rows. Fleet chat does not open a database connection for those questions. A disabled or unreachable language model still leaves `/health/live`, fleet collection, and this chat available. The no-model answer includes guided commands and citations from the tools that ran.

When `AUTH_REQUIRED=true`, every API route except health and `/api/v1/auth/login`, `/api/v1/auth/session`, and `/api/v1/auth/logout` requires the `sentinel_session` cookie. The role comes from that session. `X-Sentinel-Role` is ignored while sign-in is required. Directory sign-in uses an LDAP bind and an allowed group. A local account is accepted only when the sign-in request sets `local` to true. With `AUTH_REQUIRED=false`, the portal stays in the pre-authentication mode used by the test harness.

- `POST /api/v1/chat/threads` accepts `{scope_type, scope_id}`. `scope_type` is `fleet`, `database`, or `incident`. Fleet rejects a scope id. A database or incident id must exist, and a disabled database returns 404. The response includes `id`, `provider`, `model`, `llm_enabled`, `disclosure`, and `guided_commands`. It does not include an API key or a provider URL.
- `POST /api/v1/chat/threads/{id}/messages` accepts `{content}` of 1 to 2000 characters and returns `text/event-stream` after the answer is stored. Events are `status`, `tool`, `citation`, `message`, and `done`. The `message` event includes `llm_used`, `provider_status`, `citations`, `tools`, and `guided_commands`. `llm_used` is false when the model is disabled, unreachable, or returns an invalid reply.
- `GET /api/v1/chat/threads/{id}/messages` returns the stored user and assistant messages for that thread.
- `GET /api/v1/admin/ai/providers` requires `X-Sentinel-Role: admin`. It reports the selected provider, whether hosted use is approved, the hosted vendor (`openai` or `anthropic`), and whether a hosted secret reference is configured. It does not return the secret or the provider URL. Hosted use requires approval, a model name, a `secret://` reference such as `secret://OPENAI_API_KEY` or `secret://ANTHROPIC_API_KEY`, and a privacy disclosure. An empty base URL uses `https://api.openai.com/v1` or `https://api.anthropic.com`. The key is read from the process environment or the matching line in the project `.env` and is not stored in settings or logs. A local outage does not call the hosted model.

`X-Sentinel-Role` defaults to `operator` until Phase 7 authentication. Document search inside the copilot uses the same ACL as `POST /api/v1/knowledge/search`.

Full request/response schemas are available at `/docs`.
