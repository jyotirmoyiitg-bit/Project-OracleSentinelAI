"""The 50 operator questions stay on named tools and do not invent missing history."""

import pytest

from sentinel.ai.activity import SYNTHETIC, ActivityReading, match_activity, reading_outcome
from sentinel.ai.catalog import QUESTIONS, match_catalog, present_catalog
from sentinel.ai.orchestrator import Orchestrator
from sentinel.ai.safety import FORBIDDEN_TOOLS
from sentinel.knowledge.acl import Caller
from sentinel.persistence.models import ChatThread, DatabaseTarget


def test_every_catalog_question_matches_its_own_prompt() -> None:
    assert len(QUESTIONS) == 50
    assert len({item.question_id for item in QUESTIONS}) == 50
    assert FORBIDDEN_TOOLS.isdisjoint(item.tool for item in QUESTIONS)
    for item in QUESTIONS:
        assert match_catalog(item.prompt) == item
        assert item.tool not in {"execute_sql", "sql_query", "execute_shell"}


def test_existing_questions_do_not_enter_the_catalog() -> None:
    for prompt in (
        "Is there a problem with this database?",
        "Are there any Oracle errors?",
        "List the database users",
        "Are any sessions currently blocking other sessions?",
        "What is the FRA status?",
        "Who is logged on the database?",
    ):
        assert match_catalog(prompt) is None


def test_reachable_question_uses_the_connectivity_tool() -> None:
    question = match_catalog("Is the selected database or PDB reachable right now?")
    assert question is not None
    assert question.tool == "get_connectivity"
    assert match_activity(question.prompt) == "availability"
    reading = ActivityReading(
        "availability", "get_database_availability", "ok", [dict(row) for row in SYNTHETIC["availability"]],
    )
    outcome = reading_outcome(reading, "db")
    text = present_catalog(question, [outcome])
    assert "Q01 Reachable. Status OK." in text
    assert "Instance PDBORCL status OPEN" in text
    assert outcome.name == "get_connectivity"
    assert outcome.citations
    assert all(item.detail in text for item in outcome.citations)
    assert not any("live read" in item.detail.lower() for item in outcome.citations)
    assert outcome.payload["catalog"]["tool"] == "get_connectivity"
    assert outcome.payload["catalog"]["status"] == "OK"


def test_missing_history_is_not_reported_as_zero() -> None:
    question = match_catalog("Are session counts increasing abnormally relative to baseline?")
    assert question is not None
    text = present_catalog(question, [])
    assert "Status INSUFFICIENT_DATA." in text
    assert "Health UNKNOWN." in text
    assert "no deviation is calculated" in text
    assert "0" not in text.split("Status", 1)[0]


@pytest.mark.asyncio
async def test_catalog_answer_does_not_call_the_model(settings, sessions) -> None:
    with sessions.begin() as session:
        target = DatabaseTarget(engine="oracle", display_name="PDB", tns_alias="PDBORCL", environment="test")
        session.add(target)
        session.flush()
        thread = ChatThread(
            principal="sentinel", role="admin", scope_type="database", scope_id=str(target.id),
            provider="hosted", model="test",
        )
        session.add(thread)
        session.flush()
        thread_id = thread.id

    class _NoModel:
        name = "hosted"
        model = "test"
        disclosure = "test"
        available = True

        async def generate(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise AssertionError("the catalog answer must not call the model")

    orchestrator = Orchestrator(settings, _NoModel())  # type: ignore[arg-type]
    with sessions.begin() as session:
        stored = session.get(ChatThread, thread_id)
        assert stored is not None
        answer = await orchestrator.answer(
            session, stored, Caller("admin", "sentinel"),
            "Which tablespaces currently exceed the configured warning or critical thresholds?",
        )
    assert answer.llm_used is False
    assert answer.tools[0]["tool"] == "get_tablespace_usage"
    assert "Q06 Tablespaces over threshold. Status OK." in answer.content
    assert "USERS" in answer.content
    assert "Limitations:" in answer.content
    assert any(item.get("group") == "Storage" for item in answer.guided_commands)
