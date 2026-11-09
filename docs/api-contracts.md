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
- `compliance`: `{pass:0,fail:0,unknown:7}`. Phase 2 collects configuration evidence;
  policy evaluation belongs to Phase 4, so unevaluated checks remain unknown.

No observations means `unknown`, not healthy. A recent failed connectivity poll
means `offline`; failures of other required probes or stale data mean `unknown`.
Historical/stale values remain inspectable and are flagged `stale` in detail.

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

Full request/response schemas are available at `/docs`.
