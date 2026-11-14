"""GraphDB activity readings stay on granted views and fail independently."""

import asyncio
import re

from sentinel.adapters.oracle.activity import CATALOG, ActivityAccessError
from sentinel.graph.activity import load_activity
from sentinel.graph.memory import load_memory


def test_session_sql_filters_type_and_server() -> None:
    sql = "\n".join(query.sql.lower() for query in CATALOG["get_server_sessions"])
    assert "type = 'user'" in sql
    assert "server = 'dedicated'" in sql
    assert "server = 'shared'" in sql
    assert "d000" in sql
    assert "(p0" in sql
    assert "(p1" in sql
    for banned in ("v$bgprocess", "v$process", "dba_hist_", "v$active_session_history", "v$sysmetric"):
        assert banned not in sql
    assert ":" not in sql


def test_segment_sql_is_limited_to_twenty_rows() -> None:
    queries = CATALOG["get_largest_segments"]
    sql = "\n".join(query.sql.lower() for query in queries)
    assert re.search(r"\bfrom dba_segments\b", sql)
    assert "rownum <= 20" in sql
    assert "v$tempseg_usage" not in sql
    assert ":" not in sql
    assert {query.object_name for query in queries} == {"SYS.DBA_SEGMENTS"}


def test_denied_sessions_leave_pga_and_name_the_view(settings) -> None:
    async def reader(_settings, _alias, kind):
        if kind == "get_server_sessions":
            raise ActivityAccessError("SYS.V_$SESSION")
        if kind == "get_service_uptime":
            return ([{"up_days": "1", "up_hours": "0", "up_minutes": "0"}], "")
        if kind == "get_service_database":
            return ([{"name": "ORCL"}], "")
        if kind == "get_service_pdb":
            return ([], "")
        if kind == "get_host_stats":
            return ([{"stat_name": "BUSY_TIME", "value": "1"}, {"stat_name": "IDLE_TIME", "value": "1"}], "")
        if kind == "get_job_queue":
            return ([{"running_jobs": "2"}], "")
        return ([], "")

    picture = asyncio.run(load_activity(settings, "PDBORCL", reader))
    users = {line.label: line.value for line in picture.service}
    assert "SYS.V_$SESSION" in users["Total users"]
    assert "SYS.V_$SESSION" in users["Active users"]
    assert users["Response"] == "Not collected."
    assert users["Average active"] == "Not collected."
    assert users["Pluggable database"] == "Not a pluggable database."
    dedicated = next(item for item in picture.server_processes if item.name == "Dedicated")
    jobs = next(item for item in picture.server_processes if item.name == "Job queue")
    assert dedicated.count is None
    assert jobs.count == 2
    assert "EM Agent" not in {item.name for item in picture.server_processes}
    memory = asyncio.run(load_memory(settings, "PDBORCL"))
    assert any(item.name == "Target" and item.display == "2 GB" for item in memory.pga.items)
    assert picture.host.cpu_caption == "Since instance startup."
    assert picture.host.run_queue is None
    assert picture.host.free_memory is None


def test_rate_counters_use_only_sysstat() -> None:
    queries = CATALOG["get_rate_counters"]
    sql = "\n".join(query.sql.lower() for query in queries)
    assert "from v$sysstat" in sql
    for name in ("physical reads", "redo entries", "parse count (total)", "execute count"):
        assert name in sql
    for banned in ("v$sysmetric", "v$active_session_history", "dba_hist_", "v$bgprocess"):
        assert banned not in sql
    assert ":" not in sql
    assert {query.object_name for query in queries} == {"SYS.V_$SYSSTAT"}
