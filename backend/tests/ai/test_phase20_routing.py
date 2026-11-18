"""Phase 20: wider question routing, new readings, fleet status, and model-assisted routing."""

import asyncio
import io
import json
import sys

import pytest

from sentinel.ai.activity import (
    ADMIN_ONLY_READINGS,
    SYNTHETIC,
    TOOL_DESCRIPTIONS,
    TOOL_NAMES,
    ActivityReading,
    match_activity,
    reading_outcome,
)
from sentinel.ai.gateway import ChatGateway
from sentinel.ai.orchestrator import LISTING_DENIAL, Orchestrator, _claims, _plan
from sentinel.ai.safety import ALLOWED_TOOLS
from sentinel.ai.tools.registry import HANDLERS
from sentinel.ai.types import ModelReply
from sentinel.knowledge.acl import Caller
from sentinel.persistence.models import ChatThread, Incident


@pytest.mark.parametrize(("question", "kind"), [
    ("Where are the redo log files?", "redo_logs"),
    ("How many redo log groups are there and what size?", "redo_logs"),
    ("List the control files", "control_files"),
    ("What are the biggest segments?", "segments"),
    ("Which tables use the most space?", "segments"),
    ("List the background processes", "background"),
    ("Are there any locks?", "blocking"),
    ("Who is blocking whom?", "blocking"),
    ("What is the database waiting on?", "performance"),
    ("Which query is using the most CPU?", "top_sql"),
    ("How many users are connected?", "sessions"),
    ("What application programs are connected?", "sessions"),
    ("Which executables are running against the database?", "sessions"),
    ("How much disk space is the database using?", "tablespace"),
    ("List all database users", "database_users"),
    ("Which database users are locked?", "security"),
    ("Are any scheduler jobs failing?", "failed_jobs"),
    ("Is everything ok with this db?", "issues"),
    ("Show me the datafiles", "datafiles"),
    ("Show me the alert log", "alert_log"),
    ("Which SQL IDs are consuming the most CPU or elapsed time?", "sql_workload"),
    ("Are any sessions currently blocking other sessions?", "blocking"),
    ("Which application accounts are locked or expired?", "security"),
])
def test_operator_phrasings_reach_a_reading(question: str, kind: str) -> None:
    assert match_activity(question) == kind


def test_every_reading_is_allowlisted_described_and_presentable() -> None:
    for kind, tool in TOOL_NAMES.items():
        assert tool in ALLOWED_TOOLS
        assert tool in HANDLERS
        assert TOOL_DESCRIPTIONS.get(tool)
        outcome = reading_outcome(ActivityReading(kind, tool, "ok", [dict(row) for row in SYNTHETIC[kind]]), "db")
        assert outcome.decision == "allowed"
        assert outcome.payload["lines"]
    assert "get_fleet_health" in ALLOWED_TOOLS
    assert {"database_users", "control_files", "segments"} == ADMIN_ONLY_READINGS


def test_new_readings_read_in_plain_lines() -> None:
    redo = reading_outcome(ActivityReading("redo_logs", "get_redo_logs", "ok", [dict(row) for row in SYNTHETIC["redo_logs"]]), "db")
    assert redo.payload["lines"][0] == "Redo log groups 2, total size 400.0 MB."
    assert any("status CURRENT" in line and "redo01.log" in line for line in redo.payload["lines"])
    segments = reading_outcome(ActivityReading("segments", "get_largest_segments", "ok", [dict(row) for row in SYNTHETIC["segments"]]), "db")
    assert any(line.startswith("APPUSER.ORDERS TABLE in USERS") for line in segments.payload["lines"])
    control = reading_outcome(ActivityReading("control_files", "list_control_files", "ok", [dict(row) for row in SYNTHETIC["control_files"]]), "db")
    assert len(control.payload["lines"]) == 2
    background = reading_outcome(ActivityReading("background", "get_background_processes", "ok", [dict(row) for row in SYNTHETIC["background"]]), "db")
    assert any("PMON" in line for line in background.payload["lines"])


def test_claims_separate_explanations_changes_and_drafts() -> None:
    assert "concept" in _claims("What does ORA-01555 mean?")
    assert "live" not in _claims("What does ORA-01555 mean?")
    assert "change" in _claims("Please add a datafile to USERS")
    assert "change" in _claims("Kill session 123")
    assert "change" in _claims("restart the listener")
    assert "change" not in _claims("List the datafiles")
    assert "sql" in _claims("How do I check tablespace usage with SQL?")


