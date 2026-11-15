"""Phase 21: fleet chat answers fleet questions, and each chat scope has its own guided questions."""

import asyncio
import importlib
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from sentinel.ai import fleet as fleet_module
from sentinel.ai.activity import ActivityReading
from sentinel.ai.fleet import clean_programs, fleet_requests, nominated_request
from sentinel.ai.gateway import ChatGateway, build_gateway
from sentinel.ai.orchestrator import FLEET_COMMANDS, Orchestrator, guided_commands_for
from sentinel.ai.types import ModelReply
from sentinel.config.secrets import SecretResolver
from sentinel.knowledge.acl import Caller
from sentinel.persistence.models import ChatThread, CurrentObservation, Incident, PolicyResult

OPERATOR = Caller("operator", "olga")


@pytest.mark.parametrize(("question", "tool", "arguments"), [
    ("which database in the fleet has reported an incident ?", "get_fleet_incidents", {}),
    (
        ("which database in the fleet has an active running process with process or executable name "
        "'x_run.exe' or 'lcotp.exe' or java.exe"),
        "find_fleet_programs", {"programs": ["x_run.exe", "lcotp.exe", "java.exe"]},
    ),
    ("which database is having a tablespace with more than 10gb data file ?", "find_fleet_datafiles", {"threshold_gb": 10.0}),
    ("Which databases have a datafile larger than 500 MB?", "find_fleet_datafiles", {"threshold_gb": 500 / 1024}),
    ("which database is running in violation of organizational policy ?", "get_fleet_compliance", {}),
    ("Which databases are non-compliant?", "get_fleet_compliance", {}),
    ("Which databases have a tablespace above 90 percent used?", "get_fleet_tablespaces", {"threshold_pct": 90.0}),
    ("Which databases are down?", "get_fleet_health", {}),
    ("Give me a status summary of every database in the fleet.", "get_fleet_health", {}),
    ("Which programs are connected to each database in the fleet?", "find_fleet_programs", {"programs": []}),
    ("Summarize open incidents", "get_fleet_incidents", {}),
])
def test_fleet_questions_reach_a_fleet_tool(question: str, tool: str, arguments: dict[str, object]) -> None:
    requests = fleet_requests(question)
    assert requests, question
    assert requests[0][0] == tool
    assert requests[0][1] == arguments


@pytest.mark.parametrize("question", [
    "What is our company policy on archive log mode?",
    "Explain what a checkpoint is",
    "What does ORA-01555 mean?",
    "Search knowledge for ORA-00257",
    "Who is logged on the database?",
])
def test_document_and_explanation_questions_are_not_fleet_tool_questions(question: str) -> None:
    assert fleet_requests(question) == []


def test_program_names_are_cleaned_and_must_come_from_the_question() -> None:
    question = "Which databases run tomcat or x_run.exe?"
    assert clean_programs(["tomcat", "x_run.exe", "invented.exe", "bad;name", 7, "TOMCAT"], question) == ["tomcat", "x_run.exe"]
    assert clean_programs("java.exe") == []
    assert nominated_request({"tool": "execute_sql"}, question) is None
    assert nominated_request({"tool": "find_fleet_programs", "programs": ["tomcat", "ghost.exe"]}, question) == (
        "find_fleet_programs", {"programs": ["tomcat"]},
    )
    assert nominated_request({"tool": "find_fleet_datafiles", "threshold_gb": "many"}, question) == (
        "find_fleet_datafiles", {"threshold_gb": 10},
    )
    assert nominated_request({"tool": "get_fleet_tablespaces", "threshold_pct": 400}, question) == (
        "get_fleet_tablespaces", {"threshold_pct": 100},
    )


