"""Phase 26: a question about the schemas of this database is read from it, and an earlier answer is not repeated."""

import asyncio
import json
import re
import uuid
from pathlib import Path

import pytest

from sentinel.adapters.oracle.activity import CATALOG
from sentinel.ai.activity import SYNTHETIC, TOOL_DESCRIPTIONS, ActivityReading, match_activity, reading_outcome
from sentinel.ai.gateway import ChatGateway
from sentinel.ai.orchestrator import USER_LIST_DENIAL, Orchestrator, _claims, merge_decision, repeats_earlier
from sentinel.ai.reading_text import present_reading
from sentinel.ai.supervisor import SUPERVISOR_PROMPT, Decision
from sentinel.ai.types import EvidenceCitation, ModelReply, ToolOutcome
from sentinel.knowledge.acl import Caller
from sentinel.persistence.models import ChatThread

OPERATOR = Caller("operator", "olga")
ADMIN = Caller("admin", "sentinel")
QUESTION = "which schema in the database is not a system schema?"
REDO_QUESTION = "what is the combined size of the log files?"
REDO_ANSWER = "The combined size of the redo log files is 150.0 MB."
SCHEMA_ANSWER = "A schema that Oracle does not maintain belongs to an application. System schemas such as SYS are created by Oracle."
KNOWLEDGE = {"routes": ["oracle_knowledge"], "readings": []}


class _Model:
    """A stand-in model. `route` is the supervisor reply; `answers` are the assessments, the last one repeating."""

    name = "local"
    model = "stub"

    def __init__(self, route: object, answers: list[str] | None = None, confidence: str = "high") -> None:
        self.route = route
        self.answers = answers or ["Answer written by the model."]
        self.confidence = confidence
        self.supervisor_prompts: list[str] = []
        self.asked: list[list[dict[str, str]]] = []

    async def generate(self, messages, *, tools, structured_schema, correlation_id):
        del correlation_id
        assert tools is None
        if structured_schema and "routes" in structured_schema.get("properties", {}):
            self.supervisor_prompts.append(messages[0]["content"])
            return ModelReply(json.dumps(self.route), 1, 1, self.name, self.model)
        self.asked.append(messages)
        text = self.answers[min(len(self.asked), len(self.answers)) - 1]
        return ModelReply(json.dumps({"assessment": text, "confidence": self.confidence}), 1, 1, self.name, self.model)

    async def health(self) -> dict[str, object]:
        return {"status": "ok"}


class _Copier(_Model):
    """Repeats the last answer in the conversation, as the local model did, until told it is a copy."""

    def __init__(self, route: object, *, stubborn: bool = False) -> None:
        super().__init__(route)
        self.stubborn = stubborn

    async def generate(self, messages, *, tools, structured_schema, correlation_id):
        if structured_schema and "routes" in structured_schema.get("properties", {}):
            return await super().generate(messages, tools=tools, structured_schema=structured_schema, correlation_id=correlation_id)
        self.asked.append(messages)
        earlier = [item["content"] for item in messages[1:-1] if item["role"] == "assistant"]
        noticed = "repeats an earlier answer" in messages[-1]["content"]
        if earlier and (self.stubborn or not noticed):
            text = earlier[0].split("\n")[0]
        else:
            text = SCHEMA_ANSWER
        return ModelReply(json.dumps({"assessment": text, "confidence": "high"}), 1, 1, self.name, self.model)


def _thread(sessions, target_id: str, caller: Caller) -> str:
    with sessions.begin() as session:
        thread = ChatThread(
            principal=caller.principal, role=caller.role, scope_type="database", scope_id=target_id,
            provider="local", model="stub",
        )
        session.add(thread)
        session.flush()
        return str(thread.id)


def _ask(sessions, settings, model: _Model, thread_id: str, caller: Caller, question: str, **changes):
    applied = settings.model_copy(update={"ai_enabled": True, "ai_provider": "local", **changes})
    worker = Orchestrator(applied, ChatGateway(model, "disclosure", True))
    with sessions.begin() as session:
        thread = session.get(ChatThread, uuid.UUID(thread_id))
        return asyncio.run(worker.answer(session, thread, caller, question))