def test_fleet_questions_plan_the_stored_fleet_status() -> None:
    assert _plan("question", "Which databases are down?", False, None) == [("get_fleet_health", {})]
    assert _plan("health", "Which database has the fullest tablespace across the fleet?", False, "tablespace") == [("get_fleet_health", {})]
    assert _plan("question", "Which databases are down?", True, None) != [("get_fleet_health", {})]


class _Provider:
    """A stand-in model. It nominates readings when asked to route, and assesses otherwise."""

    name = "local"
    model = "stub"

    def __init__(self, nominate: list[str]) -> None:
        self.nominate = nominate
        self.schemas: list[dict[str, object]] = []

    async def generate(self, messages, *, tools, structured_schema, correlation_id):
        del messages, correlation_id
        assert tools is None
        self.schemas.append(structured_schema)
        if "readings" in structured_schema.get("properties", {}):
            return ModelReply(json.dumps({"readings": self.nominate}), 1, 1, self.name, self.model)
        return ModelReply(
            json.dumps({"assessment": "The reading is listed below.", "confidence": "medium"}),
            1, 1, self.name, self.model,
        )

    async def health(self) -> dict[str, object]:
        return {"status": "ok"}


def _gateway(settings, provider: _Provider) -> ChatGateway:
    del settings
    return ChatGateway(provider, "disclosure", True)


def _thread(sessions, scope_type: str, scope_id: str | None, principal: str, role: str) -> str:
    with sessions.begin() as session:
        thread = ChatThread(principal=principal, role=role, scope_type=scope_type, scope_id=scope_id, provider="local", model="stub")
        session.add(thread)
        session.flush()
        return str(thread.id)


def _ask(sessions, settings, provider: _Provider, thread_id: str, caller: Caller, question: str):
    import uuid

    orchestrator = Orchestrator(settings.model_copy(update={"ai_enabled": True, "ai_provider": "local"}), _gateway(settings, provider))
    with sessions.begin() as session:
        thread = session.get(ChatThread, uuid.UUID(thread_id))
        return asyncio.run(orchestrator.answer(session, thread, caller, question))


def test_model_nominates_only_allowlisted_readings_the_caller_may_read(settings, sessions, make_target) -> None:
    target_id = str(make_target(alias="PDBORCL"))
    operator = Caller("operator", "olga")
    provider = _Provider(["get_memory_status", "execute_sql", "list_control_files", "get_background_processes", "made_up"])
    thread_id = _thread(sessions, "database", target_id, "olga", "operator")
    answer = _ask(sessions, settings, provider, thread_id, operator, "Is the buffer cache hit ratio ok?")
    tools = {item["tool"]: item["decision"] for item in answer.tools}
    assert tools["route_question"] == "allowed"
    assert tools["get_memory_status"] == "allowed"
    assert tools["get_background_processes"] == "allowed"
    assert "list_control_files" not in tools
    assert "execute_sql" not in tools
    assert "made_up" not in tools
    assert answer.llm_used is True
    assert len(provider.schemas) == 2


def test_fixed_route_does_not_ask_the_model_to_route(settings, sessions, make_target) -> None:
    target_id = str(make_target(alias="PDBORCL"))
    provider = _Provider(["get_top_sql"])
    thread_id = _thread(sessions, "database", target_id, "olga", "operator")
    fixed = settings.model_copy(update={"ai_supervisor_enabled": False})
    answer = _ask(sessions, fixed, provider, thread_id, Caller("operator", "olga"), "Where are the redo log files?")
    tools = [item["tool"] for item in answer.tools]
    assert "route_question" not in tools
    assert "get_redo_logs" in tools
    assert "get_top_sql" not in tools
    assert "redo01.log" not in answer.content
    assert "Member file paths are shown to an admin operator." in json.dumps(answer.tools)
    assert all("readings" not in schema.get("properties", {}) for schema in provider.schemas)