def test_each_scope_has_its_own_guided_questions() -> None:
    fleet = guided_commands_for("fleet", "operator")
    assert fleet == [dict(item) for item in FLEET_COMMANDS]
    assert {item["group"] for item in fleet} == {"Health", "Incidents", "Programs", "Storage", "Policy", "Knowledge"}
    fleet_labels = {item["label"] for item in fleet}
    assert {"Policy violations", "Databases with incidents", "Datafiles over 10 GB", "java.exe"} <= fleet_labels
    assert not fleet_labels & {"Datafiles", "Memory", "Alert log", "Top SQL by CPU", "Blocking"}
    for item in fleet:
        if item["group"] != "Knowledge":
            assert fleet_requests(item["prompt"]), item["prompt"]

    operator = guided_commands_for("database", "operator")
    admin = guided_commands_for("database", "admin")
    operator_labels = {item["label"] for item in operator}
    admin_labels = {item["label"] for item in admin}
    assert {"Database users", "Control files", "Largest segments"} <= admin_labels
    assert not operator_labels & {"Database users", "Control files", "Largest segments"}
    assert {"Redo logs", "Background processes", "Datafiles", "FRA status"} <= operator_labels
    # A question that can only answer "not collected" is not offered as a button.
    unsupported = {
        "Session baseline", "SQL regressions", "Load baseline", "Metric changes", "TEMP consumers",
        "Schema storage", "Unusable indexes", "Optimizer statistics", "Parameter changes", "Segment growth",
    }
    assert not admin_labels & unsupported
    assert not admin_labels & {"Policy violations", "Fleet status", "Databases with incidents"}
    assert guided_commands_for("incident", "operator") == operator
    prompts = [item["prompt"].lower().rstrip("?.") for item in admin]
    assert len(prompts) == len(set(prompts))


def _thread(sessions) -> str:
    with sessions.begin() as session:
        thread = ChatThread(principal="olga", role="operator", scope_type="fleet", scope_id=None, provider="disabled", model="")
        session.add(thread)
        session.flush()
        return str(thread.id)


def _ask(sessions, settings, gateway: ChatGateway, question: str, caller: Caller = OPERATOR):
    thread_id = _thread(sessions)
    orchestrator = Orchestrator(settings, gateway)
    with sessions.begin() as session:
        thread = session.get(ChatThread, uuid.UUID(thread_id))
        return asyncio.run(orchestrator.answer(session, thread, caller, question))


def _disabled(settings) -> ChatGateway:
    return build_gateway(settings, SecretResolver(settings.secret_key))


def _tool(answer, name: str) -> dict[str, object]:
    return next(item for item in answer.tools if item["tool"] == name)


def test_incident_question_lists_databases_from_stored_incidents(settings, sessions, make_target) -> None:
    finance = make_target(alias="FINANCE")
    make_target(alias="WAREHOUSE")
    reporting = make_target(alias="REPORTING")
    make_target(alias="RETIRED", enabled=False)
    with sessions.begin() as session:
        session.add(Incident(fingerprint="a", target_id=finance, rule_id="r1", state="new", severity="critical", message="ORA-00257 archiver stuck"))
        session.add(Incident(fingerprint="b", target_id=finance, rule_id="r2", state="acknowledged", severity="warning", message="Tablespace USERS above 90 percent"))
        session.add(Incident(fingerprint="c", target_id=reporting, rule_id="r3", state="resolved", severity="warning", message="Listener restarted"))
    answer = _ask(sessions, settings, _disabled(settings), "which database in the fleet has reported an incident ?")
    assert _tool(answer, "get_fleet_incidents")["decision"] == "allowed"
    assert answer.llm_used is False
    assert answer.provider_status == "answered"
    assert answer.structured["kind"] == "fleet"
    assert answer.structured["summary"].startswith("1 of 3 enabled databases have an open incident. 2 reported an incident in the last 7 days.")
    reading = answer.structured["reading"]
    assert reading[0].startswith("FINANCE: 2 open incidents (1 critical, 1 warning). Latest")
    assert reading[1].startswith("REPORTING: no open incident, 1 resolved in the last 7 days")
    assert reading[2] == "No incident in the last 7 days: WAREHOUSE."
    assert "RETIRED" not in answer.content
    assert "Fleet reading:" in answer.content
    assert {item["label"] for item in answer.citations} == {"FINANCE", "REPORTING"}
    assert {item["group"] for item in answer.guided_commands} >= {"Incidents", "Programs", "Policy"}