def _tools(answer) -> dict[str, str]:
    return {item["tool"]: item["decision"] for item in answer.tools}


# --- the reading -----------------------------------------------------------------------------------


def test_the_user_reading_carries_the_oracle_maintained_flag_and_lists_application_schemas_first() -> None:
    (query,) = CATALOG["database_users"]
    assert query.object_name == "SYS.DBA_USERS"
    statement = " ".join(query.sql.split()).upper()
    assert statement.startswith("SELECT USERNAME, ACCOUNT_STATUS, ORACLE_MAINTAINED FROM (")
    assert "FROM DBA_USERS ORDER BY ORACLE_MAINTAINED, USERNAME" in statement
    assert statement.endswith("WHERE ROWNUM <= 80")
    assert ";" not in statement
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|ALTER|CREATE|DROP|GRANT)\b", statement)
    verify = (Path(__file__).resolve().parents[3] / "scripts" / "oracle" / "03_verify_gsystem.sql").read_text(encoding="utf-8")
    assert "oracle_maintained" in verify
    assert "application schema" in TOOL_DESCRIPTIONS["list_database_users"]


def test_the_reading_names_application_schemas_apart_from_the_ones_oracle_maintains() -> None:
    rows = [
        {"source": "dba_users", "username": "GSYSTEM", "account_status": "OPEN", "oracle_maintained": "N"},
        {"source": "dba_users", "username": "HRAPP", "account_status": "OPEN", "oracle_maintained": "N"},
        {"source": "dba_users", "username": "SYS", "account_status": "OPEN", "oracle_maintained": "Y"},
        {"source": "dba_users", "username": "XDB", "account_status": "EXPIRED & LOCKED", "oracle_maintained": "Y"},
        {"source": "dba_users", "username": "SYSTEM", "account_status": "OPEN", "oracle_maintained": "Y"},
    ]
    summary, lines = present_reading("database_users", rows)
    assert summary == "5 database users: 2 application schemas and 3 maintained by Oracle."
    assert lines == [
        "Application schemas, not maintained by Oracle: 2",
        "GSYSTEM OPEN (the Sentinel monitoring account)",
        "HRAPP OPEN",
        "Schemas maintained by Oracle (system schemas): 3",
        "OPEN: SYS, SYSTEM",
        "EXPIRED & LOCKED: XDB",
    ]
    # Many Oracle-maintained accounts stay a few lines, so the application schemas are never pushed out of view.
    many = [{"source": "dba_users", "username": f"ORA{n:02d}", "account_status": "LOCKED", "oracle_maintained": "Y"} for n in range(30)]
    _summary, grouped = present_reading("database_users", [rows[1], *many])
    assert grouped[:3] == ["Application schemas, not maintained by Oracle: 1", "HRAPP OPEN", "Schemas maintained by Oracle (system schemas): 30"]
    assert len(grouped) == 6
    assert grouped[3].startswith("LOCKED: ORA00, ORA01,") and grouped[3].count(",") == 11
    assert grouped[5] == "LOCKED: ORA24, ORA25, ORA26, ORA27, ORA28, ORA29"


def test_a_stored_reading_from_before_the_flag_is_one_plain_list() -> None:
    rows = [
        {"source": "dba_users", "username": "APPUSER", "account_status": "OPEN"},
        {"source": "dba_users", "username": "SYS", "account_status": "OPEN"},
    ]
    summary, lines = present_reading("database_users", rows)
    assert summary == "2 database users."
    assert lines == ["APPUSER OPEN", "SYS OPEN"]
    assert present_reading("database_users", [])[0] == "No database users were returned."


def test_the_synthetic_reading_has_both_kinds_of_schema() -> None:
    outcome = reading_outcome(
        ActivityReading("database_users", "list_database_users", "ok", [dict(row) for row in SYNTHETIC["database_users"]]),
        "db",
    )
    assert outcome.summary == "5 database users: 3 application schemas and 2 maintained by Oracle."
    assert "APPUSER OPEN" in outcome.payload["lines"]
    assert outcome.payload["lines"][-2:] == ["Schemas maintained by Oracle (system schemas): 2", "OPEN: SYS, SYSTEM"]


