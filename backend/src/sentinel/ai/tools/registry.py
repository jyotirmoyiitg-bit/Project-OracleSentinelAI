"""Execute one allowlisted tool. Anything else is denied before it can run."""

import time
from collections.abc import Callable

from sentinel.ai.safety import ALLOWED_TOOLS, PolicyDenied, digest
from sentinel.ai.tools.activity import database_activity
from sentinel.ai.tools.context import ToolContext
from sentinel.ai.tools.health import database_health, recent_events
from sentinel.ai.tools.knowledge import compliance_status, knowledge_search
from sentinel.ai.tools.storage import fra_status, tablespace_status
from sentinel.ai.types import ToolOutcome

Handler = Callable[[ToolContext, dict[str, object]], ToolOutcome]
HANDLERS: dict[str, Handler] = {
    "get_database_health": database_health,
    "get_tablespace_status": tablespace_status,
    "get_fra_status": fra_status,
    "get_events": recent_events,
    "get_compliance": compliance_status,
    "search_knowledge": knowledge_search,
    "list_datafiles": database_activity,
    "get_memory_status": database_activity,
    "get_top_sql": database_activity,
    "get_sessions": database_activity,
    "get_backup_status": database_activity,
    "get_running_jobs": database_activity,
    "get_database_availability": database_activity,
    "get_critical_errors": database_activity,
    "get_recent_ora_errors": database_activity,
    "get_alert_log": database_activity,
    "get_tablespace_pressure": database_activity,
    "get_tablespace_growth": database_activity,
    "get_performance_status": database_activity,
    "get_blocking_sessions": database_activity,
    "get_sql_workload": database_activity,
    "get_memory_configuration": database_activity,
    "get_recovery_status": database_activity,
    "get_data_guard_status": database_activity,
    "list_database_users": database_activity,
    "get_security_posture": database_activity,
    "get_failed_jobs": database_activity,
    "get_current_issues": database_activity,
}


def execute_tool(name: str, arguments: dict[str, object], context: ToolContext) -> ToolOutcome:
    started = time.perf_counter()
    if name not in ALLOWED_TOOLS or name not in HANDLERS:
        return _denied(name, arguments, "tool is not allowlisted", started)
    if _cross_target(arguments, context):
        return _denied(name, arguments, "tool target is outside this chat scope", started)
    try:
        outcome = HANDLERS[name](context, arguments)
    except PolicyDenied as exc:
        return _denied(name, arguments, exc.reason, started)
    except ValueError as exc:
        return _denied(name, arguments, str(exc)[:200], started)
    outcome.duration_ms = _elapsed(started)
    outcome.payload["digest"] = digest(outcome.payload)
    return outcome


def _cross_target(arguments: dict[str, object], context: ToolContext) -> bool:
    requested = arguments.get("database_id")
    if not isinstance(requested, str) or not requested or context.target_id is None:
        return False
    return requested != str(context.target_id)


def _denied(name: str, arguments: dict[str, object], reason: str, started: float) -> ToolOutcome:
    del arguments
    return ToolOutcome(name, "denied", reason, [], {"digest": ""}, reason, _elapsed(started))


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
