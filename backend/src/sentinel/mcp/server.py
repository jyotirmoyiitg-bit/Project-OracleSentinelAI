"""Stdio MCP server for fixed Oracle readings. It does not accept SQL."""

import asyncio
import json
import logging
import os
import re
import sys
from typing import Any

from sentinel.adapters.oracle.activity import (
    ActivityAccessError,
    ActivityUnavailable,
    read_rows,
)
from sentinel.ai.activity import SYNTHETIC, TOOL_NAMES

logger = logging.getLogger(__name__)

_ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_$#.-]{0,127}$")
_KIND_BY_TOOL = {tool: kind for kind, tool in TOOL_NAMES.items()}
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"alias": {"type": "string", "description": "TNS alias of the selected database"}},
    "required": ["alias"],
    "additionalProperties": False,
}
_DESCRIPTIONS = {
    "get_database_availability": "Live instance, open mode, startup, PDBs, services, and recent startup or shutdown lines.",
    "get_critical_errors": "Critical ORA and fatal alert-log lines from the last 24 hours.",
    "get_recent_ora_errors": "ORA- errors from this database alert log for the last 24 hours.",
    "get_alert_log": "Newest alert log lines from V$DIAG_ALERT_EXT for the last 7 days.",
    "get_tablespace_pressure": "Tablespace percent used, 80/90/95 bands, autoextend headroom, and temporary space.",
    "get_tablespace_growth": "When a tablespace may fill, from retained samples rather than AWR.",
    "get_performance_status": "Startup CPU ratio, active sessions, top waits, and file I/O latency.",
    "get_blocking_sessions": "Blocking sessions, long calls, session counts, limits, and recent deadlocks.",
    "get_sql_workload": "Cursor-cache SQL by elapsed time, executions, reads, and multiple plans.",
    "get_memory_configuration": "Temporary space, hard parses, library cache, parameters, and resource limits.",
    "get_recovery_status": "Last successful and failed backups, fast recovery area, and recent archive logs.",
    "get_data_guard_status": "Database role, archive destinations, and Data Guard lag when a standby exists.",
    "list_database_users": "Database usernames and account status. The API allows this only for an admin operator.",
    "get_security_posture": "Locked or expired accounts, invalid objects, and current privileged role grants.",
    "get_failed_jobs": "Scheduler jobs that failed or stopped in the last 2 days.",
    "get_current_issues": "Current availability, critical errors, space, and blocking evidence.",
    "list_datafiles": "Datafiles and tempfiles.",
    "get_memory_status": "SGA and PGA inside the instance.",
    "get_top_sql": "Top SQL in the cursor cache by CPU.",
    "get_sessions": "User sessions that are logged on.",
    "get_backup_status": "Backups running now and recent RMAN jobs.",
    "get_running_jobs": "Scheduler jobs and DBMS_JOBs running now.",
}


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    while True:
        message = _read_message()
        if message is None:
            return
        response = _dispatch(message)
        if response is not None:
            _write_message(response)


def _dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    if method == "notifications/initialized":
        return None
    request_id = message.get("id")
    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "oracle-sentinel-readings", "version": "1"},
        })
    if method == "tools/list":
        return _result(request_id, {"tools": _tools()})
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict):
            return _error(request_id, "Tool arguments are required.")
        name = params.get("name")
        arguments = params.get("arguments")
        try:
            payload = run_tool(name if isinstance(name, str) else "", arguments if isinstance(arguments, dict) else {})
        except ValueError as exc:
            return _result(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        return _result(request_id, {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False})
    return _error(request_id, "Method not found.")


def run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if set(arguments) - {"alias"}:
        raise ValueError("Only a database alias is accepted.")
    alias = arguments.get("alias")
    if not isinstance(alias, str) or _ALIAS.fullmatch(alias) is None:
        raise ValueError("A database alias is required.")
    kind = _KIND_BY_TOOL.get(name)
    if kind is None:
        raise ValueError("This reading is not available.")
    if os.environ.get("SENTINEL_MCP_SYNTHETIC") == "1":
        return {"status": "ok", "rows": [dict(row) for row in SYNTHETIC[kind]], "note": ""}
    from sentinel.config.secrets import init_resolver
    from sentinel.config.settings import get_settings

    settings = get_settings()
    init_resolver(settings.secret_key)
    if kind == "growth":
        from sentinel.mcp.history import growth_rows

        rows, note = growth_rows(settings, alias)
        return {"status": "ok", "rows": rows, "note": note}
    try:
        rows, note = asyncio.run(read_rows(settings, alias, kind))
    except ActivityAccessError as exc:
        return {
            "status": "not_granted",
            "rows": [],
            "note": f"Grant SELECT on {exc.object_name} to the monitoring account, then ask again.",
        }
    except ActivityUnavailable as exc:
        text = "The database could not be read just now."
        if exc.code is not None:
            text = f"{text} Oracle returned error {exc.code}."
        return {"status": "unavailable", "rows": [], "note": text}
    if kind == "availability":
        from sentinel.mcp.history import portal_row

        portal = portal_row(settings, alias)
        if portal is not None:
            rows.append(portal)
    return {"status": "ok", "rows": rows[:80], "note": note}


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": _DESCRIPTIONS.get(name, "Fixed read-only database reading."),
            "inputSchema": _SCHEMA,
        }
        for name in TOOL_NAMES.values()
    ]


def _result(request_id: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": message}}


def _read_message() -> dict[str, Any] | None:
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = sys.stdin.buffer.read(1)
        if not chunk:
            return None
        header += chunk
        if len(header) > 65536:
            return None
    match = re.search(br"Content-Length:\s*(\d+)", header, re.IGNORECASE)
    if match is None:
        return None
    length = int(match.group(1))
    if length < 1 or length > 1_000_000:
        return None
    body = sys.stdin.buffer.read(length)
    payload = json.loads(body)
    if not isinstance(payload, dict):
        return None
    return payload


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()