# --- the fixed patterns ----------------------------------------------------------------------------


@pytest.mark.parametrize("question", [
    QUESTION,
    "Which schemas are not system schemas?",
    "List the schemas in this database",
    "How many application schemas are there?",
    "Which users are Oracle-maintained?",
    "Show me the non-system users",
    "What schemas exist here?",
    "Which application users exist?",
])
def test_a_question_about_which_schemas_exist_is_the_user_reading(question: str) -> None:
    assert match_activity(question) == "database_users"


@pytest.mark.parametrize(("question", "expected"), [
    ("Which schema uses the most space?", None),
    ("Which schema owns the largest tables?", "segments"),
    ("Which objects are invalid in monitored application schemas?", None),
    ("Which schema runs the most SQL?", None),
    ("Which system privileges does the schema owner have?", None),
    ("Which database users are locked?", "security"),
    ("Which application users are connected?", "sessions"),
    ("List all database users", "database_users"),
    ("How full are the tablespaces?", "tablespace"),
])
def test_a_schema_question_about_something_else_is_not_the_user_reading(question: str, expected: str | None) -> None:
    assert match_activity(question) == expected


# --- routing ---------------------------------------------------------------------------------------


def test_the_supervisor_prompt_says_schemas_are_read_from_the_database() -> None:
    assert "users, schemas" in SUPERVISOR_PROMPT
    assert "users, accounts, or schemas" in SUPERVISOR_PROMPT
    assert "is read from the database and not explained from memory" in SUPERVISOR_PROMPT


def test_the_reading_is_added_when_the_supervisor_chose_an_explanation() -> None:
    knowledge = Decision(("oracle_knowledge",), ())
    claims, readings, added = merge_decision(knowledge, _claims(QUESTION), QUESTION, True, True)
    assert claims == {"concept", "live"}
    assert readings == ["database_users"]
    assert added is True
    # An operator may not read the listing. The question becomes a database question, and the refusal follows.
    claims, readings, added = merge_decision(knowledge, _claims(QUESTION), QUESTION, False, True)
    assert (claims, readings, added) == ({"live"}, ["database_users"], True)
    # Also when the supervisor chose the database with a reading the operator may read.
    other = Decision(("database",), ("security",))
    assert merge_decision(other, _claims(QUESTION), QUESTION, False, True) == ({"live"}, ["database_users"], True)
    # An explanation stays an explanation for everyone, and so does a question with no database selected.
    for explain in ("What is a schema?", "Explain what a system schema is for"):
        for admin in (True, False):
            claims, readings, added = merge_decision(knowledge, _claims(explain), explain, admin, True)
            assert (claims, readings, added) == ({"concept"}, [], False)
    assert merge_decision(knowledge, _claims(QUESTION), QUESTION, False, False) == ({"concept"}, [], False)
    # A policy question that mentions schemas is not turned into a refusal.
    policy = Decision(("policy",), ())
    asked = "Are application schemas allowed by our policy?"
    assert merge_decision(policy, _claims(asked), asked, False, True)[0] == {"policy"}


def test_an_administrator_gets_the_schemas_from_the_database(settings, sessions, make_target) -> None:
    target_id = str(make_target(alias="PDBORCL"))
    thread_id = _thread(sessions, target_id, ADMIN)
    model = _Model(KNOWLEDGE)
    answer = _ask(sessions, settings, model, thread_id, ADMIN, QUESTION)
    tools = _tools(answer)
    assert tools["list_database_users"] == "allowed"
    assert "This database" in answer.structured["routes"]
    assert answer.structured["routed_by"] == "supervisor"
    reading = answer.structured["reading"]
    assert reading[0] == "Application schemas, not maintained by Oracle: 3"
    assert "APPUSER OPEN" in reading
    assert "Schemas maintained by Oracle (system schemas): 2" in reading
    # The rows are in what the model was sent.
    assert "Application schemas, not maintained by Oracle: 3" in model.asked[0][-1]["content"]
    assert "GSYSTEM OPEN (the Sentinel monitoring account)" in reading
    assert "The selected database was not queried." not in answer.structured["limitations"]


