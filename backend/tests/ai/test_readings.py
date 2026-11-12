"""Operator questions stay on named readings, and the MCP server does not accept SQL."""

import pytest

from sentinel.ai.activity import SYNTHETIC, TOOL_NAMES, ActivityReading, match_activity, reading_outcome
from sentinel.config.settings import Settings
from sentinel.mcp.client import ask_reading
from sentinel.mcp.server import run_tool

QUESTIONS = (
    ("Is my selected Oracle database currently running and healthy?", "availability"),
    ("When was the database last restarted, and has it experienced any unexpected outages?", "availability"),
    ("Are all the database instances, PDBs, and services operational?", "availability"),
    ("Has the database experienced any critical ORA errors in the last 24 hours?", "errors"),
    ("Why is my database showing an Unknown, Warning, or Critical status?", "availability"),
    ("Which tablespaces are running out of space?", "tablespace"),
    ("Which tablespaces have exceeded 80%, 90%, or 95% utilization?", "tablespace"),
    ("At the current growth rate, when will this tablespace become full?", "growth"),
    ("Are any datafiles reaching their maximum allocated size or autoextend limits?", "tablespace"),
    ("Which tablespaces have grown the most during the last 7 or 30 days?", "growth"),
    ("Why is the database responding slowly right now?", "performance"),
    ("What are the current database CPU utilization and active session counts?", "performance"),
    ("What are the top database wait events affecting performance?", "performance"),
    ("Is the database experiencing unusual I/O latency or throughput problems?", "performance"),
    ("How does the current database performance compare with the normal baseline?", "performance"),
    ("Are any sessions currently blocking other sessions?", "blocking"),
    ("Which sessions have been running for an unusually long time?", "blocking"),
    ("Which database users or application services have the highest session counts?", "blocking"),
    ("Are there any deadlocks or lock contention problems?", "blocking"),
    ("Are database session, process, or connection limits close to exhaustion?", "blocking"),
    ("Which SQL IDs are consuming the most CPU or elapsed time?", "sql_workload"),
    ("Which SQL statements have the highest execution counts?", "sql_workload"),
    ("Have any SQL statements experienced significant performance degradation?", "sql_workload"),
    ("Are there SQL statements generating excessive physical reads or logical I/O?", "sql_workload"),
    ("Has a change in an execution plan contributed to the performance slowdown?", "sql_workload"),
    ("Is the database experiencing SGA or PGA memory pressure?", "memory"),
    ("Are temporary tablespaces experiencing unusually high utilization?", "memory_config"),
    ("Are there signs of excessive hard parsing or shared pool contention?", "memory_config"),
    ("Have important database initialization parameters changed recently?", "memory_config"),
    ("Are any resource utilization limits approaching their configured maximums?", "memory_config"),
    ("When was the last successful full or incremental database backup?", "recovery"),
    ("Have any RMAN backup jobs failed in the last 7 days?", "recovery"),
    ("Are archive logs being generated and backed up as expected?", "recovery"),
    ("Can the database currently meet its defined recovery point and recovery time objectives?", "recovery"),
    ("Is the Data Guard configuration healthy?", "dataguard"),
    ("What are the current redo transport lag and apply lag?", "dataguard"),
    ("Are there any redo shipping or standby synchronization errors?", "dataguard"),
    ("Are the primary and standby databases in their expected roles and states?", "dataguard"),
    ("Are there any conditions that could prevent a safe failover or switchover?", "dataguard"),
    ("Are there any locked or expired application database accounts?", "security"),
    ("Have any privileged database accounts or grants changed recently?", "security"),
    ("Are there any invalid database objects affecting production applications?", "security"),
    ("Have any scheduled database jobs failed or stopped executing?", "failed_jobs"),
    ("What are the most critical issues affecting this database right now?", "issues"),
    ("What changed in the database around the time the incident started?", "issues"),
    ("Has this incident happened before, and how was it resolved?", "issues"),
    ("What is the likely root cause of the current database performance or availability issue?", "issues"),
    ("What corrective actions should the DBA take, ordered by severity and operational risk?", "issues"),
)


@pytest.mark.parametrize(("question", "kind"), QUESTIONS)
def test_catalog_question_selects_one_named_reading(question: str, kind: str) -> None:
    assert match_activity(question) == kind


def test_fast_recovery_area_stays_on_the_existing_fra_reading() -> None:
    assert match_activity("Is the Fast Recovery Area nearing its capacity limit?") is None
    assert match_activity("Which compliance policies are failing?") is None


def test_every_reading_has_a_plain_language_answer() -> None:
    for kind, tool_name in TOOL_NAMES.items():
        outcome = reading_outcome(ActivityReading(kind, tool_name, "ok", [dict(row) for row in SYNTHETIC[kind]]), "db")
        lines = outcome.payload["lines"]
        assert isinstance(lines, list) and lines
        assert all(isinstance(line, str) and line.strip() for line in lines)


def test_mcp_tool_rejects_sql_and_answers_from_the_named_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_MCP_SYNTHETIC", "1")
    with pytest.raises(ValueError, match="alias"):
        run_tool("get_sessions", {"alias": "PDBORCL", "sql": "select 1 from dual"})
    payload = run_tool("get_blocking_sessions", {"alias": "PDBORCL"})
    assert payload["status"] == "ok"
    assert payload["rows"][0]["blocking_session"] == "18"


async def test_mcp_client_round_trip_uses_the_server() -> None:
    settings = Settings(_env_file=None, synthetic_mode=True)
    status, rows, note = await ask_reading(settings, "get_sessions", "PDBORCL")
    assert status == "ok"
    assert note == ""
    assert rows[0]["username"] == "APPUSER"
