"""Phase 27: one tablespace per timeline series, the fullest tablespace on the card, and used space against the
autoextend maximum."""

import asyncio
import importlib
import re
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from sentinel.adapters.oracle.probes import OracleProbes
from sentinel.adapters.oracle.synthetic import SyntheticOracleAdapter
from sentinel.api.v1.databases import series_labels
from sentinel.collectors.normalization import utcnow
from sentinel.persistence.models import Observation
from sentinel.persistence.session import get_session


@pytest.fixture
def client(settings, sessions):
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)

    def request_session():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    with TestClient(app) as client:
        client.headers.update({"X-Sentinel-Role": "dba", "X-Sentinel-Principal": "dana"})
        yield client


def store_tablespaces(sessions, target_id: uuid.UUID) -> None:
    """Twelve collections of the MY19C mix: rows of different tablespaces share each timestamp."""
    values = {"INDX": 0.47, "SYSAUX": 94.06, "SYSTEM": 94.19, "UNDOTBS1": 10.58, "USERS": 94.41}
    start = utcnow() - timedelta(minutes=60)
    with sessions.begin() as session:
        for step in range(12):
            for name, value in values.items():
                session.add(Observation(
                    target_id=target_id, metric_name="storage.tablespace.used_pct",
                    timestamp=start + timedelta(minutes=5 * step), value=value, unit="percent",
                    labels={"source": "dba_tablespaces", "tablespace_name": name}, quality="measured",
                    source="dba_tablespaces",
                ))


def test_metrics_return_one_tablespace_when_a_label_is_given(client, sessions, make_target):
    target_id = make_target()
    store_tablespaces(sessions, uuid.UUID(str(target_id)))
    base = f"/api/v1/databases/{target_id}/metrics?metric_name=storage.tablespace.used_pct"

    mixed = client.get(base).json()
    assert len(mixed) == 60
    assert {row["value"] for row in mixed} == {0.47, 94.06, 94.19, 10.58, 94.41}

    users = client.get(f"{base}&label=tablespace_name=USERS").json()
    assert len(users) == 12
    assert {row["value"] for row in users} == {94.41}
    assert {row["labels"]["tablespace_name"] for row in users} == {"USERS"}
    assert [row["timestamp"] for row in users] == sorted(row["timestamp"] for row in users)

    both = client.get(f"{base}&label=tablespace_name=USERS&label=source=dba_tablespaces").json()
    assert len(both) == 12
    assert client.get(f"{base}&label=tablespace_name=NOSUCH").json() == []


@pytest.mark.parametrize("label", [
    "tablespace_name", "=USERS", "Tablespace=USERS", "table space=USERS", "tablespace_name=", f"name={'x' * 256}",
    "a;drop=1",
])
def test_metrics_reject_a_malformed_label(client, make_target, label):
    target_id = make_target()
    response = client.get(f"/api/v1/databases/{target_id}/metrics", params={"label": label})
    assert response.status_code == 422


def test_metrics_reject_too_many_labels(client, make_target):
    target_id = make_target()
    labels = [f"k{index}=v" for index in range(5)]
    assert client.get(f"/api/v1/databases/{target_id}/metrics", params={"label": labels}).status_code == 422


def test_label_values_are_compared_not_run():
    assert series_labels(["tablespace_name=USERS' OR '1'='1"]) == {"tablespace_name": "USERS' OR '1'='1"}
    assert series_labels(None) == {}


def test_detail_keeps_used_and_used_of_maximum_apart(client, runner, make_target):
    target_id = make_target()
    runner.run(target_id, "storage")
    detail = client.get(f"/api/v1/databases/{target_id}").json()
    users = next(space for space in detail["tablespaces"] if space["name"] == "USERS")
    assert users["used_pct"] == 65.5
    assert users["used_pct_of_max"] == round(65.5 * 10 / 32, 2)
    assert users["max_bytes"] == 34359738368
    assert users["size_bytes"] == 10737418240


def test_synthetic_storage_reports_used_of_maximum():
    rows = asyncio.run(SyntheticOracleAdapter().collect_probe("storage", "SYNTHETIC_HEALTHY"))
    names = {row.metric for row in rows}
    assert {"storage.tablespace.used_pct", "storage.tablespace.used_pct_of_max", "storage.tablespace.bytes_max"} <= names


class _Cursor:
    def __init__(self, columns, rows):
        self.description = [(name,) for name in columns]
        self._rows = rows
        self.executed = []

    def execute(self, sql, *args):
        self.executed.append(sql)

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_probe_reports_used_space_against_the_autoextend_maximum():
    columns = ["TABLESPACE_NAME", "BYTES_TOTAL", "BYTES_USED", "BYTES_FREE", "USED_PCT", "BYTES_MAX",
               "USED_PCT_OF_MAX", "TS_STATUS", "TS_CONTENTS", "TS_LOGGING", "EXTENT_MANAGEMENT", "AUTOEXTEND"]
    gib = 1024 ** 3
    cursor = _Cursor(columns, [("USERS", gib, int(0.9441 * gib), int(0.0559 * gib), 94.41, 32 * gib, 2.95,
                                "ONLINE", "PERMANENT", "LOGGING", "LOCAL", 1)])
    rows = asyncio.run(OracleProbes().probe_tablespace_usage(_Connection(cursor), "db"))
    by_metric = {row.metric: row for row in rows}
    assert by_metric["storage.tablespace.used_pct"].value == 94.41
    assert by_metric["storage.tablespace.used_pct_of_max"].value == 2.95
    assert by_metric["storage.tablespace.used_pct_of_max"].labels["tablespace"] == "USERS"
    assert by_metric["storage.tablespace.bytes_max"].value == 32 * gib


def test_probe_sql_counts_autoextend_files_at_their_maximum_and_only_reads():
    sql = OracleProbes.TABLESPACE_USAGE_SQL
    assert "GREATEST(maxbytes, bytes)" in sql
    assert "autoextensible = 'YES'" in sql
    assert re.search(r"\b(insert|update|delete|merge|alter|drop|create|grant)\b", sql, re.IGNORECASE) is None
    assert set(re.findall(r"\bdba_\w+", sql, re.IGNORECASE)) == {"dba_data_files", "dba_free_space", "dba_tablespaces"}