def test_an_operator_is_told_the_listing_is_restricted(settings, sessions, make_target) -> None:
    target_id = str(make_target(alias="PDBORCL"))
    for route in (KNOWLEDGE, {"routes": ["database"], "readings": ["get_security_posture"]}, {"routes": ["database"]}):
        thread_id = _thread(sessions, target_id, OPERATOR)
        model = _Model(route)
        answer = _ask(sessions, settings, model, thread_id, OPERATOR, QUESTION)
        assert answer.content == USER_LIST_DENIAL
        assert _tools(answer) == {"list_database_users": "denied"}
        assert model.asked == []
        assert answer.llm_used is False
    # With the supervisor off the patterns give the same refusal.
    thread_id = _thread(sessions, target_id, OPERATOR)
    answer = _ask(sessions, settings, _Model(KNOWLEDGE), thread_id, OPERATOR, QUESTION, ai_supervisor_enabled=False)
    assert answer.content == USER_LIST_DENIAL


def test_what_a_schema_is_stays_an_explanation(settings, sessions, make_target) -> None:
    target_id = str(make_target(alias="PDBORCL"))
    thread_id = _thread(sessions, target_id, ADMIN)
    answer = _ask(sessions, settings, _Model(KNOWLEDGE), thread_id, ADMIN, "What is a schema?")
    tools = _tools(answer)
    assert "list_database_users" not in tools
    assert tools["search_knowledge"] == "allowed"
    assert answer.structured["routes"] == ["Oracle knowledge"]


# --- an earlier answer is not repeated -------------------------------------------------------------


def test_a_copy_of_an_earlier_answer_is_recognised() -> None:
    stored = REDO_ANSWER + "\nLimitations: Current configuration only.\n\nDatabase reading:\nGroup 1 50.0 MB"
    history = [{"role": "user", "content": REDO_QUESTION}, {"role": "assistant", "content": stored}]
    assert repeats_earlier(REDO_ANSWER, QUESTION, history) is True
    assert repeats_earlier("the combined size of the  redo log files is 150.0 mb", QUESTION, history) is True
    assert repeats_earlier("The combined size of the redo log file is 150.0 MB.", QUESTION, history) is True
    assert repeats_earlier(SCHEMA_ANSWER, QUESTION, history) is False
    assert repeats_earlier("The combined size of the undo tablespace is 2.0 GB, which is larger.", QUESTION, history) is False
    # The same question asked again may have the same answer.
    assert repeats_earlier(REDO_ANSWER, "What is the combined size of the log files?", history) is False
    # A short answer is not compared, and neither is the operator's own text.
    assert repeats_earlier("Yes.", QUESTION, [{"role": "user", "content": "x"}, {"role": "assistant", "content": "Yes."}]) is False
    assert repeats_earlier(REDO_QUESTION + " " + REDO_QUESTION, QUESTION, [{"role": "user", "content": REDO_QUESTION + " " + REDO_QUESTION}]) is False
    assert repeats_earlier(REDO_ANSWER, QUESTION, []) is False


def test_an_answer_copied_from_the_previous_turn_is_asked_again(settings, sessions, make_target) -> None:
    """The fault that was seen: a redo log answer, then the same sentence for a question about schemas."""
    target_id = str(make_target(alias="PDBORCL"))
    thread_id = _thread(sessions, target_id, OPERATOR)
    first = _ask(sessions, settings, _Model({"routes": ["database"], "readings": ["get_redo_logs"]}, [REDO_ANSWER]), thread_id, OPERATOR, REDO_QUESTION)
    assert first.structured["summary"] == REDO_ANSWER

    question = "Why does Oracle keep some schemas for itself?"
    model = _Copier(KNOWLEDGE)
    answer = _ask(sessions, settings, model, thread_id, OPERATOR, question)
    assert answer.structured["summary"] == SCHEMA_ANSWER
    assert "150.0 MB" not in answer.content
    assert len(model.asked) == 2
    assert model.asked[1][-1]["content"].startswith("That reply repeats an earlier answer in this conversation")
    assert any(item["content"].startswith(REDO_ANSWER) for item in model.asked[1] if item["role"] == "assistant")


