"""Phase 25: MemoryDB, StorageDB, ParameterDB, and AlertDB."""

import asyncio
import importlib
import re
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from sentinel.adapters.oracle.activity import CATALOG, ActivityAccessError, ActivityUnavailable
from sentinel.graph import sections

OPERATOR = {"X-Sentinel-Role": "operator", "X-Sentinel-Principal": "olga"}
VIEWER = {"X-Sentinel-Role": "viewer", "X-Sentinel-Principal": "vera"}
ADMIN = {"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"}
KINDS = ("get_sga_breakdown", "get_tablespace_utilization", "get_parameters", "get_alert_tail")
GRANTS = Path(__file__).resolve().parents[3] / "scripts" / "oracle" / "02_grant_readonly_monitoring.sql"


def _client(settings, sessions) -> TestClient:
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)
    client = TestClient(app)
    client.__enter__()
    app.state.session_factory = sessions
    return client


def _sql(kind: str) -> str:
    return "\n".join(query.sql for query in CATALOG[kind])


def test_the_four_readings_are_fixed_read_only_statements_on_views_already_granted() -> None:
    granted = set(re.findall(r"ON\s+(SYS\.[A-Z_$]+)", GRANTS.read_text(encoding="utf-8").upper()))
    for kind in KINDS:
        for query in CATALOG[kind]:
            statement = query.sql.strip()
            assert statement.upper().startswith("SELECT"), kind
            assert ";" not in statement and ":" not in statement.replace("HH24:MI:SS", ""), kind
            assert not re.search(r"(?i)\b(insert|update|delete|merge|alter|drop|create|grant|begin|execute)\b", statement), kind
            assert "ROWNUM <=" in statement.upper(), kind
            assert query.object_name in granted, (kind, query.object_name)
    assert {query.object_name for query in CATALOG["get_sga_breakdown"]} == {"SYS.V_$SGASTAT"}
    spaces = _sql("get_tablespace_utilization").lower()
    for view in ("dba_tablespaces", "dba_data_files", "dba_temp_files", "dba_free_space", "v$temp_space_header"):
        assert view in spaces
    alert = _sql("get_alert_tail").lower()
    assert "rdbms%alert%log.xml" in alert and "tnslsnr" in alert and "interval '7' day" in alert
    assert "rownum <= 300" in alert
    version = next(query for query in CATALOG["get_parameters"] if query.source == "v$version")
    assert version.required is False


def test_sga_is_grouped_by_pool_with_used_and_free() -> None:
    picture = asyncio.run(sections.load_sga_breakdown(_synthetic(), "PDBORCL"))
    pools = {pool.name: pool for pool in picture.pools}
    assert list(pools) == ["buffer cache", "shared pool", "large pool", "other"]
    assert pools["buffer cache"].bytes == 956301312 + 83886080
    assert pools["buffer cache"].used_percent == 100.0
    assert (pools["shared pool"].free_bytes, pools["shared pool"].used_percent) == (49183744, 68.7)
    assert pools["other"].bytes == 5032960 + 2265088
    assert picture.total_bytes == sum(pool.bytes for pool in picture.pools)
    assert picture.total_display == "1.1 GB"
    assert [item.name for item in picture.components][:3] == ["buffer_cache", "shared_io_pool", "free memory"]
    assert picture.components[0].pool == "buffer cache" and picture.components[2].pool == "shared pool"
    assert [item.name for item in picture.shared_pool] == ["free memory", "SQLA", "KGLH0", "KGLS"]
    assert {item.pool for item in picture.shared_pool} == {"shared pool"}


