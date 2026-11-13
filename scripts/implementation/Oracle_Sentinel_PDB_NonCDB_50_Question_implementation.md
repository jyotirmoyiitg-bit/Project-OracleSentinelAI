Oracle Sentinel — PDB / Non-CDB Operator Copilot Implementation Specification
Version: 1.0
Date: 2026-11-12
Scope: 50 production-operations questions, expected outputs, approved tool contracts, telemetry prerequisites and developer acceptance criteria.
Intended reader: AI coding agent, Python backend developer, TypeScript frontend developer, QA engineer.
1. Immutable scope and security contract
- A monitored target is one directly connected PDB (using its service) or one traditional non-CDB. Credentials belong to a normal, dedicated user; no C## common user, CDB$ROOT connection, cross-PDB discovery, or container switching.
- LLM tools never execute SQL, accept SQL fragments, connect to Oracle, or invoke a shell. All Copilot tools read approved telemetry, incidents, policies and runbooks from the application's PostgreSQL repository.
- A separate trusted collector may execute fixed, parameterized, read-only, version-tested Oracle queries against the registered target; no LLM input becomes Oracle SQL or a dynamic identifier. If the deployment policy prohibits any Oracle SQL, replace collectors with supported external telemetry sources and mark unavailable features UNSUPPORTED.
- The collector uses least-privilege object-level grants where feasible. Never assume that a PDB user can see V$/`GV$`, DBA_*, host, CDB, or instance-wide data; validate source grants, scope and semantic meaning at registration.
- Explicitly prohibit ALTER SESSION SET CONTAINER, DML, DDL, PL/SQL execution initiated by Copilot, SELECT ANY DICTIONARY as a blanket default, unrestricted DBA_HIST_* usage, diagnostic-pack-only operations without license verification, and action tools (kill session / resize / grant / patch / failover).
- For every value, track database_id, container_name, scope (PDB, NON_CDB, INSTANCE_CONTEXT, EXTERNAL), collection UTC timestamp, source, quality and units. Do not call instance-global counters PDB-scoped. PDB and NON_CDB rows must never merge silently.
- Tenant authorization is checked server-side before retrieving data; a user-supplied database_id is not proof of access.
- Handle unsupported/unlicensed, insufficient data, stale source and permissions distinctly. Never fabricate zero counts for data that has not been collected.
2. Common result envelope (mandatory for every question)
All tools return the following envelope. The data payload varies per question per §4. Timestamps are UTC ISO-8601; integer byte values represent bytes; rates are explicitly per-second or per-minute.
{
  "question_id": "Q01",
  "tool": "get_connectivity",
  "database_id": "db-uuid",
  "target_type": "PDB",
  "container_name": "PDBORCL",
  "scope": "PDB",
  "status": "OK",
  "health": "HEALTHY",
  "collected_at": "2026-11-12T12:00:00Z",
  "evaluated_at": "2026-11-12T12:00:02Z",
  "freshness_seconds": 2,
  "window": {"from": null, "to": null},
  "coverage_pct": 100.0,
  "data": {"reachable": true, "latency_ms": 18.4, "service": "PDBORCL", "last_success_at": "2026-11-12T12:00:00Z"},
  "evidence": [{"id": "ev-123", "source": "collector.connection_probe", "observed_at": "2026-11-12T12:00:00Z", "metric": "connectivity", "value": true, "unit": "boolean"}],
  "warnings": [],
  "recommendations": [],
  "error": null
}
Enums and semantics:
- status: OK | STALE | INSUFFICIENT_DATA | PERMISSION_REQUIRED | UNSUPPORTED | SOURCE_ERROR (tool/data availability, not database health).
- health: HEALTHY | WARNING | CRITICAL | UNKNOWN | NOT_APPLICABLE; default UNKNOWN when not determinable.
- scope: PDB | NON_CDB | INSTANCE_CONTEXT | EXTERNAL. Instance-context evidence must be labeled and cannot support claims about a PDB in isolation.
- error (optional): {"code": "...", "message": "...", "retryable": false}. Never embed credentials, SQL text, bind values or raw trace contents.
- data: typed tool-specific object; do not substitute a human-written prose answer for machine-readable data.
- evidence: source IDs that point to internally stored observations; recommendations cite evidence IDs. If empty, make epistemic limitations explicit.
- If the requested source has not been collected, return a non-OK status, data: {}, health: UNKNOWN, and explanatory warnings instead of manufacturing a value.
- PII-sensitive fields (SQL text, bind values, program/host/user identifiers, file paths) should be omitted or redacted unless explicitly permitted by RBAC policy.
Standard frontend presentation
Show: short answer → health/status chip → key values → timestamp/freshness → evidence and trend (where applicable) → recommended next steps. For lists, use sortable tables; for 7/30/90-day comparisons use trend charts with gaps shown as gaps, not zeros. Put STALE, PERMISSION_REQUIRED, etc. visibly in the UI; do not display stale evidence as live. A tool may return no recommendations if none are justified.
3. Tool invocation contract
Request format (agent to approved backend, never Oracle):
{"tool":"get_tablespace_usage","arguments":{"database_id":"db-uuid","window_hours":24,"limit":20}}
- Validate arguments with Pydantic. Use typed enum filters, limit in 1..100, periods constrained to supported ranges. No sql, query, where, order_by_sql, table_name or arbitrary execution arguments.
- Resolve and verify database_id using authenticated operator context, not LLM claims; narrow all repository reads by database ID and tenant.
- Registry maps intents/questions to tool + capabilities. Reuse one tool across multiple questions by safe, typed mode/filter values.
- Every tool call emits audit metadata: principal, database, tool, bounded arguments, timing, status and evidence IDs; no secrets.
- Collector and agent have separate processes/credentials. Agent service has no Oracle network route, connection string, or Oracle password.
- Scheduled collection is asynchronous to chat requests; UI refresh does not trigger unrestricted Oracle queries.
4. Catalog — 50 PDB/non-CDB questions and expected data payloads
Reading the entries: The field lists below define each question's data keys and types; they are examples of expected structure, not live measurements. Oracle view suggestions are collector implementation candidates and must pass a version/privilege/scope probe. Historical questions require recorded samples. An operator may select the database in the UI; the backend still validates authorization.
Health
Q01. Is the selected database or PDB reachable right now?
- Approved tool: get_connectivity
- Expected data: reachable:boolean; latency_ms:number|null; service:string; last_success_at:timestamp|null
- Collection / calculation: Collector connection probe; no SQL needed for failure determination.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q02. What is the database identity, Oracle version, and current container?
- Approved tool: get_identity
- Expected data: db_name:string|null; db_unique_name:string|null; version:string|null; container_name:string|null; database_type:PDB|NON_CDB
- Collection / calculation: Predefined identity query; values that are not visible remain null.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q03. When was the last successful monitoring collection, and are metrics current?
- Approved tool: get_telemetry_freshness
- Expected data: last_collected_at:timestamp|null; age_seconds:integer|null; expected_interval_seconds:integer; overdue:boolean
- Collection / calculation: PostgreSQL ingestion audit and collector heartbeats.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q04. What is the current database health and why?
- Approved tool: get_health_summary
- Expected data: health:HEALTHY|WARNING|CRITICAL|UNKNOWN; reasons:array; evaluated_checks:array
- Collection / calculation: Rules engine; independent telemetry freshness assessment.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q05. How many monitoring outages occurred during the past 7 days?
- Approved tool: get_availability_history
- Expected data: outage_count:integer; outages:array{start_at,end_at,duration_seconds}; observation_coverage_pct:number
- Collection / calculation: Historical heartbeat samples; distinguish monitoring outage from proven database outage.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Storage
Q06. Which tablespaces currently exceed the configured warning or critical thresholds?
- Approved tool: get_tablespace_usage
- Expected data: tablespaces:array{name,used_pct,threshold,level}; evaluated_at:timestamp
- Collection / calculation: PDB-local DBA_TABLESPACE_USAGE_METRICS or validated equivalent.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q07. How much used, allocated, free, and maximum space exists per tablespace?
- Approved tool: get_tablespace_usage
- Expected data: tablespaces:array{name,used_bytes,allocated_bytes,free_bytes,max_bytes,capacity_basis}
- Collection / calculation: Permanent/TEMP/UNDO classification; definitions of free vs growable are explicit.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q08. Which datafiles have autoextend disabled or are nearing their maximum size?
- Approved tool: get_datafile_capacity
- Expected data: datafiles:array{tablespace,file_label,allocated_bytes,max_bytes,autoextend,remaining_growth_bytes}
- Collection / calculation: DBA_DATA_FILES; avoid exposing full sensitive filesystem paths by default.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q09. Which tablespaces grew most over the last 30 days?
- Approved tool: get_tablespace_growth
- Expected data: growth:array{tablespace,start_bytes,end_bytes,delta_bytes,delta_pct}; coverage_pct:number
- Collection / calculation: Time-series snapshots; do not confuse allocation growth with used-space growth.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q10. When is each critical tablespace forecast to reach 90% or 100%?
- Approved tool: forecast_tablespace_capacity
- Expected data: forecasts:array{tablespace,threshold_pct,predicted_at,confidence_band,trend_bytes_per_day}; model:string
- Collection / calculation: Minimum historical coverage; return INSUFFICIENT_DATA for unreliable forecasts.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Sessions and locking
Q11. How many active and inactive sessions are connected to this PDB or non-CDB?
- Approved tool: get_session_summary
- Expected data: active_count:integer; inactive_count:integer; total_count:integer; scope:string
- Collection / calculation: V$SESSION if permitted, filter to current PDB/container.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q12. Which application users or programs have the most sessions?
- Approved tool: get_session_distribution
- Expected data: groups:array{username,program,session_count}; total_count:integer
- Collection / calculation: Use program/module metadata when available; redact sensitive client details.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q13. Are any sessions blocking other sessions?
- Approved tool: get_blocking_chains
- Expected data: chains:array{blocker_id,waiter_ids,wait_seconds,object_name}; blocker_count:integer
- Collection / calculation: V$SESSION / V$LOCK; only report validated visible relationships.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q14. Which sessions have run longer than the configured threshold?
- Approved tool: get_long_running_sessions
- Expected data: sessions:array{session_ref,username,sql_id,elapsed_seconds,status}; threshold_seconds:integer
- Collection / calculation: Session age is not always SQL execution age; label appropriately.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q15. Are session counts increasing abnormally relative to baseline?
- Approved tool: compare_session_baseline
- Expected data: current_count:integer; baseline_median:number; deviation_pct:number; anomaly:boolean; sample_count:integer
- Collection / calculation: PostgreSQL historical time series with comparable time-of-day baseline.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
SQL workload
Q16. Which SQL IDs used the most CPU over the last hour?
- Approved tool: get_top_sql
- Expected data: sql:array{sql_id,cpu_seconds_delta,executions_delta}; window:string; scope:string
- Collection / calculation: Delta snapshots, never assume cumulative counters represent window usage.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q17. Which SQL IDs have the highest average elapsed time per execution?
- Approved tool: get_top_sql
- Expected data: sql:array{sql_id,avg_elapsed_ms,executions_delta}; minimum_executions:integer
- Collection / calculation: Use interval deltas; avoid exposing SQL text/bind values.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q18. Which SQL IDs caused the most logical and physical reads?
- Approved tool: get_top_sql_io
- Expected data: sql:array{sql_id,logical_reads_delta,physical_reads_delta,executions_delta}
- Collection / calculation: Local permitted V$SQLSTATS/V$SQL, filter by container when possible.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q19. Did any monitored SQL ID degrade compared with its prior baseline?
- Approved tool: get_sql_regressions
- Expected data: regressions:array{sql_id,baseline_avg_ms,current_avg_ms,ratio,executions}; confidence:string
- Collection / calculation: Requires comparable periods and sufficient executions; avoid false positives.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q20. Has the observed plan hash changed for a high-impact SQL ID?
- Approved tool: get_plan_change_events
- Expected data: changes:array{sql_id,previous_plan_hash,current_plan_hash,first_seen_at}; evidence:array
- Collection / calculation: Plan hash only; plan change is a correlation, not proof of causation.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Waits and performance
Q21. What are the top wait events affecting the connected database?
- Approved tool: get_wait_event_summary
- Expected data: events:array{event,wait_class,time_waited_ms_delta,waits_delta}; window:string
- Collection / calculation: Prefer scoped wait-session sampling; never label CDB-wide totals PDB-only.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q22. Are active sessions waiting on I/O, locks, or commits?
- Approved tool: get_active_session_waits
- Expected data: wait_classes:array{class,session_count}; sessions_sampled:integer
- Collection / calculation: Current-container V$SESSION sampling; scope and sample timestamp explicit.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q23. Is database load higher than its usual baseline for this time of day?
- Approved tool: compare_load_baseline
- Expected data: current_aas:number|null; baseline_aas:number|null; deviation_pct:number|null; status:string
- Collection / calculation: AAS derived from repeated active session sampling, not host CPU.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q24. Are parse activity and hard parses unusually high?
- Approved tool: get_parse_pressure
- Expected data: parse_rate:number|null; hard_parse_rate:number|null; hard_parse_ratio:number|null; scope:string
- Collection / calculation: Use only container-attributed metrics; mark unsupported otherwise.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q25. Which performance metrics changed most in the last 30 minutes?
- Approved tool: get_metric_anomalies
- Expected data: metrics:array{name,previous_value,current_value,change_pct,unit}; coverage_pct:number
- Collection / calculation: Use collected PDB-scoped metrics, with consistent units/windows.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
TEMP, undo and transactions
Q26. How much temporary tablespace space is in use right now?
- Approved tool: get_temp_usage
- Expected data: temp_used_bytes:number; temp_capacity_bytes:number; used_pct:number; scope:string
- Collection / calculation: PDB-scoped TEMP usage; verify privilege and segment visibility.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q27. Which sessions are consuming the most TEMP space?
- Approved tool: get_temp_consumers
- Expected data: sessions:array{session_ref,username,temp_bytes,sql_id}; total_bytes:number
- Collection / calculation: V$TEMPSEG_USAGE/other supported scoped sources.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q28. Were there any temporary space allocation errors in the past day?
- Approved tool: get_oracle_error_events
- Expected data: events:array{ora_code,first_seen_at,last_seen_at,count}; source:string
- Collection / calculation: Accessible alert/trace/event feed; not guaranteed from PDB dictionary.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q29. Are long-running transactions consuming significant undo or holding locks?
- Approved tool: get_transaction_pressure
- Expected data: transactions:array{session_ref,age_seconds,undo_bytes_estimate,blocked_sessions}; scope:string
- Collection / calculation: Use only safely attributable transaction diagnostics; null if unavailable.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q30. Have ORA-01555 errors been reported in the connected database?
- Approved tool: get_oracle_error_events
- Expected data: ora_01555_count:integer|null; events:array{occurred_at,source}; coverage_pct:number
- Collection / calculation: Requires scoped error telemetry; no events is not proof of absence without coverage.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Schemas and objects
Q31. Which application schemas consume the most segment storage?
- Approved tool: get_schema_storage
- Expected data: schemas:array{name,allocated_bytes,object_count}; total_bytes:number
- Collection / calculation: DBA_SEGMENTS with local dictionary grants.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q32. What are the largest tables and indexes in this database?
- Approved tool: get_largest_segments
- Expected data: segments:array{owner,name,type,allocated_bytes,tablespace}; top_n:integer
- Collection / calculation: DBA_SEGMENTS; partitions and LOBs identified explicitly.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q33. Which objects are invalid in monitored application schemas?
- Approved tool: get_invalid_objects
- Expected data: objects:array{owner,name,object_type,status}; counts_by_owner:object
- Collection / calculation: DBA_OBJECTS; restrict to configured schemas.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q34. Are any indexes unusable?
- Approved tool: get_unusable_indexes
- Expected data: indexes:array{owner,index_name,table_name,status,partition_name}; total:integer
- Collection / calculation: DBA_INDEXES and index partition views as necessary.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q35. Which tables have missing or stale optimizer statistics?
- Approved tool: get_optimizer_stats_health
- Expected data: tables:array{owner,table_name,last_analyzed,stale_stats,num_rows}; total:integer
- Collection / calculation: DBA_TAB_STATISTICS; stale flags are advisory, not universal proof.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Jobs and security
Q36. Which local Scheduler jobs have failed in the past 24 hours?
- Approved tool: get_scheduler_failures
- Expected data: jobs:array{owner,job_name,status,error_code,log_date}; failure_count:integer
- Collection / calculation: DBA_SCHEDULER_JOB_RUN_DETAILS if granted.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q37. Which Scheduler jobs are currently running unusually long?
- Approved tool: get_long_running_jobs
- Expected data: jobs:array{owner,job_name,elapsed_seconds,baseline_seconds,overrun_ratio}
- Collection / calculation: Scheduler running jobs + history; current-PDB visibility.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q38. Which application accounts are locked or expired?
- Approved tool: get_account_status
- Expected data: accounts:array{username,account_status,expiry_date}; counts_by_status:object
- Collection / calculation: DBA_USERS; approved application users only.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q39. Which monitored user accounts will expire within 30 days?
- Approved tool: get_expiring_accounts
- Expected data: accounts:array{username,expiry_date,days_remaining}; horizon_days:integer
- Collection / calculation: DBA_USERS; exclude service/admin users unless authorized.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q40. Which compliance checks are failing for this database?
- Approved tool: get_compliance_results
- Expected data: checks:array{id,name,status,severity,evidence_id}; pass_count:integer; fail_count:integer
- Collection / calculation: Rules/approved compliance policy; never imply a failed check is a legal breach.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Configuration and capacity
Q41. What are the selected database’s relevant configuration settings?
- Approved tool: get_configuration_snapshot
- Expected data: parameters:array{name,value,scope,is_default}; source_timestamp:timestamp
- Collection / calculation: Allowlisted V$PARAMETER values; label inherited instance settings.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q42. Did any monitored configuration parameter change recently?
- Approved tool: get_configuration_changes
- Expected data: changes:array{name,old_value,new_value,detected_at,scope}; coverage_pct:number
- Collection / calculation: Compare versioned snapshots; discovery time ≠ exact change time.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q43. What is the total current storage footprint of the selected database?
- Approved tool: get_storage_footprint
- Expected data: allocated_bytes:number; used_bytes:number|null; segment_bytes:number; scope:string
- Collection / calculation: Include clear measure distinction; PDB-only datafiles if applicable.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q44. Which schemas or segments account for the most storage growth this month?
- Approved tool: get_segment_growth
- Expected data: items:array{owner,segment_name,type,growth_bytes}; sample_coverage_pct:number
- Collection / calculation: Historical DBA_SEGMENTS snapshots; DDL or segment move can distort comparisons.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q45. Is the database on track to breach any configured local storage threshold in 30 days?
- Approved tool: get_capacity_risks
- Expected data: risks:array{metric,threshold,current,forecast_30d,risk_level}; assumptions:array
- Collection / calculation: Local capacity only; avoid CDB/host free-disk promises.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Incidents and intelligence
Q46. What are the three most urgent issues affecting this database now?
- Approved tool: get_prioritized_findings
- Expected data: findings:array{id,severity,title,evidence_ids,recommended_action}; overall_status:string
- Collection / calculation: Rule-based severity plus telemetry freshness; max 3.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q47. What changed immediately before the latest detected incident?
- Approved tool: correlate_incident_timeline
- Expected data: incident_id:string; changes:array{metric,observed_at,before,after}; correlation_only:boolean
- Collection / calculation: Telemetry timeline only; do not claim causation.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q48. Has a similar incident occurred before, and what action was recorded?
- Approved tool: search_incident_history
- Expected data: matches:array{incident_id,similarity,occurred_at,resolution_summary}; confidence:string
- Collection / calculation: PostgreSQL incident corpus; exclude untrusted narrative as executable commands.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q49. What are the plausible causes of the current slowdown?
- Approved tool: explain_performance_issue
- Expected data: hypotheses:array{cause,confidence,evidence_ids,missing_evidence}; conclusion_status:string
- Collection / calculation: Evidence-based reasoning; unknown if telemetry insufficient.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
Q50. Summarize the past 7 days of health, capacity, performance, and incidents.
- Approved tool: get_weekly_database_summary
- Expected data: availability:object; capacity:object; performance:object; incidents:object; data_coverage_pct:number
- Collection / calculation: Use multiple approved tools; concise operator report, each section timestamped.
- Expected UI: Summary text + relevant KPI/table/trend + data freshness/evidence.
5. Collector source inventory and capability probing
Treat these as candidate sources, not guaranteed grants or guarantees that every column is scoped to the local PDB:
Category	Candidate source(s)	Scope and implementation notes
Identity and health	connection probe, SYS_CONTEXT('USERENV','CON_NAME'), V$VERSION, selected V$DATABASE metadata	PDB open-mode and instance uptime may not be visible from a normal PDB session; unavailable = unsupported/partial.
Tablespace/datafiles	DBA_TABLESPACE_USAGE_METRICS, DBA_TABLESPACES, DBA_DATA_FILES, DBA_FREE_SPACE, DBA_TEMP_FILES	Prefer documented USED_PERCENT semantics; used/allocated/max are different.
Sessions/locks	V$SESSION, V$LOCK, optionally scoped GV$ (if permitted)	Filter correctly by PDB CON_ID and session identity where present; never leak other containers.
SQL workload	V$SQLSTATS, V$SQL, selected plan metadata	Only with validated container-scoped visibility. Store IDs and aggregate deltas, not literal SQL text.
Waits/load	current-container session wait sampling, supported scoped metrics	V$SYSTEM_EVENT and instance metrics can be global: never relabel them PDB-only.
TEMP/undo	V$TEMPSEG_USAGE, local TEMP metadata, permitted transaction views	Undo often managed at instance/CDB level; report unsupported if unable to attribute safely.
Objects/statistics	DBA_SEGMENTS, DBA_OBJECTS, DBA_INDEXES, DBA_TAB_STATISTICS	Scope to selected PDB and approved schemas.
Jobs/accounts	DBA_SCHEDULER_*, DBA_USERS	Restricted metadata; never store authentication material.
Config	allowlisted V$PARAMETER values; stored snapshots	Configuration may be inherited/instance-wide; label INSTANCE_CONTEXT.
Errors and incidents	collector-accessible alert/error feed, PostgreSQL incident log	If alert logs are not locally accessible, mark error-history questions unavailable rather than return zero.


