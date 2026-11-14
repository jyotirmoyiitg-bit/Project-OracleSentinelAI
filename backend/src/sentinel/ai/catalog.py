"""The 50 PDB and non-CDB operator questions.

Each question names one approved tool. The copilot answers from a fixed reading or from
stored Sentinel records. It does not turn the question into SQL. Questions that need
history this deployment does not keep say so instead of inventing a number.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.ai.activity import TOOL_NAMES
from sentinel.ai.types import ToolOutcome
from sentinel.persistence.models import CollectorCheckpoint, Incident

CatalogStatus = Literal[
    "OK", "STALE", "INSUFFICIENT_DATA", "PERMISSION_REQUIRED", "UNSUPPORTED", "SOURCE_ERROR",
]
CatalogHealth = Literal["HEALTHY", "WARNING", "CRITICAL", "UNKNOWN", "NOT_APPLICABLE"]


class CatalogEnvelope(BaseModel):
    question_id: str
    tool: str
    status: CatalogStatus
    health: CatalogHealth
    scope: str
    summary: str
    lines: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class CatalogQuestion:
    question_id: str
    tool: str
    group: str
    label: str
    prompt: str
    reading: str | None = None
    repository: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    empty: str = "No row matched this question."
    limitation: str = ""
    next_step: str = ""
    scope: str = "PDB"
    forced_status: CatalogStatus | None = None
    forced_health: CatalogHealth | None = None


def _gap(
    question_id: str, tool: str, group: str, label: str, prompt: str, reason: str, step: str,
    status: CatalogStatus = "INSUFFICIENT_DATA",
) -> CatalogQuestion:
    health: CatalogHealth = "NOT_APPLICABLE" if status == "UNSUPPORTED" else "UNKNOWN"
    return CatalogQuestion(
        question_id, tool, group, label, prompt, empty=reason, limitation=reason, next_step=step,
        forced_status=status, forced_health=health,
    )


QUESTIONS: tuple[CatalogQuestion, ...] = (
    CatalogQuestion(
        "Q01", "get_connectivity", "Health", "Reachable",
        "Is the selected database or PDB reachable right now?",
        reading="availability", include=("status open", "status mounted", "services:"),
        empty="The connectivity reading did not return an instance status.",
        limitation="A successful reading means the selected service accepted the monitoring connection. Latency is not measured separately. The portal's stored status is a separate check.",
        next_step="If the instance status is not OPEN, check that service before looking at workload.",
    ),
    CatalogQuestion(
        "Q02", "get_identity", "Health", "Identity",
        "What is the database identity, Oracle version, and current container?",
        reading="availability", include=("open_mode", "pdb "),
        empty="Identity rows were not returned.",
        limitation="Oracle version is not part of this reading. The container name is taken from the PDB rows visible to the monitoring user.",
        next_step="Use the database name and open mode below as the identity of the selected target.",
    ),
    CatalogQuestion(
        "Q03", "get_telemetry_freshness", "Health", "Freshness",
        "When was the last successful monitoring collection, and are metrics current?",
        empty="No successful scheduled collection is recorded for this database.",
        limitation="Freshness comes from collector checkpoints in Sentinel, not from a new Oracle query.",
        next_step="If the checkpoint is missing or overdue, the scheduled collector has not confirmed this database recently.",
    ),
    CatalogQuestion(
        "Q04", "get_health_summary", "Health", "Health summary",
        "What is the current database health and why?",
        reading="issues", repository=("get_database_health",),
        include=("status open", "status mounted", "critical alert", "ora-", "above ", "blocked"),
        empty="The health reading did not return a reason.",
        limitation="Health is the current readings. It is evidence, not a confirmed root cause.",
        next_step="Start with the first warning or error line, then open the matching guided question.",
    ),
    CatalogQuestion(
        "Q05", "get_availability_history", "Health", "Outages, 7 days",
        "How many monitoring outages occurred during the past 7 days?",
        reading="availability", include=("startup or shutdown", "starting oracle", "instance terminated", "shutdown complete"),
        empty="No startup or shutdown message was found in the alert log for the last 7 days.",
        limitation="These are database alert startup and shutdown lines, not Sentinel heartbeat outages. A planned restart is included when it is logged.",
        next_step="Treat a logged restart as a database event. It does not by itself prove a monitoring outage.",
    ),
    CatalogQuestion(
        "Q06", "get_tablespace_usage", "Storage", "Tablespaces over threshold",
        "Which tablespaces currently exceed the configured warning or critical thresholds?",
        reading="tablespace", include=("above 80", "above 90", "above 95"),
        empty="No permanent tablespace is at or above 80 percent used.",
        limitation="The warning line is 80 percent used and the critical line is 95 percent used, based on allocated bytes.",
        next_step="Review each tablespace listed above before adding a datafile.",
    ),
    CatalogQuestion(
        "Q07", "get_tablespace_usage", "Storage", "Tablespace space",
        "How much used, allocated, free, and maximum space exists per tablespace?",
        reading="tablespace", include=("percent used",),
        empty="No tablespace space row was returned.",
        limitation="Used and allocated bytes are shown. Maximum autoextend size is included only for datafiles already at 90 percent of that maximum.",
        next_step="Use the percent-used lines for current space and the datafile lines for files near their maximum.",
    ),
    CatalogQuestion(
        "Q08", "get_datafile_capacity", "Storage", "Datafile headroom",
        "Which datafiles have autoextend disabled or are nearing their maximum size?",
        reading="tablespace", include=("autoextend", "maximum"),
        empty="No datafile headroom row was returned.",
        limitation="Autoextend is reported per tablespace. Individual files are listed when an autoextensible file is at or above 90 percent of its maximum.",
        next_step="A tablespace with autoextend NO cannot grow by itself.",
    ),
    CatalogQuestion(
        "Q09", "get_tablespace_growth", "Storage", "Tablespace growth",
        "Which tablespaces grew most over the last 30 days?",
        reading="growth", include=("fills in", "not sufficient", "gb/day"),
        empty="Retained history is not sufficient to rank tablespace growth.",
        limitation="Growth uses retained Sentinel samples. Allocation growth and used-space growth are not separated when history is thin.",
        next_step="Collect samples for at least 24 hours before treating a forecast as usable.",
    ),
    CatalogQuestion(
        "Q10", "forecast_tablespace_capacity", "Storage", "Capacity forecast",
        "When is each critical tablespace forecast to reach 90 percent or 100 percent?",
        reading="growth", include=("fills in", "not sufficient"),
        empty="Retained history is not sufficient to forecast when a tablespace will fill.",
        limitation="A forecast needs regular samples spanning at least 24 hours. No forecast is invented when that history is missing.",
        next_step="Use the days-to-fill figure only when a forecast row is present.",
    ),
    CatalogQuestion(
        "Q11", "get_session_summary", "Sessions", "Session counts",
        "How many active and inactive sessions are connected to this PDB or non-CDB?",
        reading="performance", include=("user sessions by status",),
        empty="No user-session count by status was returned.",
        limitation="Counts are current user sessions visible to the monitoring user. Background processes are not included.",
        next_step="Compare ACTIVE with INACTIVE. A high ACTIVE count is the workload signal.",
    ),
    CatalogQuestion(
        "Q12", "get_session_distribution", "Sessions", "Session distribution",
        "Which application users or programs have the most sessions?",
        reading="blocking", include=("service ",),
        empty="No per-user session count was returned.",
        limitation="Counts are grouped by username and service. Program names are not in this count.",
        next_step="The largest session count is the first account to review.",
    ),
    CatalogQuestion(
        "Q13", "get_blocking_chains", "Sessions", "Blocking",
        "Are any sessions blocking other sessions?",
        reading="blocking", include=("blocked",),
        empty="No user session is currently blocked by another session.",
        limitation="Only a blocking relationship visible in the current session view is reported.",
        next_step="If a session is blocked, identify the blocker before looking at SQL.",
    ),
    CatalogQuestion(
        "Q14", "get_long_running_sessions", "Sessions", "Long sessions",
        "Which sessions have run longer than the configured threshold?",
        reading="blocking", include=("long ", "older than 10 minutes"),
        empty="No user session has a current call older than 10 minutes.",
        limitation="The threshold is 10 minutes of last-call elapsed time. That is session call age, not always SQL execution age.",
        next_step="A long call is a candidate for review. It is not killed from the copilot.",
    ),
    _gap(
        "Q15", "compare_session_baseline", "Sessions", "Session baseline",
        "Are session counts increasing abnormally relative to baseline?",
        "No time-of-day session baseline is stored, so no deviation is calculated.",
        "Collect session counts over comparable hours before asking for an anomaly.",
    ),
    CatalogQuestion(
        "Q16", "get_top_sql", "SQL", "Top SQL by CPU",
        "Which SQL IDs used the most CPU over the last hour?",
        reading="top_sql", include=("cpu=",),
        empty="No user SQL was returned from the cursor cache.",
        limitation="CPU time is cumulative in the cursor cache, not CPU used during the last hour. SQL text may be included because this reading already returns it.",
        next_step="Treat the order as a cache ranking, not as an hour delta.",
        scope="INSTANCE_CONTEXT",
    ),
    CatalogQuestion(
        "Q17", "get_top_sql", "SQL", "Top SQL by elapsed time",
        "Which SQL IDs have the highest average elapsed time per execution?",
        reading="sql_workload", include=("elapsed ",),
        empty="No elapsed-time SQL row was returned.",
        limitation="Elapsed time is cumulative cursor-cache time, not an average over the last hour, and executions may be zero.",
        next_step="Divide elapsed time by executions only when the execution count is greater than zero.",
        scope="INSTANCE_CONTEXT",
    ),
    CatalogQuestion(
        "Q18", "get_top_sql_io", "SQL", "Top SQL by reads",
        "Which SQL IDs caused the most logical and physical reads?",
        reading="sql_workload", include=("reads ",),
        empty="No read-ranked SQL row was returned.",
        limitation="Buffer gets and disk reads are cumulative cursor-cache totals, not a one-hour delta.",
        next_step="Use the READS lines for physical and logical read ranking.",
        scope="INSTANCE_CONTEXT",
    ),
    _gap(
        "Q19", "get_sql_regressions", "SQL", "SQL regressions",
        "Did any monitored SQL ID degrade compared with its prior baseline?",
        "No SQL baseline is stored, so a regression ratio is not calculated.",
        "Keep comparable SQL samples before asking which statement got slower.",
    ),
    CatalogQuestion(
        "Q20", "get_plan_change_events", "SQL", "Plan changes",
        "Has the observed plan hash changed for a high-impact SQL ID?",
        reading="sql_workload", include=("plan ", "more than one plan", "no user sql"),
        empty="No user SQL ID currently has more than one execution plan in the cache.",
        limitation="More than one plan in the cache is not proof of when the plan changed, and it is not proof that the plan caused a slowdown.",
        next_step="Treat multiple plans as a correlation to investigate, not as a cause.",
        scope="INSTANCE_CONTEXT",
    ),
    CatalogQuestion(
        "Q21", "get_wait_event_summary", "Waits", "Top waits",
        "What are the top wait events affecting the connected database?",
        reading="performance", include=("wait ",),
        empty="No non-idle wait event was returned.",
        limitation="Wait time is cumulative since startup and can be instance-wide. It is not labeled as PDB-only.",
        next_step="Use the largest wait time as the first event to review.",
        scope="INSTANCE_CONTEXT",
    ),
    CatalogQuestion(
        "Q22", "get_active_session_waits", "Waits", "Active waits",
        "Are active sessions waiting on I/O, locks, or commits?",
        reading="performance", include=("user sessions by status",),
        empty="No active-session status count was returned.",
        limitation="This reading counts user sessions by status. It does not split current waits into I/O, lock, and commit classes.",
        next_step="Use the blocking question for lock waits. Wait-class sampling is not available here.",
    ),
    _gap(
        "Q23", "compare_load_baseline", "Waits", "Load baseline",
        "Is database load higher than its usual baseline for this time of day?",
        "No active-session baseline is stored, so load is not compared with a usual hour.",
        "Sample active sessions over comparable hours before asking for a load anomaly.",
    ),
    CatalogQuestion(
        "Q24", "get_parse_pressure", "Waits", "Parse pressure",
        "Are parse activity and hard parses unusually high?",
        reading="memory_config", include=("hard parses",),
        empty="Parse counts were not returned.",
        limitation="Parse counts are cumulative since startup. One sample is not a per-second rate and is not marked unusual against a baseline.",
        next_step="Review the hard-parse share of total parses. A high share needs a second sample before it is called a spike.",
        scope="INSTANCE_CONTEXT",
    ),
    _gap(
        "Q25", "get_metric_anomalies", "Waits", "Metric changes",
        "Which performance metrics changed most in the last 30 minutes?",
        "Two samples 30 minutes apart are not stored for this comparison, so no change percent is calculated.",
        "Wait for two stored performance samples before asking what changed.",
    ),
    CatalogQuestion(
        "Q26", "get_temp_usage", "Temporary", "Temporary space",
        "How much temporary tablespace space is in use right now?",
        reading="tablespace", include=("temporary ",),
        empty="No temporary tablespace usage was returned.",
        limitation="Temporary usage is the current TEMP header visible to the monitoring user.",
        next_step="A high percent used on TEMP is the figure to review.",
    ),
    _gap(
        "Q27", "get_temp_consumers", "Temporary", "TEMP consumers",
        "Which sessions are consuming the most TEMP space?",
        "Per-session temporary segment usage is not in the approved readings.",
        "Use the temporary-space question for total TEMP usage.",
        status="UNSUPPORTED",
    ),
    CatalogQuestion(
        "Q28", "get_oracle_error_events", "Temporary", "TEMP errors",
        "Were there any temporary space allocation errors in the past day?",
        reading="ora_errors", include=("ora-01652",),
        empty="No ORA-01652 temporary-space error was in the Oracle errors from the last 24 hours.",
        limitation="The search looks at ORA- lines from this database alert log for the last 24 hours, limited to the newest 30.",
        next_step="No ORA-01652 in that window means none was found. It is not a guarantee that every trace was searched.",
    ),
    CatalogQuestion(
        "Q29", "get_transaction_pressure", "Temporary", "Long transactions",
        "Are long-running transactions consuming significant undo or holding locks?",
        reading="blocking", include=("long ", "blocked", "older than 10 minutes"),
        empty="No long call or blocked session was returned.",
        limitation="Undo bytes are not in this reading. A long call or a block is not a measured undo size.",
        next_step="Use a blocked or long session as a candidate, not as a measured undo consumer.",
    ),
    CatalogQuestion(
        "Q30", "get_oracle_error_events", "Temporary", "ORA-01555",
        "Have ORA-01555 errors been reported in the connected database?",
        reading="ora_errors", include=("ora-01555",),
        empty="No ORA-01555 error was in the Oracle errors from the last 24 hours.",
        limitation="Absence from the newest 30 ORA- lines is not proof that the error never occurred outside that window.",
        next_step="If a line is listed, the timestamp is when the alert recorded it.",
    ),
    _gap(
        "Q31", "get_schema_storage", "Objects", "Schema storage",
        "Which application schemas consume the most segment storage?",
        "Segment storage by schema is not in the approved readings. DBA_SEGMENTS is not queried.",
        "Use the tablespace question for space by tablespace.",
        status="UNSUPPORTED",
    ),
    _gap(
        "Q32", "get_largest_segments", "Objects", "Largest segments",
        "What are the largest tables and indexes in this database?",
        "Largest tables and indexes are not in the approved readings. DBA_SEGMENTS is not queried.",
        "Use the tablespace question for which tablespace is using space.",
        status="UNSUPPORTED",
    ),
    CatalogQuestion(
        "Q33", "get_invalid_objects", "Objects", "Invalid objects",
        "Which objects are invalid in monitored application schemas?",
        reading="security", include=("invalid ",),
        empty="No invalid application object was returned.",
        limitation="Invalid objects exclude Oracle-maintained schemas. This is current status, not a compile history.",
        next_step="An invalid procedure or view is the object to review. The copilot does not compile it.",
    ),
    _gap(
        "Q34", "get_unusable_indexes", "Objects", "Unusable indexes",
        "Are any indexes unusable?",
        "Index usability is not in the approved readings. DBA_INDEXES is not queried.",
        "Invalid objects are a separate question and do not list unusable indexes.",
        status="UNSUPPORTED",
    ),
    _gap(
        "Q35", "get_optimizer_stats_health", "Objects", "Optimizer statistics",
        "Which tables have missing or stale optimizer statistics?",
        "Optimizer statistics are not in the approved readings. DBA_TAB_STATISTICS is not queried.",
        "A missing statistics reading is not the same as stale statistics.",
        status="UNSUPPORTED",
    ),
    CatalogQuestion(
        "Q36", "get_scheduler_failures", "Jobs", "Failed jobs",
        "Which local Scheduler jobs have failed in the past 24 hours?",
        reading="failed_jobs", include=("failed", "stopped", "did not succeed"),
        empty="No scheduler job failed or stopped in the checked window.",
        limitation="The scheduler reading covers the last 2 days, which is wider than 24 hours.",
        next_step="A failed job name and time are listed. The job is not restarted from the copilot.",
    ),
    CatalogQuestion(
        "Q37", "get_long_running_jobs", "Jobs", "Long jobs",
        "Which Scheduler jobs are currently running unusually long?",
        reading="jobs", include=("running ",),
        empty="No scheduler job or DBMS_JOB is running.",
        limitation="No runtime baseline is stored, so an overrun ratio is not calculated. The list is jobs running now.",
        next_step="Elapsed time is how long the job has been running, not how long it usually runs.",
    ),
    CatalogQuestion(
        "Q38", "get_account_status", "Jobs", "Locked accounts",
        "Which application accounts are locked or expired?",
        reading="security", include=("account ",),
        empty="No locked or expired application account was returned.",
        limitation="Oracle-maintained accounts such as SYS and SYSTEM are excluded. OPEN accounts are not listed.",
        next_step="A locked or expired application account cannot connect until it is changed outside the copilot.",
    ),
    CatalogQuestion(
        "Q39", "get_expiring_accounts", "Jobs", "Expiring accounts",
        "Which monitored user accounts will expire within 30 days?",
        reading="security", include=("expires ",),
        empty="No non-OPEN account with an expiry date was returned.",
        limitation="This reading lists accounts that are not OPEN. An OPEN account that expires within 30 days is not included.",
        next_step="Read the expiry date on each listed account. The copilot does not change the profile.",
    ),
    CatalogQuestion(
        "Q40", "get_compliance_results", "Jobs", "Compliance",
        "Which compliance checks are failing for this database?",
        repository=("get_compliance",),
        include=("fail", "compliance", "policy", "pass"),
        empty="No compliance result was returned for this database.",
        limitation="A failed check is a Sentinel policy result. It is not a legal finding.",
        next_step="Open the failing check and its evidence before treating it as an incident.",
    ),
    CatalogQuestion(
        "Q41", "get_configuration_snapshot", "Capacity", "Configuration",
        "What are the selected database's relevant configuration settings?",
        reading="memory_config", include=("parameter ", "limit "),
        empty="No allowlisted parameter was returned.",
        limitation="Only the allowlisted parameters in this reading are shown. Inherited instance settings are not labeled separately.",
        next_step="Use the parameter name and value as the current setting visible to the monitoring user.",
        scope="INSTANCE_CONTEXT",
    ),
    _gap(
        "Q42", "get_configuration_changes", "Capacity", "Parameter changes",
        "Did any monitored configuration parameter change recently?",
        "Parameter snapshots are not compared, so a change time is not available.",
        "A current parameter value is not evidence of when it last changed.",
    ),
    CatalogQuestion(
        "Q43", "get_storage_footprint", "Capacity", "Storage footprint",
        "What is the total current storage footprint of the selected database?",
        reading="tablespace", include=("percent used", "temporary "),
        empty="No tablespace size was returned.",
        limitation="The rows are tablespace allocated and used bytes, plus temporary space. They are not a host-disk total.",
        next_step="Add the allocated sizes only if you need one figure, and keep temporary space separate.",
    ),
    _gap(
        "Q44", "get_segment_growth", "Capacity", "Segment growth",
        "Which schemas or segments account for the most storage growth this month?",
        "Segment snapshots are not stored, so monthly segment growth is not calculated.",
        "Use the tablespace growth question for retained tablespace samples.",
    ),
    CatalogQuestion(
        "Q45", "get_capacity_risks", "Capacity", "Capacity risk",
        "Is the database on track to breach any configured local storage threshold in 30 days?",
        reading="growth", include=("fills in", "not sufficient"),
        empty="Retained history is not sufficient to say whether storage will cross a threshold in 30 days.",
        limitation="A risk uses the retained tablespace forecast only. Host free disk and fast recovery area forecasts are not added here.",
        next_step="A forecast inside 30 days is the risk. No forecast means the history is not sufficient.",
    ),
    CatalogQuestion(
        "Q46", "get_prioritized_findings", "Report", "Urgent issues",
        "What are the three most urgent issues affecting this database now?",
        reading="issues", repository=("get_database_health", "get_events"),
        include=("ora-", "above 9", "above 8", "blocked", "failed"),
        empty="No urgent error, space, or blocking finding was returned.",
        limitation="At most three findings are listed. They are current evidence, not a ranked root cause.",
        next_step="Review the listed findings in order.",
    ),
    CatalogQuestion(
        "Q47", "correlate_incident_timeline", "Report", "Before the incident",
        "What changed immediately before the latest detected incident?",
        repository=("get_events",),
        empty="No incident is stored for this database, so nothing is correlated.",
        limitation="Incident times and nearby events are correlation only. They are not a cause.",
        next_step="Compare the incident time with the events. Do not treat a nearby change as the cause.",
    ),
    CatalogQuestion(
        "Q48", "search_incident_history", "Report", "Similar incidents",
        "Has a similar incident occurred before, and what action was recorded?",
        empty="No earlier incident is stored for this database.",
        limitation="Similarity is the same database and severity in the Sentinel incident list. Resolution text is a note, not a command.",
        next_step="Read the recorded note. Do not run it as a change.",
    ),
    CatalogQuestion(
        "Q49", "explain_performance_issue", "Report", "Slowdown evidence",
        "What are the plausible causes of the current slowdown?",
        reading="performance", include=("database cpu", "host cpu", "user sessions", "wait ", "file "),
        empty="No performance evidence was returned.",
        limitation="These lines are cumulative evidence and hypotheses only. They do not confirm the cause of a slowdown.",
        next_step="If database time is mostly waits, start with the largest wait. If it is mostly CPU, start with the top SQL question.",
        scope="INSTANCE_CONTEXT",
    ),
    CatalogQuestion(
        "Q50", "get_weekly_database_summary", "Report", "Weekly summary",
        "Summarize the past 7 days of health, capacity, performance, and incidents.",
        reading="issues", repository=("get_events",),
        include=("status open", "ora-", "above ", "blocked", "incident ", "no startup"),
        empty="A 7-day rollup is not stored. Current readings are listed instead when they matched.",
        limitation="This is current evidence plus stored incidents. It is not a complete 7-day trend, and missing samples are not filled with zeros.",
        next_step="Use the section lines as the current picture, then open a specific question for space, errors, or sessions.",
    ),
)

_BY_PROMPT = {" ".join(item.prompt.lower().split()): item for item in QUESTIONS}


def match_catalog(content: str) -> CatalogQuestion | None:
    folded = " ".join(content.lower().replace("’", "'").replace("'", "'").split())
    exact = _BY_PROMPT.get(folded) or _BY_PROMPT.get(folded.rstrip("?"))
    if exact is not None:
        return exact
    for prompt, question in _BY_PROMPT.items():
        if prompt in folded or prompt.rstrip("?") in folded:
            return question
    return None


def catalog_guided_commands() -> list[dict[str, str]]:
    return [{"group": item.group, "label": item.label, "prompt": item.prompt} for item in QUESTIONS]


def present_catalog(
    question: CatalogQuestion,
    outcomes: list[ToolOutcome],
    *,
    session: Session | None = None,
    target_id: object | None = None,
    expected_interval_seconds: int = 300,
) -> str:
    """Build the operator answer and attach the machine-readable envelope."""
    if any(item.decision == "denied" and item.reason == "database_scope_required" for item in outcomes):
        return "Open Ask copilot on a database. Fleet chat does not read a database."
    _rename(question, outcomes)
    lines = _collected_lines(outcomes)
    status: CatalogStatus | None = None
    if question.question_id == "Q03":
        lines, status = _freshness(session, target_id, expected_interval_seconds)
    if question.question_id in {"Q47", "Q48", "Q50"}:
        lines = [*lines, *_incidents(session, target_id, question.question_id)]
    failure = _failure_status(outcomes)
    if failure is not None and question.forced_status is None:
        status = failure
        lines = [item.summary for item in outcomes if item.summary] or lines
    selected = [line for line in lines if _kept(question, line)]
    if question.question_id == "Q46":
        selected = selected[:3]
    else:
        selected = selected[:12]
    if not selected:
        selected = [question.empty]
    final_status = question.forced_status or status or "OK"
    health = question.forced_health or _health(final_status, selected)
    # The operator sees the label, status, and health. The question id stays in the envelope only.
    summary = f"{question.label}. Status {final_status}. Health {health}."
    warnings = [question.limitation] if question.limitation else []
    envelope = CatalogEnvelope(
        question_id=question.question_id, tool=question.tool, status=final_status, health=health,
        scope=question.scope, summary=summary, lines=selected, warnings=warnings,
    )
    _store(question, outcomes, envelope, selected)
    parts = [summary, "", *selected]
    if question.limitation:
        parts.append("\nLimitations: " + question.limitation)
    if question.next_step:
        parts.append("\nNext steps: " + question.next_step)
    return "\n".join(parts)


def _rename(question: CatalogQuestion, outcomes: list[ToolOutcome]) -> None:
    reading_tool = TOOL_NAMES.get(question.reading or "")
    for outcome in outcomes:
        if outcome.decision == "allowed" and reading_tool and outcome.name == reading_tool:
            outcome.name = question.tool
            return
    if question.reading is None:
        for outcome in outcomes:
            if outcome.decision == "allowed":
                outcome.name = question.tool
                return
        outcomes.append(ToolOutcome(question.tool, "allowed", question.empty, []))


def _collected_lines(outcomes: list[ToolOutcome]) -> list[str]:
    lines: list[str] = []
    for outcome in outcomes:
        if outcome.decision != "allowed":
            continue
        before = len(lines)
        payload = outcome.payload.get("lines")
        if isinstance(payload, list):
            lines.extend(str(line) for line in payload if str(line).strip())
        results = outcome.payload.get("results")
        if isinstance(results, list):
            lines.extend(_policy_line(item) for item in results if isinstance(item, dict))
        events = outcome.payload.get("events")
        if isinstance(events, list):
            lines.extend(_event_line(item) for item in events if isinstance(item, dict))
        if len(lines) == before and outcome.payload and outcome.summary:
            lines.append(outcome.summary)
    return lines


def _policy_line(item: dict[str, object]) -> str:
    return (
        f"Policy {item.get('policy_id') or ''} result {item.get('result') or ''} "
        f"severity {item.get('severity') or ''}"
    ).strip()


def _event_line(item: dict[str, object]) -> str:
    return f"Event {item.get('occurred_at') or ''} {item.get('code') or ''} {item.get('message') or ''}".strip()


def _kept(question: CatalogQuestion, line: str) -> bool:
    if not question.include:
        return True
    folded = line.lower()
    return any(token in folded for token in question.include)


def _failure_status(outcomes: list[ToolOutcome]) -> CatalogStatus | None:
    statuses = [str(item.payload.get("status") or "") for item in outcomes if item.decision == "allowed"]
    if "not_granted" in statuses:
        return "PERMISSION_REQUIRED"
    if any(value in {"unavailable", "no_alias"} for value in statuses):
        return "SOURCE_ERROR"
    return None


def _health(status: CatalogStatus, lines: list[str]) -> CatalogHealth:
    if status == "UNSUPPORTED":
        return "NOT_APPLICABLE"
    if status != "OK":
        return "UNKNOWN"
    folded = " ".join(lines).lower()
    if any(token in folded for token in ("ora-", "blocked ", "above 95", " is critical")):
        return "CRITICAL"
    if any(token in folded for token in ("above 80", "above 90", "failed", "warning", "long ")):
        return "WARNING"
    return "HEALTHY"


def _store(
    question: CatalogQuestion, outcomes: list[ToolOutcome], envelope: CatalogEnvelope, selected: list[str],
) -> None:
    for outcome in outcomes:
        if outcome.name == question.tool and outcome.decision == "allowed":
            outcome.payload = {**outcome.payload, "catalog": envelope.model_dump()}
            outcome.summary = envelope.summary
            outcome.citations = [item for item in outcome.citations if item.detail in selected]
            return


def _freshness(
    session: Session | None, target_id: object | None, expected_interval_seconds: int,
) -> tuple[list[str], CatalogStatus]:
    if session is None or target_id is None:
        return ["No database is selected, so collection freshness cannot be read."], "INSUFFICIENT_DATA"
    rows = session.scalars(select(CollectorCheckpoint).where(CollectorCheckpoint.target_id == target_id)).all()
    times = [row.last_success_at for row in rows if row.last_success_at is not None]
    if not times:
        return ["No successful scheduled collection is recorded for this database."], "INSUFFICIENT_DATA"
    latest = max(times)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    age = int((datetime.now(UTC) - latest).total_seconds())
    overdue = age > expected_interval_seconds * 3
    line = (
        f"Last successful collection {latest.strftime('%Y-%m-%d %H:%M:%S')} UTC, "
        f"{age} seconds ago. Expected interval {expected_interval_seconds} seconds. "
        f"Overdue {'yes' if overdue else 'no'}."
    )
    return [line], "STALE" if overdue else "OK"


def _incidents(session: Session | None, target_id: object | None, question_id: str) -> list[str]:
    if session is None or target_id is None:
        return []
    rows = session.scalars(
        select(Incident).where(Incident.target_id == target_id).order_by(Incident.last_seen.desc()).limit(5),
    ).all()
    if not rows:
        return ["No incident is stored for this database."]
    lines = []
    for row in rows:
        note = (row.root_cause_candidate or row.message or "").strip()
        lines.append(
            f"Incident {row.severity or 'unknown'} {row.state or 'open'} "
            f"first {row.first_seen} last {row.last_seen} {note}".strip(),
        )
    if question_id == "Q48" and len(lines) == 1:
        lines.append("Only one incident is stored, so no earlier similar incident was found.")
    return lines
