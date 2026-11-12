"""Redaction, untrusted-text handling, and the tool allowlist."""

import hashlib
import json
import re
from typing import Any

FORBIDDEN_TOOLS = frozenset({"execute_sql", "run_sql", "shell", "subprocess", "http_request", "send_to_hosted"})
ALLOWED_TOOLS = frozenset({
    "get_database_health",
    "get_tablespace_status",
    "get_fra_status",
    "get_events",
    "get_compliance",
    "search_knowledge",
    "list_datafiles",
    "get_memory_status",
    "get_top_sql",
    "get_sessions",
    "get_backup_status",
    "get_running_jobs",
    "get_database_availability",
    "get_critical_errors",
    "get_alert_log",
    "get_tablespace_pressure",
    "get_tablespace_growth",
    "get_performance_status",
    "get_blocking_sessions",
    "get_sql_workload",
    "get_memory_configuration",
    "get_recovery_status",
    "get_data_guard_status",
    "list_database_users",
    "get_security_posture",
    "get_failed_jobs",
    "get_current_issues",
})
_FORBIDDEN = re.compile(r"\b(execute_sql|run_sql|shell|subprocess|http_request|send_to_hosted)\b", re.IGNORECASE)
_SECRET = re.compile(
    r"(gAAAAA[A-Za-z0-9_\-]{10,}=*)|(sk-[A-Za-z0-9]{8,})|((?i:password|api[_-]?key)\s*[:=]\s*\S+)",
)
_TABLESPACE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,29}$")
_SQL_SELECT = re.compile(r"(?is)\bselect\b[\s\S]{0,300}\bfrom\b")
_SQL_SHAPE = re.compile(
    r"(?is)\b(insert\s+into|delete\s+from|merge\s+into|truncate\s+table|"
    r"grant\s+[a-z_]+|revoke\s+[a-z_]+|"
    r"(?:alter|drop|create)\s+(?:table|database|system|session|user|index|tablespace|view))\b",
)
_SQL_LEADING = re.compile(
    r"(?is)^(select|with|insert|update|delete|merge|alter|drop|create|truncate|grant|revoke|commit|rollback)\b",
)
SQL_PROMPT_REFUSAL = (
    "SQL cannot be entered in the prompt. Ask for the reading in plain language, "
    "such as who is logged on or list the datafiles."
)
_EVENT_CODE = re.compile(r"^[A-Z]{1,8}-\d{3,6}$")
_SEVERITY = frozenset({"info", "warning", "critical", "error"})


class PolicyDenied(Exception):
    def __init__(self, tool_name: str, reason: str) -> None:
        super().__init__(reason)
        self.tool_name = tool_name
        self.reason = reason


def sql_in_prompt(value: str) -> bool:
    """Reject a statement typed by the operator. Plain-language questions stay allowed."""
    text = value.strip()
    if _SQL_SELECT.search(text) or _SQL_SHAPE.search(text):
        return True
    leading = _SQL_LEADING.match(text)
    if leading is None:
        return False
    verb = leading.group(1).lower()
    if verb in {"select", "with"}:
        return re.search(r"(?is)\b(from|where|join)\b", text) is not None
    return True


def forbidden_names(value: str) -> list[str]:
    found: list[str] = []
    for match in _FORBIDDEN.finditer(value):
        name = match.group(1).lower()
        if name not in found:
            found.append(name)
    return found


def redact(value: str) -> str:
    return _SECRET.sub("[redacted]", value)


def sanitize_untrusted(value: str, limit: int = 500) -> str:
    """Treat log lines and document text as data, and drop characters that hide instructions."""
    cleaned = "".join(character for character in value if character in "\n\t" or 32 <= ord(character) < 127 or ord(character) > 159)
    return redact(cleaned).strip()[:limit]


def neutralize(value: str) -> str:
    return _FORBIDDEN.sub("[blocked]", sanitize_untrusted(value, limit=4000))


def tablespace_name(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    candidate = value.strip()
    if not _TABLESPACE.fullmatch(candidate):
        raise PolicyDenied("get_tablespace_status", "tablespace name is not a safe identifier")
    return candidate


def event_code(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    candidate = value.strip().upper()
    if not _EVENT_CODE.fullmatch(candidate):
        raise PolicyDenied("get_events", "event code is not a safe identifier")
    return candidate


def event_severity(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    candidate = value.strip().lower()
    if candidate not in _SEVERITY:
        raise PolicyDenied("get_events", "event severity is not recognized")
    return candidate


def digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def bounded(payload: dict[str, Any], limit: int = 4000) -> dict[str, Any]:
    encoded = json.dumps(payload, default=str)
    if len(encoded) <= limit:
        return payload
    return {"truncated": True, "preview": sanitize_untrusted(encoded, limit)}