Registration procedure: (1) verify connection goes to configured PDB service/non-CDB; (2) establish target_type and container_name; (3) test each predefined read-only source under the actual monitoring user; (4) record scope, license requirements, supported data fields and error codes; (5) register supported questions and mark others PERMISSION_REQUIRED or UNSUPPORTED. Never attempt to self-grant privileges.
6. PostgreSQL telemetry model (minimum logical schema)
- monitored_databases(id, tenant_id, target_type, service_alias, container_name, version, collection_interval_seconds, enabled) — credentials stored separately in an encrypted secret manager.
- database_capabilities(database_id, capability_key, status, scope, source_name, checked_at, reason) — active probes and permissions.
- metric_samples(database_id, metric_name, scope, dimension_json, observed_at, ingested_at, value_num, unit, quality) — indexed by (database_id, metric_name, observed_at); partition if needed.
- entity_snapshots(database_id, entity_type, entity_key, observed_at, payload_json) — tablespaces, datafiles, SQL IDs, schema objects, jobs.
- collector_runs(database_id, collector_name, started_at, finished_at, status, records_collected, error_code) — distinguish connection failures and parser errors.
- alert_events(id, database_id, observed_at, severity, code, source, metadata_json) — no unbounded alert-log text.
- incidents(id, database_id, opened_at, closed_at, severity, status, description, resolution_summary).
- evidence_refs(id, database_id, source_table, source_record_id, observed_at) — immutable provenance for Copilot conclusions.
- tool_audit(id, principal_id, database_id, tool, request_json, invoked_at, completed_at, status).
Use UTC timestamps, explicit scope and data quality, retention rules for raw samples, and aggregated rollups for longer time windows. Add indexes and tenant-aware row-level protection/authorization as appropriate.
7. Implementation milestones
1. M1 — security and capabilities: enforce no Oracle connection from agent, database RBAC, capability discovery, structured error envelope, audit trail.
2. M2 — basic telemetry: Q01–Q15, connection heartbeat, tablespace/disk, sessions, locks and history storage.
3. M3 — performance: Q16–Q25, scoped SQL deltas and wait/session sampling; baseline and regression logic.
4. M4 — operational coverage: Q26–Q45, TEMP/undo (where possible), objects, jobs, account status, config and capacity.
5. M5 — Copilot reasoning: Q46–Q50, correlation with evidence IDs, guarded retrieval of runbooks, explainable summary and UI integration.
8. Test and acceptance criteria
- [ ] All 50 questions have an intent mapping, approved tool, Pydantic input/output schema, example payload and frontend display state.
- [ ] All requests accept exactly one authorized database_id and reject cross-tenant / unauthorized access, including indirect comparisons.
- [ ] No LLM-generated SQL can reach Oracle; deny-listed tool names execute_sql, sql_query, execute_shell and arbitrary SQL parameters are absent.
- [ ] An audit demonstrates that no Copilot tool accesses Oracle directly; collector credentials are unavailable to the agent process.
- [ ] PDB connections are verified to remain in the configured PDB; any root target is rejected during registration.
- [ ] Data from CON_ID=0/root/other PDB is never represented as selected-PDB measurements.
- [ ] Missing SELECT grants produce PERMISSION_REQUIRED; structurally unavailable views produce UNSUPPORTED, not success with empty data.
- [ ] Collector failure produces STALE/`SOURCE_ERROR`; the dashboard never marks an unverified database healthy.
- [ ] Empty results are distinguished from missing telemetry (0 incidents requires positive telemetry coverage).
- [ ] All history calculations use actual sample windows; no interpolation across missing intervals without explicit flagging.
- [ ] Counter deltas account for restarts/resets/negative differences; SQL metrics are not calculated from raw cumulative totals as window totals.
- [ ] Bytes vs blocks, allocated vs used vs maximum, percentages and PDB-vs-instance scope have unit tests.
- [ ] Forecasts require a documented minimum data window and return uncertainty/insufficient-data when unsuitable.
- [ ] Response evidence can be inspected in UI; timestamps and warnings remain visible alongside conclusions.
- [ ] No Copilot tool changes database state. Any future action system is a separately authorized, explicitly approved design.
9. Suggested response fixtures for AI coder tests
Fresh result: status=OK, health=HEALTHY|WARNING|CRITICAL, positive telemetry coverage and evidence IDs.

