"""GraphDB is open to every signed-in role. A non-administrator sees the storage summary."""

import importlib
from unittest.mock import patch

from fastapi.testclient import TestClient

from sentinel.adapters.oracle.activity import ActivityAccessError
from sentinel.api.v1 import graph as graph_api
from sentinel.graph.memory import load_memory
from sentinel.graph.storage import load_storage


def _client(settings, sessions) -> TestClient:
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)
    client = TestClient(app)
    client.__enter__()
    app.state.session_factory = sessions
    return client


def test_graph_shell_is_open_to_every_role_and_has_no_measurements(settings, sessions) -> None:
    client = _client(settings, sessions)
    try:
        others = {
            role: client.get("/api/v1/graph", headers={"X-Sentinel-Role": role, "X-Sentinel-Principal": "pat"})
            for role in ("operator", "dba", "auditor", "viewer")
        }
        allowed = client.get("/api/v1/graph", headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"})
        platform = client.get(
            "/api/v1/graph", headers={"X-Sentinel-Role": "platform_admin", "X-Sentinel-Principal": "sentinel"},
        )
        unknown = client.get("/api/v1/graph", headers={"X-Sentinel-Role": "guest", "X-Sentinel-Principal": "pat"})
    finally:
        client.__exit__(None, None, None)
    assert {role: response.status_code for role, response in others.items()} == {
        "operator": 200, "dba": 200, "auditor": 200, "viewer": 200,
    }
    assert all(response.json()["detail"] is False for response in others.values())
    assert allowed.status_code == 200
    assert platform.status_code == 200
    assert unknown.status_code == 400
    body = allowed.json()
    assert body["detail"] is True
    assert [panel["title"] for panel in body["panels"]] == [
        "Memory structures", "Physical structures", "Logical structures", "Management",
    ]
    assert {panel["message"] for panel in body["panels"]} == {"Not collected in this phase."}
    assert "value" not in body
    assert "sql" not in body


def test_memory_is_open_to_an_operator(settings, sessions, make_target) -> None:
    target_id = make_target(alias="PDBORCL")
    client = _client(settings, sessions)
    try:
        operator = client.get(
            f"/api/v1/databases/{target_id}/graph/memory",
            headers={"X-Sentinel-Role": "operator", "X-Sentinel-Principal": "pat"},
        )
        allowed = client.get(
            f"/api/v1/databases/{target_id}/graph/memory",
            headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"},
        )
        missing = client.get(
            "/api/v1/databases/00000000-0000-0000-0000-000000000000/graph/memory",
            headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"},
        )
        disabled_id = make_target(alias="RETIRED", enabled=False)
        disabled = client.get(
            f"/api/v1/databases/{disabled_id}/graph/memory",
            headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"},
        )
    finally:
        client.__exit__(None, None, None)
    assert operator.status_code == 200
    assert operator.json()["sga"] == allowed.json()["sga"]
    assert allowed.status_code == 200
    body = allowed.json()
    assert [item["name"] for item in body["sga"]["items"]] == ["Buffer Cache Size", "Shared Pool Size"]
    assert any(item["name"] == "Target" and item["display"] == "2 GB" for item in body["pga"]["items"])
    assert body["background_processes"][0]["program"] == "oracle (PMON)"
    assert body["snapshot"] == "Current instance snapshot."
    assert "v$" not in allowed.text.lower()
    assert "select" not in allowed.text.lower()
    assert missing.status_code == 404
    assert disabled.status_code == 404


def test_storage_summary_for_an_operator_and_detail_for_an_administrator(settings, sessions, make_target) -> None:
    target_id = make_target(alias="PDBORCL")
    client = _client(settings, sessions)
    try:
        operator = client.get(
            f"/api/v1/databases/{target_id}/graph/storage",
            headers={"X-Sentinel-Role": "operator", "X-Sentinel-Principal": "pat"},
        )
        allowed = client.get(
            f"/api/v1/databases/{target_id}/graph/storage",
            headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"},
        )
        missing = client.get(
            "/api/v1/databases/00000000-0000-0000-0000-000000000000/graph/storage",
            headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"},
        )
    finally:
        client.__exit__(None, None, None)
    assert operator.status_code == 200
    summary = operator.json()
    assert summary["detail"] is False
    assert summary["detail_note"] == "File, control-file, redo-member, and segment listings are shown to an administrator."
    assert summary["data_files"]["files"] == []
    assert summary["temp_files"]["files"] == []
    assert summary["control_files"]["files"] == []
    assert summary["control_files"]["count"] == 1
    assert summary["redo_logs"]["members"] == []
    assert summary["segments"]["rows"] == []
    assert summary["data_files"]["used_percent"] == 90
    assert summary["data_files"]["online_files"] == 2
    assert summary["redo_logs"]["groups"] == 2
    assert summary["recovery_area"]["present"] is True
    assert "SYSTEM01" not in operator.text
    assert "REDO01" not in operator.text
    assert "ORDERS" not in operator.text
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["detail"] is True
    assert body["detail_note"] is None
    assert body["data_files"]["files"][0]["name"].endswith("SYSTEM01.DBF")
    assert body["data_files"]["files"][1]["online"] is False
    assert body["data_files"]["used_display"] == "9 MB"
    assert body["data_files"]["used_percent"] == 90
    assert body["data_files"]["flash_cache"] == "Not collected."
    assert body["redo_logs"]["members"][0]["current"] is True
    assert body["redo_logs"]["groups"] == 2
    assert body["control_files"]["files"][0]["status"] == "The status was not returned."
    lowered = allowed.text.lower()
    assert "sql" not in body
    assert "from v$" not in lowered
    assert "select" not in lowered
    assert missing.status_code == 404


def test_activity_is_open_to_a_viewer_and_leaves_memory(settings, sessions, make_target) -> None:
    target_id = make_target(alias="PDBORCL")
    client = _client(settings, sessions)
    try:
        viewer = client.get(
            f"/api/v1/databases/{target_id}/graph/activity",
            headers={"X-Sentinel-Role": "viewer", "X-Sentinel-Principal": "pat"},
        )
        allowed = client.get(
            f"/api/v1/databases/{target_id}/graph/activity",
            headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"},
        )
        memory = client.get(
            f"/api/v1/databases/{target_id}/graph/memory",
            headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"},
        )
    finally:
        client.__exit__(None, None, None)
    assert viewer.status_code == 200
    assert viewer.json()["service"] == allowed.json()["service"]
    assert allowed.status_code == 200
    body = allowed.json()
    values = {line["label"]: line["value"] for line in body["service"]}
    assert values["Uptime"] == "2d 11h 0m"
    assert values["Container database"] == "ORCL12C"
    assert values["Pluggable database"] == "PDBORCL"
    assert values["Response"] == "Not collected."
    assert values["Average active"] == "Not collected."
    assert values["Total users"] == "3"
    assert body["host"]["cpu_caption"] == "Since instance startup."
    assert body["host"]["cpu_percent"] == 12
    assert [item["name"] for item in body["server_processes"]] == [
        "Dedicated", "Shared", "Dispatchers", "Parallel query", "Job queue", "Total",
    ]
    assert [item["name"] for item in body["counters"]] == [
        "physical reads", "redo entries", "parse count (total)", "execute count",
    ]
    assert [item["value"] for item in body["counters"]] == [100, 40, 20, 80]
    assert "from v$" not in allowed.text.lower()
    assert memory.status_code == 200
    assert any(item["name"] == "Target" and item["display"] == "2 GB" for item in memory.json()["pga"]["items"])


def test_denied_sgastat_still_returns_buffer_cache(settings, sessions, make_target, monkeypatch) -> None:
    async def reader(_settings, _alias, kind):
        if kind == "get_shared_pool_free":
            raise ActivityAccessError("SYS.V_$SGASTAT")
        if kind == "get_sga_components":
            return ([{"source": "v$sgainfo", "name": "Buffer Cache Size", "bytes": "1048576"}], "")
        return ([], "")

    async def load(current, alias, _reader=None):
        return await load_memory(current, alias, reader)

    monkeypatch.setattr(graph_api, "load_memory", load)
    target_id = make_target(alias="PDBORCL")
    client = _client(settings, sessions)
    try:
        allowed = client.get(
            f"/api/v1/databases/{target_id}/graph/memory",
            headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"},
        )
    finally:
        client.__exit__(None, None, None)
    assert allowed.status_code == 200
    body = allowed.json()
    assert [item["name"] for item in body["sga"]["items"]] == ["Buffer Cache Size"]
    assert body["shared_pool_used"]["caption"] == "V$SGASTAT is not granted."
    assert body["shared_pool_used"]["percent"] is None
    assert body["result_cache"] == "Not collected."
    assert "from v$" not in allowed.text.lower()


def test_empty_rman_job_still_returns_the_recovery_area(settings, sessions, make_target, monkeypatch) -> None:
    async def reader(_settings, _alias, kind):
        if kind == "get_recovery_area":
            return ([{
                "source": "v$recovery_file_dest", "name": "FRA",
                "space_limit": "104857600", "space_used": "20971520", "used_pct": "20",
            }], "")
        return ([], "")

    async def load(current, alias, _reader=None):
        return await load_storage(current, alias, reader)

    monkeypatch.setattr(graph_api, "load_storage", load)
    target_id = make_target(alias="PDBORCL")
    client = _client(settings, sessions)
    try:
        allowed = client.get(
            f"/api/v1/databases/{target_id}/graph/storage",
            headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"},
        )
    finally:
        client.__exit__(None, None, None)
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["recovery_area"]["present"] is True
    assert body["recovery_area"]["used_percent"] == 20
    assert body["backup"]["limitation"] == "No RMAN job was returned."
    assert body["backup"]["failed"] is False
    assert body["archive"]["time_to_failure"] == "Not collected."
    assert "from v$" not in allowed.text.lower()


def test_pictures_share_one_oracle_session_per_request(settings, sessions, make_target, monkeypatch) -> None:
    from sentinel.adapters.oracle import activity as activity_module
    from sentinel.api.v1 import graph as graph_module

    opened: list[str] = []
    closed: list[str] = []
    kinds: list[str] = []

    class FakeConnection:
        pass

    class FakeManager:
        def __init__(self, _settings) -> None:
            pass

        async def connect(self, alias: str) -> FakeConnection:
            opened.append(alias)
            return FakeConnection()

        async def close_connection(self, _connection) -> None:
            closed.append("closed")

    def collect(_connection, kind):
        kinds.append(kind)
        return [], ""

    live = settings.model_copy(update={"synthetic_mode": False})
    monkeypatch.setattr(activity_module, "collect_rows", collect)
    monkeypatch.setattr("sentinel.adapters.oracle.connection.OracleConnectionManager", FakeManager)
    target_id = make_target(alias="PDBORCL")
    client = _client(settings, sessions)
    client.app.dependency_overrides[graph_module.runtime_settings] = lambda: live
    try:
        response = client.get(
            f"/api/v1/databases/{target_id}/graph/storage",
            headers={"X-Sentinel-Role": "dba", "X-Sentinel-Principal": "pat"},
        )
    finally:
        client.__exit__(None, None, None)
    assert response.status_code == 200
    assert opened == ["PDBORCL"]
    assert closed == ["closed"]
    assert len(kinds) == 8
    assert response.json()["detail"] is False


def test_failed_logon_reports_every_card_and_does_not_error(settings, sessions, make_target, monkeypatch) -> None:
    import oracledb

    from sentinel.api.v1 import graph as graph_module

    class FailingManager:
        def __init__(self, _settings) -> None:
            pass

        async def connect(self, _alias: str):
            raise oracledb.Error("ORA-12541: TNS:no listener")

        async def close_connection(self, _connection) -> None:
            raise AssertionError("nothing to close")

    live = settings.model_copy(update={"synthetic_mode": False})
    monkeypatch.setattr("sentinel.adapters.oracle.connection.OracleConnectionManager", FailingManager)
    target_id = make_target(alias="PDBORCL")
    client = _client(settings, sessions)
    client.app.dependency_overrides[graph_module.runtime_settings] = lambda: live
    try:
        response = client.get(
            f"/api/v1/databases/{target_id}/graph/memory",
            headers={"X-Sentinel-Role": "operator", "X-Sentinel-Principal": "pat"},
        )
    finally:
        client.__exit__(None, None, None)
    assert response.status_code == 200
    body = response.json()
    assert body["sga"]["items"] == []
    assert any("12541" in item for item in body["limitations"])
    assert "no listener" not in response.text.lower()
