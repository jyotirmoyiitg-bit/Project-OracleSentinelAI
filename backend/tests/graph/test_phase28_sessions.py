"""Phase 28: SessionDB, the user sessions of one database grouped by program, and one session's statement and plan."""

import asyncio
import importlib
import re
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from sentinel.adapters.oracle import activity
from sentinel.adapters.oracle.activity import CATALOG, ActivityAccessError, ActivityUnavailable
from sentinel.graph import sections, session_sql

OPERATOR = {"X-Sentinel-Role": "operator", "X-Sentinel-Principal": "olga"}
VIEWER = {"X-Sentinel-Role": "viewer", "X-Sentinel-Principal": "vera"}
DBA = {"X-Sentinel-Role": "dba", "X-Sentinel-Principal": "dana"}
ADMIN = {"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"}
GRANTS = Path(__file__).resolve().parents[3] / "scripts" / "oracle" / "02_grant_readonly_monitoring.sql"


def _client(settings, sessions) -> TestClient:
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)
    client = TestClient(app)
    client.__enter__()
    app.state.session_factory = sessions
    return client


def _row(sid: int, program: str, status: str = "INACTIVE", **extra: str) -> dict[str, str]:
    return {
        "source": "v$session", "sid": str(sid), "username": extra.get("username", "APP"), "osuser": "someone",
        "machine": extra.get("machine", "host01"), "terminal": extra.get("terminal", "WS-1"), "program": program,
        "status": status, "module": extra.get("module", program), "action": "", "logon_time": "2026-11-20 08:04:46",
        "last_call_at": "2026-11-20 08:26:58", "last_call_seconds": extra.get("idle", "462"), "sql_id": extra.get("sql_id", ""),
    }


def test_the_reading_is_one_fixed_read_only_statement_on_a_granted_view() -> None:
    granted = set(re.findall(r"ON\s+(SYS\.[A-Z_$]+)", GRANTS.read_text(encoding="utf-8").upper()))
    (query,) = CATALOG["get_session_list"]
    statement = query.sql.strip()
    assert query.object_name == "SYS.V_$SESSION" and query.object_name in granted
    assert statement.upper().startswith("SELECT")
    assert ";" not in statement and ":" not in statement.replace("HH24:MI:SS", "")
    assert not re.search(r"(?i)\b(insert|update|delete|merge|alter|drop|create|grant|begin|execute|kill)\b", statement)
    assert "ROWNUM <= 500" in statement
    lowered = statement.lower()
    # User sessions only, and not the session that runs this reading.
    assert "type = 'user'" in lowered and "sys_context('userenv', 'sid')" in lowered
    # No SQL text: only the identifier of the statement a session is running.
    assert "sql_text" not in lowered and "sql_fulltext" not in lowered and "v$sql " not in lowered


def test_sessions_are_grouped_by_program_like_a_session_browser() -> None:
    rows = [
        _row(488, "lcotp.exe"), _row(246, "lcotp.exe"),
        _row(730, "Toad.exe", "ACTIVE", sql_id="5jm8qnb729z8u", idle="2"), _row(608, "Toad.exe"),
        _row(12, "", username="ODD"),
    ]
    picture = sections.assemble_sessions(rows, None)
    assert [group.program for group in picture.groups] == ["LCOTP.EXE", "TOAD.EXE", "(no program)"]
    assert (picture.total, picture.active, picture.inactive, picture.programs) == (5, 1, 4, 3)
    lcotp, toad, _ = picture.groups
    assert [row.sid for row in lcotp.sessions] == [246, 488]
    assert toad.active == 1
    active = next(row for row in toad.sessions if row.sid == 730)
    assert active.status == "ACTIVE" and active.sql_id == "5jm8qnb729z8u" and active.idle_seconds == 2
    assert active.logon_time == "2026-11-20 08:04:46" and active.last_call == "2026-11-20 08:26:58"
    assert picture.capped is False


def test_terminal_falls_back_to_the_machine() -> None:
    picture = sections.assemble_sessions([
        _row(1, "JDBC Thin Client", terminal="unknown", machine="apphost01"),
        _row(2, "JDBC Thin Client", terminal="", machine="apphost02"),
        _row(3, "JDBC Thin Client", terminal="pts/0"),
    ], None)
    assert [row.terminal for row in picture.groups[0].sessions] == ["apphost01", "apphost02", "pts/0"]


def test_a_full_reading_says_it_may_be_cut_short() -> None:
    picture = sections.assemble_sessions([_row(index, "app.exe") for index in range(500)], None)
    assert picture.capped is True