def test_router_fallback_can_be_switched_off(settings, sessions, make_target) -> None:
    target_id = str(make_target(alias="PDBORCL"))
    provider = _Provider(["get_memory_status"])
    thread_id = _thread(sessions, "database", target_id, "olga", "operator")
    off = settings.model_copy(update={"ai_router_fallback": False, "ai_supervisor_enabled": False})
    answer = _ask(sessions, off, provider, thread_id, Caller("operator", "olga"), "Is the buffer cache hit ratio ok?")
    assert "route_question" not in [item["tool"] for item in answer.tools]
    assert all("readings" not in schema.get("properties", {}) for schema in provider.schemas)


def test_listing_readings_are_admin_only(settings, sessions, make_target) -> None:
    target_id = str(make_target(alias="PDBORCL"))
    provider = _Provider([])
    operator_thread = _thread(sessions, "database", target_id, "olga", "operator")
    denied = _ask(sessions, settings, provider, operator_thread, Caller("operator", "olga"), "List the control files")
    assert denied.content == LISTING_DENIAL
    assert denied.provider_status == "denied"
    admin_thread = _thread(sessions, "database", target_id, "sentinel", "admin")
    allowed = _ask(sessions, settings, provider, admin_thread, Caller("admin", "sentinel"), "What are the biggest segments?")
    assert "APPUSER.ORDERS" in json.dumps(allowed.tools) + allowed.content
    redo = _ask(sessions, settings, provider, admin_thread, Caller("admin", "sentinel"), "Where are the redo log files?")
    assert "redo01.log" in json.dumps(redo.tools) + redo.content


def test_fleet_chat_answers_from_stored_status(settings, sessions, make_target) -> None:
    down = make_target(alias="FINANCE", last_health_status="offline")
    make_target(alias="WAREHOUSE", last_health_status="healthy")
    with sessions.begin() as session:
        session.add(Incident(fingerprint="f", target_id=down, rule_id="r", state="new", severity="critical", message="m"))
    provider = _Provider([])
    thread_id = _thread(sessions, "fleet", None, "olga", "operator")
    answer = _ask(sessions, settings, provider, thread_id, Caller("operator", "olga"), "Which databases are down?")
    fleet = next(item for item in answer.tools if item["tool"] == "get_fleet_health")
    assert fleet["decision"] == "allowed"
    assert "2 enabled databases: 1 offline, 1 healthy" in fleet["summary"]
    details = [item["detail"] for item in answer.citations]
    assert details[0].startswith("FINANCE (FINANCE, test) health offline")
    assert "open incidents 1" in details[0]
    assert "route_question" not in [item["tool"] for item in answer.tools]


def test_mcp_server_speaks_one_json_object_per_line(monkeypatch) -> None:
    from sentinel.mcp import server

    monkeypatch.setenv("SENTINEL_MCP_SYNTHETIC", "1")
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_redo_logs", "arguments": {"alias": "PDBORCL"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "get_redo_logs", "arguments": {"alias": "PDBORCL", "sql": "select 1"}}},
    ]

    class _Pipe:
        def __init__(self, data: bytes) -> None:
            self.buffer = io.BytesIO(data)

    stdin = _Pipe(b"".join(json.dumps(item).encode() + b"\n" for item in requests))
    stdout = _Pipe(b"")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    server.main()
    lines = [json.loads(line) for line in stdout.buffer.getvalue().splitlines() if line.strip()]
    assert [item["id"] for item in lines] == [1, 2, 3, 4]
    names = {tool["name"] for tool in lines[1]["result"]["tools"]}
    assert names == set(TOOL_NAMES.values()) | {"collect_snapshot"}
    reading = json.loads(lines[2]["result"]["content"][0]["text"])
    assert reading["status"] == "ok"
    assert len(reading["rows"]) == 4
    assert lines[3]["result"]["isError"] is True


def test_mcp_server_still_answers_content_length_framing(monkeypatch) -> None:
    from sentinel.mcp import server

    monkeypatch.setenv("SENTINEL_MCP_SYNTHETIC", "1")
    body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}).encode()

    class _Pipe:
        def __init__(self, data: bytes) -> None:
            self.buffer = io.BytesIO(data)

    stdin = _Pipe(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    stdout = _Pipe(b"")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    server.main()
    raw = stdout.buffer.getvalue()
    assert raw.startswith(b"Content-Length: ")
    assert json.loads(raw.split(b"\r\n\r\n", 1)[1])["id"] == 7