def test_sga_rows_that_cannot_be_used_are_left_out() -> None:
    rows = [
        {"source": "v$sgastat_pools", "pool": "shared pool", "name": "", "bytes": "100", "free_bytes": "900"},
        {"source": "v$sgastat_pools", "pool": "In-Memory Area", "name": "", "bytes": "50", "free_bytes": "0"},
        {"source": "v$sgastat_pools", "pool": "java pool", "name": "", "bytes": "", "free_bytes": "0"},
        {"source": "v$sgastat", "pool": "-", "name": "", "bytes": "10"},
        {"source": "v$sgastat", "pool": "-", "name": "mystery", "bytes": "0"},
    ]
    picture = sections.assemble_sga(rows, None)
    assert [(pool.name, pool.free_bytes, pool.used_percent) for pool in picture.pools] == [
        ("shared pool", 100, 0.0), ("in-memory", 0, 100.0),
    ]
    assert picture.components == []
    denied = sections.assemble_sga(rows, "Grant SELECT on SYS.V_$SGASTAT to the monitoring account, then ask again.")
    assert (denied.pools, denied.components, denied.total_bytes, denied.total_display) == ([], [], 0, "")


def test_tablespaces_report_used_free_percent_and_fragments() -> None:
    picture = asyncio.run(sections.load_tablespaces(_synthetic(), "PDBORCL"))
    rows = {row.name: row for row in picture.tablespaces}
    assert list(rows) == ["SYSAUX", "SYSTEM", "TEMP", "UNDOTBS1", "USERS"]
    assert (rows["SYSAUX"].used_percent, rows["SYSAUX"].fragments, rows["SYSAUX"].contents) == (93.9, 1, "PERMANENT")
    assert rows["SYSAUX"].used_bytes == 643825664 - 39321600
    assert (rows["TEMP"].used_bytes, rows["TEMP"].used_percent, rows["TEMP"].fragments) == (0, 0.0, None)
    assert rows["UNDOTBS1"].fragments == 52
    assert picture.total_bytes == sum(row.total_bytes for row in picture.tablespaces)
    assert picture.used_bytes == sum(row.used_bytes for row in picture.tablespaces)
    odd = sections.assemble_tablespaces([
        {"tablespace_name": "BIG", "contents": "permanent", "status": "online", "bytes_total": "100", "bytes_free": "250", "fragments": "3"},
        {"tablespace_name": "NOSIZE", "contents": "PERMANENT", "status": "OFFLINE", "bytes_total": "", "bytes_free": "", "fragments": ""},
        {"tablespace_name": "", "bytes_total": "5"},
    ], None)
    assert [(row.name, row.free_bytes, row.used_percent, row.status) for row in odd.tablespaces] == [
        ("BIG", 100, 0.0, "ONLINE"), ("NOSIZE", None, None, "OFFLINE"),
    ]


def test_parameters_are_listed_with_their_default_flag_and_version() -> None:
    picture = asyncio.run(sections.load_parameters(_synthetic(), "PDBORCL"))
    assert picture.version.startswith("Oracle Database 19c")
    assert (picture.total, picture.changed) == (7, 5)
    assert [item.name for item in picture.parameters] == sorted(item.name for item in picture.parameters)
    by_name = {item.name: item for item in picture.parameters}
    assert (by_name["undo_retention"].is_default, by_name["open_cursors"].is_default) == (True, False)
    assert by_name["processes"].sys_modifiable == "FALSE"
    assert picture.detail is True and picture.detail_note is None


def test_a_non_administrator_does_not_see_paths_or_addresses_in_parameter_values() -> None:
    picture = asyncio.run(sections.load_parameters(_synthetic(), "PDBORCL"))
    trimmed = sections.parameters_for_role(picture, False)
    by_name = {item.name: item for item in trimmed.parameters}
    assert (by_name["audit_file_dest"].value, by_name["audit_file_dest"].restricted) == ("Shown to an administrator.", True)
    assert by_name["local_listener"].restricted is True
    assert (by_name["open_cursors"].value, by_name["open_cursors"].restricted) == ("300", False)
    assert trimmed.detail is False and trimmed.detail_note == sections.PARAMETER_NOTE
    assert "/u01" not in trimmed.model_dump_json() and "dbhost01" not in trimmed.model_dump_json()
    assert sections.parameters_for_role(picture, True) is picture
    for value in ("/u01/app", "C:\\oracle\\dbs\\spfile.ora", "+DATA/orcl/control01.ctl", "(DESCRIPTION=(ADDRESS=(HOST=h)))", "\\\\share\\dump"):
        assert sections.holds_location(value), value
    for value in ("300", "TRUE", "8G", "", "partial", "EXCLUSIVE", "TYPICAL"):
        assert not sections.holds_location(value), value


