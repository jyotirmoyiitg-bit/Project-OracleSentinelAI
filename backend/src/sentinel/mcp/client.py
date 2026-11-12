"""Ask the readings MCP server for one fixed tool."""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from sentinel.config.settings import Settings

_SRC = Path(__file__).resolve().parents[2]
_BACKEND = _SRC.parent


async def ask_reading(settings: Settings, tool_name: str, alias: str) -> tuple[str, list[dict[str, str]], str]:
    env = os.environ.copy()
    env["SENTINEL_MCP_SYNTHETIC"] = "1" if settings.synthetic_mode else "0"
    env["PYTHONPATH"] = str(_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "sentinel.mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(_BACKEND),
        env=env,
    )
    try:
        return await asyncio.wait_for(_exchange(process, tool_name, alias), timeout=90)
    except TimeoutError:
        return "unavailable", [], "The readings server did not answer in time."
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def _exchange(
    process: asyncio.subprocess.Process, tool_name: str, alias: str,
) -> tuple[str, list[dict[str, str]], str]:
    if process.stdin is None or process.stdout is None:
        return "unavailable", [], "The readings server could not be started."
    await _send(process, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "oracle-sentinel", "version": "1"},
        },
    })
    await _read(process.stdout)
    await _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    await _send(process, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": tool_name, "arguments": {"alias": alias}},
    })
    while True:
        message = await _read(process.stdout)
        if message.get("id") == 2:
            return _payload(message)


async def _send(process: asyncio.subprocess.Process, payload: dict[str, Any]) -> None:
    if process.stdin is None:
        return
    body = json.dumps(payload).encode()
    process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    await process.stdin.drain()


async def _read(stdout: asyncio.StreamReader) -> dict[str, Any]:
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = await stdout.read(1)
        if not chunk:
            raise ValueError("readings server closed")
        header += chunk
        if len(header) > 65536:
            raise ValueError("readings header is too large")
    marker = header.lower().find(b"content-length:")
    if marker < 0:
        raise ValueError("readings response has no length")
    line = header[marker:].split(b"\r\n", 1)[0]
    length = int(line.split(b":", 1)[1].strip())
    if length < 1 or length > 1_000_000:
        raise ValueError("readings response length is invalid")
    body = await stdout.readexactly(length)
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise TypeError("readings response is not an object")
    return payload


def _payload(message: dict[str, Any]) -> tuple[str, list[dict[str, str]], str]:
    result = message.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        return "unavailable", [], "The readings server rejected the request."
    content = result.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        return "unavailable", [], "The readings server returned no reading."
    text = content[0].get("text")
    if not isinstance(text, str):
        return "unavailable", [], "The readings server returned no reading."
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return "unavailable", [], "The readings server returned an unreadable reading."
    if not isinstance(parsed, dict):
        return "unavailable", [], "The readings server returned an unreadable reading."
    rows = parsed.get("rows")
    clean = [
        {str(key): str(value) for key, value in row.items()}
        for row in rows
        if isinstance(row, dict)
    ] if isinstance(rows, list) else []
    status = parsed.get("status")
    note = parsed.get("note")
    return (
        status if isinstance(status, str) else "unavailable",
        clean,
        note if isinstance(note, str) else "",
    )