def test_policy_question_lists_databases_that_fail_a_policy(settings, sessions, make_target) -> None:
    finance = make_target(alias="FINANCE")
    warehouse = make_target(alias="WAREHOUSE")
    make_target(alias="REPORTING")
    now = datetime.now(UTC).replace(tzinfo=None)
    with sessions.begin() as session:
        for target, policy, result, severity in (
            (finance, "ARCHIVELOG_REQUIRED", "fail", "high"),
            (finance, "OMF_REQUIRED", "fail", "medium"),
            (finance, "FRA_CONFIGURED", "pass", "medium"),
            (warehouse, "ARCHIVELOG_REQUIRED", "pass", "high"),
        ):
            session.add(PolicyResult(
                target_id=target, policy_id=policy, policy_version="1", observed_at=now, result=result,
                severity=severity, evidence={},
            ))
    answer = _ask(sessions, settings, _disabled(settings), "which database is running in violation of organizational policy ?")
    assert _tool(answer, "get_fleet_compliance")["decision"] == "allowed"
    assert not any(item["tool"] == "search_knowledge" for item in answer.tools)
    assert answer.structured["summary"].startswith("1 of 3 enabled databases fail at least one compliance policy.")
    assert answer.structured["reading"] == [
        "FINANCE: 2 policies failing: ARCHIVELOG_REQUIRED (high), OMF_REQUIRED (medium)",
        "No failing policy: WAREHOUSE.",
        "Not evaluated yet, so not known: REPORTING.",
    ]


def test_tablespace_question_uses_stored_percent_used(settings, sessions, make_target) -> None:
    finance = make_target(alias="FINANCE")
    warehouse = make_target(alias="WAREHOUSE")
    now = datetime.now(UTC).replace(tzinfo=None)
    with sessions.begin() as session:
        for target, name, value in ((finance, "USERS", 93.2), (finance, "SYSTEM", 40.0), (warehouse, "USERS", 50.0)):
            session.add(CurrentObservation(
                target_id=target, metric_name="storage.tablespace.used_pct", label_key=name, observation_id=uuid.uuid4(),
                probe_type="tablespace", timestamp=now, value=value, value_str=None, unit="percent",
                labels={"tablespace_name": name}, quality="measured", source="test",
            ))
    answer = _ask(sessions, settings, _disabled(settings), "Which databases have a tablespace above 90 percent used?")
    assert answer.structured["reading"] == [
        "FINANCE: USERS 93.2 percent used",
        "No tablespace at or above 90 percent: WAREHOUSE.",
    ]


def test_program_question_reads_every_database_and_filters_in_python(settings, sessions, make_target) -> None:
    make_target(alias="FINANCE")
    make_target(alias="WAREHOUSE")
    question = (
        "which database in the fleet has an active running process with process or executable name "
        "'x_run.exe' or 'lcotp.exe' or java.exe"
    )
    answer = _ask(sessions, settings, _disabled(settings), question)
    tool = _tool(answer, "find_fleet_programs")
    assert tool["decision"] == "allowed"
    assert tool["summary"].startswith("x_run.exe, lcotp.exe, java.exe: found on 2 of 2 databases read.")
    assert (
        "x_run.exe is connected but idle on 2; lcotp.exe was not found on any database read; "
        "java.exe has an active session on 2."
    ) in tool["summary"]
    assert answer.structured["reading"] == [
        "FINANCE: java.exe 12 sessions (4 active); x_run.exe 2 sessions (0 active)",
        "WAREHOUSE: java.exe 12 sessions (4 active); x_run.exe 2 sessions (0 active)",
    ]
    assert "sqlplus.exe" not in answer.content
    assert "lcotp.exe 0" not in answer.content
    assert "from v$" not in json.dumps(answer.tools).lower()
    assert not any(item["decision"] == "denied" for item in answer.tools)