def test_the_alert_log_reads_oldest_first_with_errors_and_warnings_marked() -> None:
    picture = asyncio.run(sections.load_alert_tail(_synthetic(), "PDBORCL"))
    assert [line.at for line in picture.lines] == sorted(line.at for line in picture.lines)
    assert [line.severity for line in picture.lines] == ["error", "warning", "info", "info", "info"]
    assert picture.lines[0].codes == ["ORA-01578"]
    assert (picture.errors, picture.warnings) == (1, 1)
    assert picture.window == "The 300 most recent lines of the last 7 days."
    rows = [
        {"occurred_at": "2026-11-15 10:00:00", "message_type": "1", "message_text": "Thread 1 advanced to log sequence 20"},
        {"occurred_at": "2026-11-15 09:00:00", "message_type": "3", "message_text": "Process   termination requested"},
        {"occurred_at": "2026-11-15 08:00:00", "message_type": "1", "message_text": "Errors in file x: ORA-00600 and ORA-07445 and ORA-00600"},
        {"occurred_at": "2026-11-15 07:00:00", "message_type": "1", "message_text": "   "},
    ]
    built = sections.assemble_alert_tail(rows, None)
    assert [(line.severity, line.codes) for line in built.lines] == [
        ("error", ["ORA-00600", "ORA-07445"]), ("error", []), ("info", []),
    ]
    assert built.lines[1].text == "Process termination requested"


def test_a_non_administrator_reads_the_alert_log_without_file_paths() -> None:
    picture = asyncio.run(sections.load_alert_tail(_synthetic(), "PDBORCL"))
    trimmed = sections.alert_tail_for_role(picture, False)
    resize = next(line for line in trimmed.lines if line.text.startswith("Resize operation"))
    assert resize.text == (
        "Resize operation completed for file# 3, fname (path shown to an administrator), old size 870400K, new size 880640K"
    )
    assert "/u01" not in trimmed.model_dump_json()
    assert trimmed.detail is False and trimmed.detail_note == sections.ALERT_NOTE
    assert any("ORA-01578" in line.text for line in trimmed.lines)
    assert sections.alert_tail_for_role(picture, True) is picture


def test_a_reading_that_is_denied_or_fails_names_its_limitation() -> None:
    async def denied(_settings, _alias, kind):
        raise ActivityAccessError({"get_sga_breakdown": "SYS.V_$SGASTAT", "get_alert_tail": "SYS.V_$DIAG_ALERT_EXT"}.get(kind, "SYS.V_$PARAMETER"))

    async def failed(_settings, _alias, _kind):
        raise ActivityUnavailable(12541)

    settings = _synthetic()
    sga = asyncio.run(sections.load_sga_breakdown(settings, "PDBORCL", denied))
    assert sga.limitation == "Grant SELECT on SYS.V_$SGASTAT to the monitoring account, then ask again."
    assert sga.pools == []
    alert = asyncio.run(sections.load_alert_tail(settings, "PDBORCL", denied))
    assert "SYS.V_$DIAG_ALERT_EXT" in alert.limitation and alert.lines == []
    spaces = asyncio.run(sections.load_tablespaces(settings, "PDBORCL", failed))
    assert spaces.limitation == "The database could not be read just now. Oracle returned error 12541."
    params = asyncio.run(sections.load_parameters(settings, "PDBORCL", failed))
    assert (params.parameters, params.total, params.changed) == ([], 0, 0)


