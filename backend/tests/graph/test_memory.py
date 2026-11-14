"""GraphDB memory readings stay on the granted views and fail independently."""

import asyncio

from sentinel.adapters.oracle.activity import CATALOG, ActivityAccessError
from sentinel.graph.memory import load_memory


def test_memory_sql_uses_only_granted_views() -> None:
    kinds = ("get_sga_components", "get_pga_status", "get_background_processes")
    sql = "\n".join(query.sql.lower() for kind in kinds for query in CATALOG[kind])
    assert "from v$sgainfo" in sql
    assert "from v$pgastat" in sql
    assert "from v$parameter" in sql
    assert "from v$session" in sql
    assert "type = 'background'" in sql
    for banned in ("v$bgprocess", "v$process", "dba_hist_", "v$active_session_history", "addm"):
        assert banned not in sql
    assert ":" not in sql


def test_one_missing_grant_leaves_the_other_cards(settings) -> None:
    async def reader(_settings, _alias, kind):
        if kind == "get_pga_status":
            raise ActivityAccessError("SYS.V_$PGASTAT")
        if kind == "get_sga_components":
            return ([{"source": "v$sgainfo", "name": "Buffer Cache Size", "bytes": "1048576"}], "")
        return ([{"program": "", "status": "ACTIVE", "state": "WAITING"}], "")

    picture = asyncio.run(load_memory(settings, "PDBORCL", reader))
    assert [item.name for item in picture.sga.items] == ["Buffer Cache Size"]
    assert picture.pga.items == []
    assert any("SYS.V_$PGASTAT" in item for item in picture.limitations)
    assert picture.background_processes[0].program == "The process name was not returned."
    assert any(item.name == "Buffer Cache Size" for item in picture.sga.items)


def test_hit_ratio_and_shared_pool_use_separate_views() -> None:
    hit = "\n".join(query.sql.lower() for query in CATALOG["get_buffer_hit_ratio"])
    shared = "\n".join(query.sql.lower() for query in CATALOG["get_shared_pool_free"])
    assert "from v$sysstat" in hit
    assert "v$sgastat" not in hit
    assert "from v$sgastat" in shared
    assert "v$sysstat" not in shared
    assert "v$sgainfo" not in shared
    for banned in ("v$bgprocess", "v$process", "dba_hist_", "v$active_session_history", "v$sysmetric", "result_cache"):
        assert banned not in hit
        assert banned not in shared
    assert ":" not in hit
    assert ":" not in shared


def test_denied_sgastat_keeps_the_buffer_cache(settings) -> None:
    async def reader(_settings, _alias, kind):
        if kind == "get_shared_pool_free":
            raise ActivityAccessError("SYS.V_$SGASTAT")
        if kind == "get_sga_components":
            return ([
                {"source": "v$sgainfo", "name": "Buffer Cache Size", "bytes": "1048576"},
                {"source": "v$sgainfo", "name": "Shared Pool Size", "bytes": "2097152"},
            ], "")
        return ([], "")

    picture = asyncio.run(load_memory(settings, "PDBORCL", reader))
    assert [item.name for item in picture.sga.items] == ["Buffer Cache Size", "Shared Pool Size"]
    assert picture.shared_pool_used.percent is None
    assert picture.shared_pool_used.caption == "V$SGASTAT is not granted."
    assert picture.hit_ratio.caption == "Not collected."
    assert picture.hit_ratio.percent is None
    assert picture.result_cache == "Not collected."
    assert picture.data_guard.limitation == "Not a Data Guard database."