def test_datafile_question_applies_the_size_from_the_question(settings, sessions, make_target) -> None:
    make_target(alias="FINANCE")
    over_ten = _ask(sessions, settings, _disabled(settings), "which database is having a tablespace with more than 10gb data file ?")
    assert _tool(over_ten, "find_fleet_datafiles")["summary"].startswith("1 of 1 databases read have a datafile larger than 10 GB.")
    assert over_ten.structured["reading"] == ["FINANCE: USERS largest datafile 32 GB, 3 files, 64 GB total"]
    over_fifty = _ask(sessions, settings, _disabled(settings), "Which databases have a datafile larger than 50 GB?")
    assert over_fifty.structured["reading"] == ["No datafile over 50 GB: FINANCE."]


def test_an_unread_database_is_reported_as_unknown_and_the_limit_is_stated(settings, sessions, make_target, monkeypatch) -> None:
    make_target(alias="FINANCE")
    make_target(alias="OFFLINE_DB")
    make_target(alias="WAREHOUSE")

    async def reading(_settings, alias: str, kind: str) -> ActivityReading:
        if alias == "OFFLINE_DB":
            return ActivityReading(kind, "get_session_programs", "unavailable", [], "The database could not be read just now.")
        return ActivityReading(kind, "get_session_programs", "ok", [
            {"program": "java.exe", "session_count": "5", "active_count": "2"},
        ])

    monkeypatch.setattr(fleet_module, "prepare_reading", reading)
    limited = settings.model_copy(update={"ai_fleet_max_databases": 2})
    answer = _ask(sessions, limited, _disabled(limited), "Which databases have an active session from java.exe?")
    assert _tool(answer, "find_fleet_programs")["summary"].startswith("java.exe: found on 1 of 1 databases read.")
    assert answer.structured["reading"] == [
        "FINANCE: java.exe 5 sessions (2 active)",
        "Could not be read, so not known: OFFLINE_DB (The database could not be read just now.).",
        "1 more enabled databases were not read because of the per-question limit.",
    ]


class _Model:
    """Stand-in model: nominates a fleet tool when asked to route, and writes an answer otherwise."""

    name = "local"
    model = "stub"

    def __init__(self, nomination: dict[str, object]) -> None:
        self.nomination = nomination
        self.prompts: list[str] = []
        self.routes = 0

    async def generate(self, messages, *, tools, structured_schema, correlation_id):
        del correlation_id
        assert tools is None
        if "tool" in structured_schema.get("properties", {}):
            self.routes += 1
            return ModelReply(json.dumps(self.nomination), 1, 1, self.name, self.model)
        self.prompts.append(messages[0]["content"] + "\n" + messages[-1]["content"])
        return ModelReply(
            json.dumps({"assessment": "Tomcat is connected to FINANCE.", "confidence": "high"}), 1, 1, self.name, self.model,
        )

    async def health(self) -> dict[str, object]:
        return {"status": "ok"}


def test_the_model_writes_the_fleet_answer_from_the_tool_rows(settings, sessions, make_target) -> None:
    finance = make_target(alias="FINANCE")
    with sessions.begin() as session:
        session.add(Incident(fingerprint="a", target_id=finance, rule_id="r1", state="new", severity="critical", message="ORA-00257"))
    model = _Model({"tool": ""})
    answer = _ask(sessions, settings, ChatGateway(model, "disclosure", True), "Which databases have reported an incident?")
    assert answer.llm_used is True
    assert answer.structured["kind"] == "fleet"
    assert answer.structured["summary"] == "Tomcat is connected to FINANCE."
    assert answer.structured["reading"][0].startswith("FINANCE: 1 open incidents (1 critical)")
    assert "A database that could not be read is not known" in answer.structured["limitations"]
    assert model.routes == 0
    assert "whole fleet of registered databases" in model.prompts[0]
    assert "FINANCE: 1 open incidents" in model.prompts[0]