def test_each_loader_asks_for_one_fixed_reading() -> None:
    asked: list[str] = []

    async def reader(_settings, alias, kind):
        asked.append(f"{alias}:{kind}")
        return [], ""

    settings = _synthetic()
    for loader in (sections.load_sga_breakdown, sections.load_tablespaces, sections.load_parameters, sections.load_alert_tail):
        asyncio.run(loader(settings, "PDBORCL", reader))
    assert asked == [f"PDBORCL:{kind}" for kind in KINDS]


def test_the_four_routes_are_open_to_every_role_and_name_no_sql(settings, sessions, make_target) -> None:
    target_id = make_target(alias="PDBORCL")
    retired = make_target(alias="RETIRED", enabled=False)
    paths = ("memory/sga", "storage/tablespaces", "parameters", "alert-log")
    client = _client(settings, sessions)
    try:
        bodies = {}
        for path in paths:
            for headers in (OPERATOR, VIEWER, ADMIN):
                response = client.get(f"/api/v1/databases/{target_id}/{path}", headers=headers)
                assert response.status_code == 200, (path, headers)
                assert "select " not in response.text.lower() and "v$" not in response.text.lower()
                bodies[(path, headers["X-Sentinel-Role"])] = response.json()
            assert client.get(f"/api/v1/databases/{retired}/{path}", headers=ADMIN).status_code == 404
            assert client.get(f"/api/v1/databases/00000000-0000-0000-0000-000000000000/{path}", headers=ADMIN).status_code == 404
            unknown = client.get(
                f"/api/v1/databases/{target_id}/{path}", headers={"X-Sentinel-Role": "guest", "X-Sentinel-Principal": "pat"},
            )
            assert unknown.status_code == 400
    finally:
        client.__exit__(None, None, None)
    assert bodies[("memory/sga", "operator")]["pools"] == bodies[("memory/sga", "admin")]["pools"]
    assert bodies[("storage/tablespaces", "viewer")]["tablespaces"][0]["name"] == "SYSAUX"
    admin_parameters = {item["name"]: item for item in bodies[("parameters", "admin")]["parameters"]}
    operator_parameters = {item["name"]: item for item in bodies[("parameters", "operator")]["parameters"]}
    assert admin_parameters["audit_file_dest"]["value"].startswith("/u01/")
    assert operator_parameters["audit_file_dest"] == {**admin_parameters["audit_file_dest"], "value": "Shown to an administrator.", "restricted": True}
    assert (bodies[("parameters", "admin")]["detail"], bodies[("parameters", "operator")]["detail"]) == (True, False)
    assert "/u01/oradata" in str(bodies[("alert-log", "admin")]["lines"])
    assert "/u01/oradata" not in str(bodies[("alert-log", "operator")]["lines"])
    assert bodies[("alert-log", "viewer")]["detail_note"] == sections.ALERT_NOTE


def test_a_picture_uses_one_oracle_session(settings, sessions, make_target, monkeypatch) -> None:
    from contextlib import asynccontextmanager

    from sentinel.api.v1 import graph as graph_api

    target_id = make_target(alias="PDBORCL")
    opened: list[str] = []
    asked: list[str] = []

    @asynccontextmanager
    async def shared(_settings, alias):
        opened.append(alias)

        async def reader(_settings_, _alias, kind):
            asked.append(kind)
            return [], ""

        yield reader

    monkeypatch.setattr(graph_api, "shared_reader", shared)
    live = settings.model_copy(update={"synthetic_mode": False})
    client = _client(live, sessions)
    try:
        for path in ("memory/sga", "storage/tablespaces", "parameters", "alert-log"):
            assert client.get(f"/api/v1/databases/{target_id}/{path}", headers=OPERATOR).status_code == 200
    finally:
        client.__exit__(None, None, None)
    assert opened == ["PDBORCL"] * 4
    assert asked == list(KINDS)


def _synthetic():
    from sentinel.config.settings import Settings

    return Settings(_env_file=None, database_url="sqlite://", synthetic_mode=True, scheduler_enabled=False)