Stale result: status=STALE, health=UNKNOWN, last valid sample timestamp preserved, explanatory warning.

Missing grant: status=PERMISSION_REQUIRED, health=UNKNOWN, data={}, safe required capability/grant description.

Unsupported scoped metric: status=UNSUPPORTED, health=NOT_APPLICABLE or UNKNOWN, with scope explaining why.

No historical baseline: status=INSUFFICIENT_DATA, health=UNKNOWN, coverage %, required minimum history and no invented forecast.

Empty but adequately observed: status=OK, data contains an empty list or zero count, evidence that the observation window was collected, and coverage %.
10. Source references for implementing Oracle collectors
- Oracle 19c Monitoring CDBs and PDBs: https://docs.oracle.com/en/database/oracle/oracle-database/19/multi/viewing-information-about-cdbs-and-pdbs-with-sql-plus.html
- Oracle 19c About Dynamic Performance Views: https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/about-dynamic-performance-views.html
- Oracle DBA_TABLESPACE_USAGE_METRICS: https://docs.oracle.com/en/database/oracle/oracle-database/21/refrn/DBA_TABLESPACE_USAGE_METRICS.html
Developer note: Oracle Database 12c/19c/21c/23ai/26ai details, features, dictionary visibility and licensing vary; do not assume that one query definition works identically for every version. Build version-qualified collectors and integration-test them as the designated local user. Do not implement the above telemetry-source candidates as copy/paste arbitrary SQL tools.