def test_an_operator_sees_sessions_without_names_of_people_or_machines() -> None:
    picture = sections.assemble_sessions([
        _row(1, "sqlplus@dbhost01 (TNS V1-V3)", module="sqlplus@dbhost01 (TNS V1-V3)"),
        _row(2, "sqlplus@dbhost02 (TNS V1-V3)"),
        _row(3, "Toad.exe", "ACTIVE", username="SYS", sql_id="abc"),
    ], None)
    trimmed = sections.sessions_for_role(picture, admin=False)
    assert trimmed.detail is False and trimmed.detail_note == sections.SESSION_NOTE
    # Two hosts of the same program fold into one group once the host is left out.
    assert [group.program for group in trimmed.groups] == ["SQLPLUS (TNS V1-V3)", "TOAD.EXE"]
    assert trimmed.programs == 2 and trimmed.total == 3 and trimmed.active == 1
    for group in trimmed.groups:
        for row in group.sessions:
            assert row.restricted and not row.username and not row.os_user and not row.terminal
            assert "dbhost" not in row.program and "dbhost" not in row.module
    toad = trimmed.groups[1].sessions[0]
    assert toad.sid == 3 and toad.status == "ACTIVE" and toad.sql_id == "abc"
    assert sections.sessions_for_role(picture, admin=True) is picture


def test_a_missing_grant_or_a_failed_read_names_its_limitation() -> None:
    async def denied(settings, alias, kind):
        raise ActivityAccessError("SYS.V_$SESSION")

    async def failed(settings, alias, kind):
        raise ActivityUnavailable(1033)

    settings = _settings()
    picture = asyncio.run(sections.load_sessions(settings, "MY19C", denied))
    assert picture.groups == [] and picture.total == 0
    assert picture.limitation == "Grant SELECT on SYS.V_$SESSION to the monitoring account, then ask again."
    assert "1033" in (asyncio.run(sections.load_sessions(settings, "MY19C", failed)).limitation or "")


def test_the_reading_asks_for_the_session_list_only() -> None:
    asked: list[str] = []

    async def reader(settings, alias, kind):
        asked.append(kind)
        return [_row(1, "app.exe")], ""

    picture = asyncio.run(sections.load_sessions(_settings(), "MY19C", reader))
    assert asked == ["get_session_list"] and picture.total == 1


def _settings():
    from sentinel.config.settings import Settings

    return Settings(_env_file=None, database_url="sqlite://", synthetic_mode=False, scheduler_enabled=False)


def test_endpoint_is_open_to_every_role_and_trims_for_non_administrators(settings, sessions, make_target) -> None:
    target_id = make_target()
    client = _client(settings, sessions)
    url = f"/api/v1/databases/{target_id}/sessions"
    admin = client.get(url, headers=ADMIN)
    assert admin.status_code == 200
    body = admin.json()
    assert body["detail"] is True and body["total"] == 6 and body["active"] == 2
    assert any(row["username"] for group in body["groups"] for row in group["sessions"])
    assert "SQLPLUS@DBHOST01 (TNS V1-V3)" in [group["program"] for group in body["groups"]]
    for headers in (OPERATOR, VIEWER, DBA):
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        trimmed = response.json()
        assert trimmed["detail"] is False and trimmed["total"] == 6
        assert not any(row["username"] or row["os_user"] or row["terminal"] for group in trimmed["groups"] for row in group["sessions"])
        assert "SQLPLUS (TNS V1-V3)" in [group["program"] for group in trimmed["groups"]]
    assert client.get("/api/v1/databases/00000000-0000-0000-0000-000000000000/sessions", headers=ADMIN).status_code == 404


# ------------------------------------------------------------------ statement and plan of one session

def test_bound_readings_bind_only_numbers_and_sql_ids() -> None:
    (statement,) = CATALOG["get_session_statement"]
    (plan,) = CATALOG["get_statement_plan"]
    assert statement.binds == ("sid", "serial") and plan.binds == ("sql_id", "child")
    assert plan.object_name == "SYS.V_$SQL_PLAN" and statement.object_name == "SYS.V_$SQL"
    for query in (statement, plan):
        sql = query.sql.strip()
        assert sql.upper().startswith("SELECT") and ";" not in sql
        assert not re.search(r"(?i)\b(insert|update|delete|merge|alter|drop|create|grant|begin|execute|explain)\b", sql)
        assert set(re.findall(r":(\w+)", sql.replace("HH24:MI:SS", ""))) == set(query.binds)
        assert "ROWNUM <=" in sql
    assert "s.serial# = :serial" in statement.sql and "prev_sql_id" in statement.sql
    assert activity.bind_value_allowed(246) and activity.bind_value_allowed("5jm8qnb729z8u")
    for bad in (True, -1, 2**32, 1.5, "5jm8qnb729z8u' OR 1=1", "SELECT", "", "5JM8QNB729Z8U", None):
        assert not activity.bind_value_allowed(bad), bad


class _Cursor:
    def __init__(self, columns, rows, fail=None):
        self.description = [(name,) for name in columns]
        self._rows, self._fail, self.calls = rows, fail, []

    def execute(self, sql, binds=None):
        self.calls.append((sql, binds))
        if self._fail:
            raise self._fail

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _Lob:
    def __init__(self, text):
        self.text = text

    def read(self, offset, amount):
        return self.text[offset - 1: offset - 1 + amount]


