"""Wait readings use the already granted system event and time model views."""

from sentinel.adapters.oracle.activity import CATALOG
from sentinel.graph.waits import assemble


def test_wait_sql_excludes_idle_events_and_has_no_bind():
    sql = "\n".join(query.sql.lower() for query in CATALOG["get_wait_events"])
    assert "v$system_event" in sql
    assert "wait_class <> 'idle'" in sql
    assert "rownum <= 15" in sql
    assert ":" not in sql
    cpu = "\n".join(query.sql.lower() for query in CATALOG["get_wait_cpu"])
    assert "v$sys_time_model" in cpu
    assert "db cpu" in cpu
    assert ":" not in cpu


def test_assemble_keeps_events_when_cpu_is_not_granted():
    picture = assemble(
        [{"event": "db file sequential read", "wait_class": "User I/O", "total_waits": "4", "time_waited_micro": "1000"}],
        [],
        [None, "Grant SELECT on SYS.V_$SYS_TIME_MODEL to the monitoring account, then ask again."],
    )
    assert picture.events[0].event == "db file sequential read"
    assert picture.events[0].total_waits == 4
    assert picture.cpu_micro is None
    assert picture.caption.startswith("Totals are since instance startup.")
    assert any("V_$SYS_TIME_MODEL" in note for note in picture.limitations)
