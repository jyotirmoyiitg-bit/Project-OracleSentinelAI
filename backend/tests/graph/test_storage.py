"""GraphDB storage readings stay on the granted views and fail independently."""

import asyncio
import re

from sentinel.adapters.oracle.activity import CATALOG, ActivityAccessError
from sentinel.graph.storage import load_storage


def test_storage_sql_uses_only_granted_views() -> None:
    kinds = ("get_data_files", "get_temp_files", "get_control_files", "get_redo_logs")
    sql = "\n".join(query.sql.lower() for kind in kinds for query in CATALOG[kind])
    for required in ("dba_data_files", "dba_temp_files", "dba_free_space", "v$controlfile", "v$log", "v$logfile"):
        assert re.search(rf"\bfrom {re.escape(required)}\b", sql)
    for banned in ("v$bgprocess", "v$process", "dba_hist_", "v$active_session_history", "addm", "v$sysmetric"):
        assert banned not in sql
    assert ":" not in sql


def test_one_missing_grant_leaves_the_data_files(settings) -> None:
    async def reader(_settings, _alias, kind):
        if kind == "get_control_files":
            raise ActivityAccessError("SYS.V_$CONTROLFILE")
        if kind == "get_redo_logs":
            raise ActivityAccessError("SYS.V_$LOG")
        if kind == "get_data_files":
            return ([
                {"source": "datafile_summary", "file_count": "1", "online_files": "1", "online_tablespaces": "1", "bytes_total": "1048576"},
                {"source": "dba_free_space", "bytes_free": "0"},
                {"source": "dba_data_files", "file_name": "system01.dbf", "tablespace_name": "SYSTEM", "bytes": "1048576", "online_status": "SYSTEM"},
            ], "")
        return ([], "")

    picture = asyncio.run(load_storage(settings, "PDBORCL", reader))
    assert picture.data_files.files[0].name == "system01.dbf"
    assert picture.data_files.used_percent == 100
    assert any("SYS.V_$CONTROLFILE" in item for item in picture.limitations)
    assert any("SYS.V_$LOG" in item for item in picture.limitations)
    assert picture.control_files.files == []
    assert picture.redo_logs.members == []
    assert picture.redo_logs.groups == 0
    assert picture.data_files.flash_cache == "Not collected."
    assert picture.backup.limitation == "No RMAN job was returned."
    assert picture.recovery_area.present is False


def test_empty_backup_leaves_the_recovery_area(settings) -> None:
    async def reader(_settings, _alias, kind):
        if kind == "get_backup":
            raise ActivityAccessError("SYS.V_$RMAN_BACKUP_JOB_DETAILS")
        if kind == "get_recovery_area":
            return ([{
                "source": "v$recovery_file_dest", "name": "FRA",
                "space_limit": "104857600", "space_used": "20971520", "used_pct": "20",
            }], "")
        return ([], "")

    picture = asyncio.run(load_storage(settings, "PDBORCL", reader))
    assert picture.recovery_area.present is True
    assert picture.recovery_area.used_percent == 20
    assert picture.backup.failed is False
    assert picture.backup.limitation is not None
    assert "SYS.V_$RMAN_BACKUP_JOB_DETAILS" in picture.backup.limitation
    assert picture.archive.time_to_failure == "Not collected."
    assert picture.archive.free_minutes == "Not collected."


def test_summary_keeps_counts_and_drops_every_listing(settings) -> None:
    from sentinel.graph.storage import summarize

    picture = asyncio.run(load_storage(settings, "PDBORCL"))
    summary = summarize(picture)
    assert summary.detail is False
    assert summary.detail_note == "File, control-file, redo-member, and segment listings are shown to an administrator."
    assert summary.data_files.files == []
    assert summary.temp_files.files == []
    assert summary.control_files.files == []
    assert summary.control_files.count == 1
    assert summary.redo_logs.members == []
    assert summary.segments.rows == []
    assert summary.data_files.online_files == picture.data_files.online_files
    assert summary.data_files.used_percent == picture.data_files.used_percent
    assert summary.redo_logs.groups == picture.redo_logs.groups
    assert summary.redo_logs.total_display == picture.redo_logs.total_display
    assert summary.recovery_area == picture.recovery_area
    assert summary.archive == picture.archive
    assert summary.backup == picture.backup
    assert picture.detail is True
    assert picture.data_files.files
