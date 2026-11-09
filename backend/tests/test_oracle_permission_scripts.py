import re
from pathlib import Path

import pytest

from sentinel.adapters.oracle.alert_log import OracleAlertLogPoller
from sentinel.adapters.oracle.capabilities import OracleCapabilityDetector
from sentinel.adapters.oracle.probes import OracleProbes

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "oracle"


def script(name):
    return (SCRIPTS / name).read_text(encoding="utf-8")


def statements(name):
    return re.sub(r"--[^\n]*", "", script(name)).upper()


def queried_views(sql):
    return set(re.findall(r"\b(?:FROM|JOIN)\s+((?:V\$|DBA_)[A-Z0-9_$]+|DUAL)\b", sql.upper()))


def grant_object(view):
    return f"SYS.{view.replace('V$', 'V_$', 1)}" if view.startswith("V$") else f"SYS.{view}"


def test_grants_match_collector_queries_and_capabilities_exactly():
    sql = "\n".join(
        value for name, value in vars(OracleProbes).items() if name.endswith("_SQL") and isinstance(value, str)
    )
    views = queried_views(sql + OracleAlertLogPoller.ALERT_LOG_SQL)
    views.update(view.upper() for view in OracleCapabilityDetector.VIEWS_TO_CHECK)
    views.update({"V$VERSION", "DUAL"})
    granted = set(re.findall(r"GRANT SELECT ON (\S+) TO GSYSTEM;", statements("02_grant_readonly_monitoring.sql")))
    assert granted == {grant_object(view) for view in views}
    assert queried_views(statements("03_verify_gsystem.sql")) == views


@pytest.mark.parametrize(("view", "columns"), [
    ("v$instance", ["instance_name", "status", "database_status", "host_name", "version", "startup_time"]),
    ("v$database", ["name", "open_mode", "database_role", "log_mode", "flashback_on", "platform_name", "created"]),
    ("dba_tablespaces", ["tablespace_name", "status", "contents", "logging", "extent_management"]),
    ("dba_data_files", ["tablespace_name", "bytes", "autoextensible"]),
    ("dba_free_space", ["tablespace_name", "bytes"]),
    ("v$temp_space_header", ["tablespace_name", "bytes_used", "bytes_free"]),
    ("v$recovery_file_dest", ["name", "space_limit", "space_used", "space_reclaimable", "number_of_files"]),
    ("v$flash_recovery_area_usage", ["file_type", "percent_space_used", "percent_space_reclaimable", "number_of_files"]),
    ("v$archive_dest_status", ["destination", "status", "error"]),
    ("v$parameter", ["name", "value", "issys_modifiable"]),
    ("v$resource_limit", ["resource_name", "current_utilization", "max_utilization", "initial_allocation", "limit_value"]),
    ("v$diag_alert_ext", ["record_id", "originating_timestamp", "message_text", "message_type", "module", "client_id"]),
])
def test_verification_checks_required_columns_with_bounded_queries(view, columns):
    query = next(
        part for part in statements("03_verify_gsystem.sql").split(";")
        if re.search(rf"\bFROM\s+{re.escape(view.upper())}\b", part)
    )
    for column in columns:
        assert re.search(rf"\b{column.upper()}\b", query)
    assert re.search(r"\bROWNUM\s*<=\s*(1|7)\b", query)
    assert "COUNT(*)" not in query


def test_revoke_covers_current_and_legacy_grants_and_locks_first():
    revoke = statements("04_revoke_gsystem.sql")
    grants = re.findall(r"GRANT SELECT ON SYS\.(\S+) TO GSYSTEM;", statements("02_grant_readonly_monitoring.sql"))
    for name in [*grants, "V_$TABLESPACE", "DBA_TEMP_FILES", "V_$SESSION", "V_$SYSSTAT", "V_$SYSMETRIC"]:
        assert f"'{name}'" in revoke
    assert revoke.index("ALTER USER GSYSTEM ACCOUNT LOCK") < revoke.index("EXECUTE IMMEDIATE 'REVOKE")
    assert "FROM SYS.DBA_TAB_PRIVS" in revoke
    assert "FROM SYS.DBA_SYS_PRIVS" in revoke
    assert "REVOKE CREATE SESSION FROM GSYSTEM" in revoke
    assert "DROP USER" not in revoke


def test_create_prompts_for_password_and_reuses_grant_script_without_storage_privileges():
    create = statements("01_create_gsystem.sql")
    assert re.search(r"ACCEPT GSYSTEM_PASSWORD CHAR PROMPT '[^']+' HIDE", create)
    assert 'CREATE USER GSYSTEM IDENTIFIED BY "&GSYSTEM_PASSWORD";' in create
    assert "UNDEFINE GSYSTEM_PASSWORD" in create
    assert "@@02_GRANT_READONLY_MONITORING.SQL" in create
    assert "QUOTA" not in create
    assert "DEFAULT TABLESPACE" not in create
    assert "CHANGEME" not in create


@pytest.mark.parametrize("name", [
    "01_create_gsystem.sql", "02_grant_readonly_monitoring.sql", "03_verify_gsystem.sql", "04_revoke_gsystem.sql",
])
def test_scripts_fail_explicitly_and_reject_wrong_container(name):
    content = statements(name)
    assert "WHENEVER SQLERROR EXIT SQL.SQLCODE" in content
    assert "WHENEVER OSERROR EXIT FAILURE" in content
    assert "SYS_CONTEXT('USERENV', 'CON_NAME')" in content
    assert "'CDB$ROOT', 'PDB$SEED'" in content
    assert "RAISE_APPLICATION_ERROR" in content
    assert not re.search(r"WHEN OTHERS\s+THEN\s+NULL", content)


def test_no_conflict_markers_or_broad_grants():
    for path in SCRIPTS.iterdir():
        if path.is_file():
            assert not re.search(r"^(<<<<<<<|=======|>>>>>>>)", path.read_text(encoding="utf-8"), re.MULTILINE)
    for path in SCRIPTS.glob("*.sql"):
        content = statements(path.name)
        assert not re.search(r"GRANT\s+(DBA|SELECT ANY|SELECT_CATALOG_ROLE|CREATE TABLE|UNLIMITED TABLESPACE)\b", content)
        assert not re.search(r"GRANT SELECT ON V\$", content)
