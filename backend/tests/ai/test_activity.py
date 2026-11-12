"""Question routing and fixed-query readings. The question text never becomes SQL."""

import oracledb

from sentinel.adapters.oracle.activity import CATALOG, ActivityAccessError, collect_rows
from sentinel.ai.activity import ActivityReading, match_activity, prepare_reading, reading_outcome
from sentinel.config.settings import Settings


def test_operator_questions_select_one_fixed_reading() -> None:
    assert match_activity("list all the files") == "datafiles"
    assert match_activity("list all the data files") == "datafiles"
    assert match_activity("How full is the memory?") == "memory"
    assert match_activity("What is the top query consuming resources?") == "top_sql"
    assert match_activity("Who is logged on the database?") == "sessions"
    assert match_activity("Are any backups running?") == "backups"
    assert match_activity("Is a scheduler job running?") == "jobs"
    assert match_activity("What is the fast recovery area status?") is None
    assert match_activity("What is the FRA status?") is None


def test_question_text_is_not_copied_into_sql() -> None:
    question = "list all the files; drop table users"
    assert match_activity(question) == "datafiles"
    for queries in CATALOG.values():
        for query in queries:
            assert question not in query.sql
            assert "drop table" not in query.sql.lower()


def test_memory_rows_become_a_fullness_reading() -> None:
    class Cursor:
        description = (("POOL",), ("NAME",), ("VALUE",))

        def execute(self, sql: str) -> None:
            assert "v$sgainfo" in sql

        def fetchall(self) -> list[tuple[str, str, int]]:
            return [
                ("sga", "Maximum SGA Size", 200),
                ("sga", "Free SGA Memory Available", 50),
            ]

        def close(self) -> None:
            return None

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

    rows, note = collect_rows(Connection(), "memory")  # type: ignore[arg-type]
    assert note == ""
    assert rows[0]["name"] == "Maximum SGA Size"
    outcome = reading_outcome(ActivityReading("memory", "get_memory_status", "ok", rows), "target")
    assert any("75 percent" in line for line in outcome.payload["lines"])


def test_missing_grant_names_the_view_and_hides_the_driver_text() -> None:
    class Cursor:
        description = ()

        def execute(self, sql: str) -> None:
            del sql
            raise oracledb.DatabaseError("ORA-00942: secret path should not surface")

        def fetchall(self) -> list[object]:
            return []

        def close(self) -> None:
            return None

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

    try:
        collect_rows(Connection(), "sessions")  # type: ignore[arg-type]
    except ActivityAccessError as exc:
        assert exc.object_name == "SYS.V_$SESSION"
        assert "secret" not in exc.object_name
    else:
        raise AssertionError("missing grant must stop the reading")


async def test_synthetic_mode_does_not_open_oracle() -> None:
    settings = Settings(_env_file=None, synthetic_mode=True)
    reading = await prepare_reading(settings, "PDBORCL", "sessions")
    outcome = reading_outcome(reading, "target")
    assert outcome.name == "get_sessions"
    assert "APPUSER" in outcome.payload["lines"][0]
