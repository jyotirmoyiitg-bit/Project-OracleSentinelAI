"""WaitDB readings use granted views, plus the lock and buffer-class views."""

from sentinel.adapters.oracle.activity import CATALOG
from sentinel.graph.wait_activity import assemble


def _sql(kind: str) -> str:
    return "\n".join(query.sql.lower() for query in CATALOG[kind])


def test_wait_activity_sql_uses_named_views_and_has_no_bind():
    locks = _sql("get_enqueue_stats")
    blocking = _sql("get_blocking_locks")
    busy = _sql("get_buffer_busy")
    classes = _sql("get_buffer_classes")
    users = _sql("get_buffer_users")
    files = _sql("get_file_io")
    assert "v$enqueue_statistics" in locks and "rownum <= 12" in locks
    assert "v$lock" in blocking and "l.request > 0" in blocking and "dba_objects" in blocking
    assert "buffer busy waits" in busy and "read by other session" in busy
    assert "v$waitstat" in classes
    assert "v$session_event" in users
    assert "v$filestat" in files
    combined = f"{locks}\n{blocking}\n{busy}\n{classes}\n{users}\n{files}\n{_sql('get_io_counters')}"
    assert ":" not in combined


def test_a_failed_lock_reading_leaves_blocking_and_buffer_class():
    picture = assemble(
        {
            "get_enqueue_stats": [],
            "get_blocking_locks": [{
                "sid": "9", "serial_no": "1", "username": "APPUSER", "pid": "44",
                "lock_type": "TM", "mode_wanted": "3", "object_name": "ORDERS",
            }],
            "get_buffer_classes": [{"class_name": "data block", "wait_count": "4", "wait_time": "8"}],
            "get_buffer_busy": [{"event": "buffer busy waits", "wait_class": "Concurrency", "total_waits": "3", "time_waited_micro": "100"}],
        },
        {"get_enqueue_stats": "Grant SELECT on SYS.V_$ENQUEUE_STATISTICS to the monitoring account, then ask again."},
    )
    assert picture.locking.locks == []
    assert picture.locking.limitation is not None
    assert picture.blocking.locks[0].mode_wanted == "Row-X (SX)"
    assert picture.blocking.locks[0].object_name == "ORDERS"
    assert picture.blocking.empty == "No data"
    assert picture.buffer_busy.classes[0].name == "data block"
    assert picture.buffer_busy.events[0].event == "buffer busy waits"
