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
    shared_reader,
)
from sentinel.ai.activity import SYNTHETIC, TOOL_DESCRIPTIONS, TOOL_NAMES

logger = logging.getLogger(__name__)

_ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_$#.-]{0,127}$")
_KIND_BY_TOOL = {tool: kind for kind, tool in TOOL_NAMES.items()}
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"alias": {"type": "string", "description": "TNS alias of the selected database"}},
    "required": ["alias"],
    "additionalProperties": False,
}
_DESCRIPTIONS = TOOL_DESCRIPTIONS
SNAPSHOT_TOOL = "collect_snapshot"
_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "alias": {"type": "string", "description": "TNS alias of the selected database"},
        "readings": {
            "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 30,
            "description": "Names of fixed readings from this server's tool list",
        },
    },
    "required": ["alias", "readings"],
    "additionalProperties": False,
}
# Set when a client used the older Content-Length framing, so the reply uses the same framing.
_legacy_framing = False


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


def run_snapshot(arguments: dict[str, Any]) -> dict[str, Any]:
    """Run several fixed readings for one database on one Oracle logon. It does not accept SQL."""
    if set(arguments) - {"alias", "readings"}:
        raise ValueError("Only a database alias and a list of reading names are accepted.")
    alias = arguments.get("alias")
    if not isinstance(alias, str) or _ALIAS.fullmatch(alias) is None:
        raise ValueError("A database alias is required.")
    names = arguments.get("readings")
    if not isinstance(names, list) or not names or len(names) > 30:
        raise ValueError("Between 1 and 30 reading names are required.")
    if any(not isinstance(name, str) or name not in _KIND_BY_TOOL for name in names):
        raise ValueError("A reading in the list is not available.")
    names = list(dict.fromkeys(names))
    if os.environ.get("SENTINEL_MCP_SYNTHETIC") == "1":
        return {"status": "ok", "readings": {
            name: {"status": "ok", "rows": [dict(row) for row in SYNTHETIC[_KIND_BY_TOOL[name]]], "note": ""}
            for name in names
        }}
    from sentinel.config.secrets import init_resolver
    from sentinel.config.settings import get_settings

    settings = get_settings()
    init_resolver(settings.secret_key)
    return {"status": "ok", "readings": asyncio.run(_read_many(settings, alias, names))}


async def _read_many(settings: Any, alias: str, names: list[str]) -> dict[str, dict[str, Any]]:
    readings: dict[str, dict[str, Any]] = {}
    async with shared_reader(settings, alias) as reader:
        for name in names:
            kind = _KIND_BY_TOOL[name]
            if kind == "growth":
                from sentinel.mcp.history import growth_rows

                rows, note = growth_rows(settings, alias)
                readings[name] = {"status": "ok", "rows": rows, "note": note}
                continue
            try:
                rows, note = await reader(settings, alias, kind)
            except ActivityAccessError as exc:
                readings[name] = {
                    "status": "not_granted", "rows": [],
                    "note": f"Grant SELECT on {exc.object_name} to the monitoring account, then ask again.",
                }
                continue
            except ActivityUnavailable as exc:
                text = "The database could not be read just now."
                if exc.code is not None:
                    text = f"{text} Oracle returned error {exc.code}."
                readings[name] = {"status": "unavailable", "rows": [], "note": text}
                continue
            if kind == "availability":
                from sentinel.mcp.history import portal_row

                portal = portal_row(settings, alias)
                if portal is not None:
                    rows.append(portal)
            readings[name] = {"status": "ok", "rows": rows[:80], "note": note}
    return readings


def run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == SNAPSHOT_TOOL:
        return run_snapshot(arguments)
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
    ] + [{
        "name": SNAPSHOT_TOOL,
        "description": "Run several of the fixed readings for one database on one logon and return each result.",
        "inputSchema": _SNAPSHOT_SCHEMA,
    }]


def _result(request_id: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": message}}


def _read_message() -> dict[str, Any] | None:
    """Read one JSON-RPC message.

    The MCP stdio transport sends one JSON object per line. A client that still sends a
    Content-Length header is accepted and answered the same way.
    """
    global _legacy_framing
    line = sys.stdin.buffer.readline()
    while line and not line.strip():
        line = sys.stdin.buffer.readline()
    if not line:
        return None
    if line.lower().startswith(b"content-length:"):
        _legacy_framing = True
        try:
            length = int(line.split(b":", 1)[1].strip())
        except ValueError:
            return None
        while True:
            header = sys.stdin.buffer.readline()
            if not header or not header.strip():
                break
        if length < 1 or length > 1_000_000:
            return None
        body = sys.stdin.buffer.read(length)
    else:
        _legacy_framing = False
        if len(line) > 1_000_000:
            return None
        body = line
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode()
    if _legacy_framing:
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    else:
        sys.stdout.buffer.write(body + b"\n")
    sys.stdout.buffer.flush()