def test_a_model_that_keeps_copying_is_asked_without_the_conversation(settings, sessions, make_target) -> None:
    target_id = str(make_target(alias="PDBORCL"))
    thread_id = _thread(sessions, target_id, OPERATOR)
    _ask(sessions, settings, _Model({"routes": ["database"], "readings": ["get_redo_logs"]}, [REDO_ANSWER]), thread_id, OPERATOR, REDO_QUESTION)
    model = _Copier(KNOWLEDGE, stubborn=True)
    answer = _ask(sessions, settings, model, thread_id, OPERATOR, "Why does Oracle keep some schemas for itself?")
    assert answer.structured["summary"] == SCHEMA_ANSWER
    assert len(model.asked) == 3
    assert [item["role"] for item in model.asked[2]] == ["system", "user"]


def test_the_same_reading_asked_twice_is_not_asked_again(settings, sessions, make_target) -> None:
    target_id = str(make_target(alias="PDBORCL"))
    thread_id = _thread(sessions, target_id, OPERATOR)
    route = {"routes": ["database"], "readings": ["get_redo_logs"]}
    _ask(sessions, settings, _Model(route, [REDO_ANSWER]), thread_id, OPERATOR, REDO_QUESTION)
    model = _Model(route, [REDO_ANSWER])
    answer = _ask(sessions, settings, model, thread_id, OPERATOR, "How big are the redo logs in total?")
    assert answer.structured["summary"] == REDO_ANSWER
    assert len(model.asked) == 1


def test_a_first_answer_and_a_different_answer_cost_one_call(settings, sessions, make_target) -> None:
    target_id = str(make_target(alias="PDBORCL"))
    thread_id = _thread(sessions, target_id, OPERATOR)
    model = _Model(KNOWLEDGE, ["Undo keeps the old version of changed rows."])
    _ask(sessions, settings, model, thread_id, OPERATOR, "Why does Oracle need undo?")
    assert len(model.asked) == 1
    again = _Model(KNOWLEDGE, ["Redo records every change so that it can be applied again after a failure."])
    _ask(sessions, settings, again, thread_id, OPERATOR, "Why does Oracle need redo?")
    assert len(again.asked) == 1


# --- an explanation with no source ----------------------------------------------------------------


def _compose(settings, model: _Model, citations: list[EvidenceCitation], advice: str = "concept"):
    applied = settings.model_copy(update={"ai_enabled": True, "ai_provider": "local"})
    worker = Orchestrator(applied, ChatGateway(model, "disclosure", True))
    outcomes = [ToolOutcome("search_knowledge", "allowed", f"{len(citations)} cited document passages.", citations, {"items": []})]
    return asyncio.run(worker._compose("What is a schema?", "question", outcomes, citations, "test", advice))[5]


def test_an_explanation_with_no_manual_passage_is_low_confidence_and_says_so(settings) -> None:
    structured = _compose(settings, _Model(KNOWLEDGE, ["A schema is the set of objects a user owns."]), [])
    assert structured.confidence == "low"
    assert "No manual passage was found for this question" in structured.limitations
    assert "without a source" in structured.limitations
    assert "The selected database was not queried." in structured.limitations


def test_an_explanation_with_a_manual_passage_keeps_its_confidence(settings) -> None:
    passage = EvidenceCitation(
        "document", "ora-19c-manual/database-concepts.pdf", "ora-19c-manual/database-concepts.pdf",
        "A database schema is a logical container for data structures.",
        document_path="ora-19c-manual/database-concepts.pdf", heading="Introduction to Schema Objects",
    )
    structured = _compose(settings, _Model(KNOWLEDGE, ["A schema is the set of objects a user owns."]), [passage])
    assert structured.confidence == "high"
    assert "with the manual passages it used listed as sources" in structured.limitations
    assert "No manual passage was found" not in structured.limitations
    # Only an explanation is capped. A general answer keeps what the model said.
    general = _compose(settings, _Model(KNOWLEDGE, ["I explain Oracle and read approved measurements."], "medium"), [], "other")
    assert general.confidence == "medium"