def test_the_model_may_nominate_one_fleet_tool_when_no_fixed_route_matches(settings, sessions, make_target, monkeypatch) -> None:
    make_target(alias="FINANCE")

    async def reading(_settings, _alias: str, kind: str) -> ActivityReading:
        return ActivityReading(kind, "get_session_programs", "ok", [
            {"program": "Tomcat8", "session_count": "9", "active_count": "1"},
            {"program": "sqlplus.exe", "session_count": "1", "active_count": "0"},
        ])

    monkeypatch.setattr(fleet_module, "prepare_reading", reading)
    model = _Model({"tool": "find_fleet_programs", "programs": ["tomcat", "ghost.exe"]})
    answer = _ask(sessions, settings, ChatGateway(model, "disclosure", True), "Where is tomcat attached right now?")
    assert model.routes == 1
    assert _tool(answer, "route_question")["decision"] == "allowed"
    assert _tool(answer, "find_fleet_programs")["summary"].startswith("tomcat: found on 1 of 1 databases read.")
    assert answer.structured["reading"] == ["FINANCE: Tomcat8 9 sessions (1 active)"]

    off = settings.model_copy(update={"ai_router_fallback": False})
    silent = _Model({"tool": "find_fleet_programs", "programs": ["tomcat"]})
    plain = _ask(sessions, off, ChatGateway(silent, "disclosure", True), "Where is tomcat attached right now?")
    assert silent.routes == 0
    assert not any(item["tool"] == "find_fleet_programs" for item in plain.tools)


def test_explanations_and_drafts_in_fleet_chat_do_not_scan_the_fleet(settings, sessions, make_target, monkeypatch) -> None:
    make_target(alias="FINANCE")

    async def reading(_settings, _alias: str, _kind: str) -> ActivityReading:
        raise AssertionError("no database should be read for this question")

    monkeypatch.setattr(fleet_module, "prepare_reading", reading)
    model = _Model({"tool": "find_fleet_programs", "programs": []})
    for question in ("What is our company policy on archive log mode?", "Explain what a checkpoint is", "Kill session 123 on every database"):
        answer = _ask(sessions, settings, ChatGateway(model, "disclosure", True), question)
        assert not any(item["tool"].startswith(("find_fleet", "get_fleet")) for item in answer.tools), question
    assert model.routes == 0


def test_a_new_thread_returns_the_questions_for_its_scope(settings, sessions, make_target) -> None:
    target_id = str(make_target(alias="FINANCE"))
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)
    client = TestClient(app)
    client.__enter__()
    app.state.session_factory = sessions
    headers = {"X-Sentinel-Role": "operator", "X-Sentinel-Principal": "olga"}
    try:
        fleet = client.post("/api/v1/chat/threads", json={"scope_type": "fleet"}, headers=headers).json()
        database = client.post("/api/v1/chat/threads", json={"scope_type": "database", "scope_id": target_id}, headers=headers).json()
        asked = client.post(
            f"/api/v1/chat/threads/{fleet['id']}/messages",
            json={"content": "Which databases have an active session from java.exe?"}, headers=headers,
        )
    finally:
        client.__exit__(None, None, None)
    assert [item["label"] for item in fleet["guided_commands"]] == [item["label"] for item in FLEET_COMMANDS]
    database_labels = {item["label"] for item in database["guided_commands"]}
    assert "Datafiles" in database_labels
    assert "Policy violations" not in database_labels
    assert "Database users" not in database_labels
    assert asked.status_code == 200
    assert "find_fleet_programs" in asked.text
    assert "FINANCE: java.exe 12 sessions (4 active)" in asked.text