def test_bound_collector_keeps_statement_lines_and_refuses_bad_values() -> None:
    text = "DELETE FROM LX_DTA.LX_FOL\n WHERE FOLMOD = 'J$BATCHCTL'\n" + "x" * 500
    cursor = _Cursor(["SQL_ID", "SQL_FULLTEXT", "CHILD_NUMBER"], [("gc77vs203t2v4", _Lob(text), 0)])

    class Connection:
        def cursor(self):
            return cursor

    rows, note = activity.collect_bound_rows(Connection(), "get_session_statement", {"sid": 246, "serial": 18731})
    assert rows[0]["sql_fulltext"] == text and rows[0]["child_number"] == "0" and note == ""
    assert cursor.calls[0][1] == {"sid": 246, "serial": 18731}
    for binds in ({"sid": 246}, {"sid": "246", "serial": 1}, {"sid": 246, "serial": 1, "x": 2}):
        try:
            activity.collect_bound_rows(Connection(), "get_session_statement", binds)
        except ValueError:
            continue
        raise AssertionError(binds)
    try:
        activity.collect_rows(Connection(), "get_session_statement")
    except ValueError:
        pass
    else:
        raise AssertionError("an unbound run of a bound reading must be refused")


def test_statement_is_current_when_active_and_previous_when_idle() -> None:
    settings = _synthetic_settings()
    active = asyncio.run(session_sql.load_statement(settings, "MY19C", 730, 55102))
    assert active.found and active.kind == "current" and active.sql_id == "5jm8qnb729z8u"
    assert active.text.startswith("SELECT") and active.text_note is None
    assert [step.operation for step in active.plan][:3] == ["SELECT STATEMENT", "SORT", "HASH JOIN"]
    assert active.plan[0].optimizer == "ALL_ROWS" and active.plan[0].cost == 14 and active.executions == 128
    idle = asyncio.run(session_sql.load_statement(settings, "MY19C", 246, 18731))
    assert idle.kind == "previous" and idle.text.startswith("DELETE")
    assert idle.text_note == "The session is idle. This is the last statement it ran."
    assert [(step.depth, step.operation, step.options) for step in idle.plan] == [
        (0, "DELETE STATEMENT", ""), (1, "DELETE", ""), (2, "TABLE ACCESS", "FULL")]
    aged = asyncio.run(session_sql.load_statement(settings, "MY19C", 312, 27741))
    assert aged.found and aged.text == "" and "no longer in the shared pool" in (aged.text_note or "")
    gone = asyncio.run(session_sql.load_statement(settings, "MY19C", 246, 1))
    assert not gone.found and "has ended" in (gone.text_note or "")


def test_missing_plan_grant_keeps_the_statement() -> None:
    async def reader(settings, alias, sid, serial):
        return ([{"sid": "246", "serial_no": "1", "status": "INACTIVE", "statement_kind": "previous",
                  "sql_id": "gc77vs203t2v4", "child_number": "0", "sql_fulltext": "DELETE FROM T"}], [],
                "Grant SELECT on SYS.V_$SQL_PLAN to the monitoring account to see the plan.")

    result = asyncio.run(session_sql.load_statement(_settings(), "MY19C", 246, 1, reader))
    assert result.text == "DELETE FROM T" and result.plan == []
    assert result.plan_note == "Grant SELECT on SYS.V_$SQL_PLAN to the monitoring account to see the plan."

    async def denied(settings, alias, sid, serial):
        raise ActivityAccessError("SYS.V_$SQL")

    refused = asyncio.run(session_sql.load_statement(_settings(), "MY19C", 246, 1, denied))
    assert not refused.found and refused.limitation.startswith("Grant SELECT on SYS.V_$SQL ")


def _synthetic_settings():
    from sentinel.config.settings import Settings

    return Settings(_env_file=None, database_url="sqlite://", synthetic_mode=True, scheduler_enabled=False)


def test_statement_endpoint_is_for_administrators_only(settings, sessions, make_target) -> None:
    target_id = make_target()
    client = _client(settings, sessions)
    listed = client.get(f"/api/v1/databases/{target_id}/sessions", headers=ADMIN).json()
    toad = next(group for group in listed["groups"] if group["program"] == "TOAD.EXE")
    active = next(row for row in toad["sessions"] if row["status"] == "ACTIVE")
    assert active["serial"] == 55102
    url = f"/api/v1/databases/{target_id}/sessions/{active['sid']}/statement?serial={active['serial']}"
    body = client.get(url, headers=ADMIN).json()
    assert body["found"] and body["kind"] == "current" and body["plan"]
    for headers in (OPERATOR, VIEWER, DBA):
        refused = client.get(url, headers=headers)
        assert refused.status_code == 403
        assert refused.json()["detail"] == session_sql.STATEMENT_NOTE
    assert client.get(f"/api/v1/databases/{target_id}/sessions/730/statement", headers=ADMIN).status_code == 422
    assert client.get(f"/api/v1/databases/{target_id}/sessions/-1/statement?serial=1", headers=ADMIN).status_code == 422
    assert client.get(f"/api/v1/databases/{target_id}/sessions/730/statement?serial=abc", headers=ADMIN).status_code == 422
