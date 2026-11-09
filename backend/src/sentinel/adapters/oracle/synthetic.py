"""Synthetic Oracle adapter for testing without real Oracle credentials."""

import logging
from datetime import datetime
from typing import Any

from sentinel.domain.enums import Engine
from sentinel.domain.models import (
    CapabilityMatrix,
    DiagnosticEvent,
    Observation,
)

logger = logging.getLogger(__name__)


class SyntheticOracleAdapter:
    """Synthetic Oracle adapter that simulates different Oracle versions and scenarios.
    
    Used for testing without real Oracle credentials.
    """

    engine = Engine.ORACLE

    def __init__(
        self,
        version: str = "19.0.0.0",
        edition: str = "Enterprise",
        is_cdb: bool = False,
        container_name: str = "",
        simulate_error: bool = False,
        simulate_permission_denied: bool = False,
        used_pct: float = 65.5,
    ):
        self.version = version
        self.edition = edition
        self.is_cdb = is_cdb
        self.container_name = container_name
        self.simulate_error = simulate_error
        self.simulate_permission_denied = simulate_permission_denied
        self.used_pct = used_pct

    async def collect_probe(self, probe: str, alias: str) -> list[Observation]:
        if self.simulate_error:
            raise ConnectionError("Synthetic target is offline")
        if self.simulate_permission_denied and probe != "connectivity":
            raise PermissionError("Synthetic monitoring grants are missing")
        if probe == "connectivity":
            return [Observation(alias, "connectivity.available", True, source="synthetic.connect")]
        if probe == "instance":
            return await self.collect_health(alias) + [
                Observation(alias, "database.role", "PRIMARY", source="v$database"),
            ]
        if probe == "storage":
            return [
                Observation(alias, "storage.tablespace.used_pct", self.used_pct, unit="percent",
                            labels={"tablespace": "USERS", "source": "dba_tablespaces"}),
                Observation(alias, "storage.tablespace.bytes_total", 10737418240, unit="bytes",
                            labels={"tablespace": "USERS", "source": "dba_data_files"}),
                Observation(alias, "storage.tablespace.autoextend", True,
                            labels={"tablespace": "USERS", "source": "dba_data_files"}),
            ]
        if probe == "fra_archive":
            return [
                Observation(alias, "storage.fra.used_pct", 28.7, unit="percent", source="v$recovery_file_dest"),
                Observation(alias, "storage.fra.space_limit", 10737418240, unit="bytes", source="v$recovery_file_dest"),
                Observation(alias, "storage.fra.space_used", 3081634119, unit="bytes", source="v$recovery_file_dest"),
                Observation(alias, "storage.fra.usage_by_type", 28.7, unit="percent",
                            labels={"file_type": "ARCHIVED LOG"}, source="v$flash_recovery_area_usage"),
                Observation(alias, "storage.archive.dest_status", "VALID",
                            labels={"destination": "USE_DB_RECOVERY_FILE_DEST"}, source="v$archive_dest_status"),
            ]
        if probe == "resources":
            return [Observation(alias, "resources.used_pct", 40, unit="percent",
                                labels={"resource": "sessions"}, source="v$resource_limit")]
        if probe == "configuration":
            return await self.collect_configuration(alias)
        raise ValueError(f"Unknown probe: {probe}")

    async def test_connection(self, database_id: str) -> dict[str, Any]:
        """Simulate a connection test."""
        if self.simulate_error:
            return {
                "status": "error",
                "tns_alias": database_id,
                "message": "Simulated connection error",
            }
        return {
            "status": "connected",
            "tns_alias": database_id,
            "version": self.version,
            "banner": f"Oracle Database {self.version} {self.edition} Edition",
            "message": "Connection successful (simulated)",
        }

    async def collect_health(self, database_id: str) -> list[Observation]:
        """Simulate health metrics collection."""
        if self.simulate_error:
            return []

        now = datetime.utcnow()
        return [
            Observation(
                database_id=database_id,
                metric="instance.name",
                value="ORCL",
                collected_at=now,
                labels={"source": "v$instance"},
            ),
            Observation(
                database_id=database_id,
                metric="instance.status",
                value="OPEN",
                collected_at=now,
                labels={"source": "v$instance"},
            ),
            Observation(
                database_id=database_id,
                metric="instance.version",
                value=self.version,
                collected_at=now,
                labels={"source": "v$instance"},
            ),
            Observation(
                database_id=database_id,
                metric="database.open_mode",
                value="READ WRITE",
                collected_at=now,
                labels={"source": "v$database"},
            ),
            Observation(
                database_id=database_id,
                metric="database.log_mode",
                value="ARCHIVELOG",
                collected_at=now,
                labels={"source": "v$database"},
            ),
            Observation(
                database_id=database_id,
                metric="database.flashback_on",
                value="YES",
                collected_at=now,
                labels={"source": "v$database"},
            ),
        ]

    async def collect_capacity(self, database_id: str) -> list[Observation]:
        """Simulate capacity metrics collection."""
        if self.simulate_error:
            return []

        now = datetime.utcnow()
        return [
            Observation(
                database_id=database_id,
                metric="storage.tablespace.used_pct",
                value=65.5,
                unit="percent",
                collected_at=now,
                labels={"tablespace": "SYSTEM", "source": "dba_tablespaces"},
            ),
            Observation(
                database_id=database_id,
                metric="storage.tablespace.used_pct",
                value=42.3,
                unit="percent",
                collected_at=now,
                labels={"tablespace": "USERS", "source": "dba_tablespaces"},
            ),
            Observation(
                database_id=database_id,
                metric="storage.fra.used_pct",
                value=28.7,
                unit="percent",
                collected_at=now,
                labels={"source": "v$recovery_file_dest"},
            ),
        ]

    async def collect_configuration(self, database_id: str) -> list[Observation]:
        """Simulate configuration metrics collection."""
        if self.simulate_error:
            return []

        now = datetime.utcnow()
        return [
            Observation(
                database_id=database_id,
                metric="config.parameter",
                value="/u01/oradata",
                collected_at=now,
                labels={"parameter": "db_create_file_dest", "source": "v$parameter"},
            ),
            Observation(
                database_id=database_id,
                metric="config.parameter",
                value="/u02/fast_recovery_area",
                collected_at=now,
                labels={"parameter": "db_recovery_file_dest", "source": "v$parameter"},
            ),
            Observation(
                database_id=database_id,
                metric="config.parameter",
                value="10G",
                collected_at=now,
                labels={"parameter": "db_recovery_file_dest_size", "source": "v$parameter"},
            ),
        ]

    async def poll_events(
        self, database_id: str, checkpoint: dict[str, Any] | None = None
    ) -> tuple[list[DiagnosticEvent], dict[str, Any]]:
        """Simulate alert log polling."""
        if self.simulate_error:
            return [], {}

        events = [
            DiagnosticEvent(
                database_id=database_id,
                source_id="alert_log:1001",
                occurred_at=datetime.utcnow(),
                severity="info",
                code=None,
                message="Database mounted",
                attributes={"record_id": 1001},
            ),
            DiagnosticEvent(
                database_id=database_id,
                source_id="alert_log:1002",
                occurred_at=datetime.utcnow(),
                severity="warning",
                code="ORA-01652",
                message="ORA-01652: unable to extend temp segment",
                attributes={"record_id": 1002},
            ),
        ]

        new_checkpoint = {
            "since": datetime.utcnow().isoformat(),
            "last_record_id": 1002,
        }

        return events, new_checkpoint

    async def run_named_diagnostic(
        self, database_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Simulate a named diagnostic tool."""
        if self.simulate_error:
            return {
                "tool_name": tool_name,
                "database_id": database_id,
                "status": "error",
                "message": "Simulated error",
            }

        if tool_name == "instance_status":
            observations = await self.collect_health(database_id)
            return {
                "tool_name": tool_name,
                "database_id": database_id,
                "status": "success",
                "data": [
                    {"metric": obs.metric, "value": obs.value, "labels": obs.labels}
                    for obs in observations
                ],
            }
        elif tool_name == "tablespace_status":
            observations = await self.collect_capacity(database_id)
            return {
                "tool_name": tool_name,
                "database_id": database_id,
                "status": "success",
                "data": [
                    {"metric": obs.metric, "value": obs.value, "labels": obs.labels}
                    for obs in observations
                ],
            }
        else:
            return {
                "tool_name": tool_name,
                "database_id": database_id,
                "status": "error",
                "message": f"Unknown diagnostic tool: {tool_name}",
            }

    async def detect_capabilities(self, database_id: str) -> CapabilityMatrix:
        """Simulate capability detection."""
        if self.simulate_permission_denied:
            return CapabilityMatrix()

        caps = CapabilityMatrix()
        caps.oracle_version = f"Oracle Database {self.version} {self.edition} Edition"
        caps.oracle_edition = self.edition
        caps.is_cdb = self.is_cdb
        caps.container_name = self.container_name
        caps.supports_fra = True
        caps.supports_flashback = True
        caps.supports_vdiag_alert_ext = True
        caps.supports_tablespace_usage_metrics = True
        caps.has_select_v_instance = True
        caps.has_select_v_database = True
        caps.has_select_dba_tablespaces = True
        caps.has_select_dba_data_files = True
        caps.has_select_dba_free_space = True
        caps.has_select_v_recovery_file_dest = True
        caps.has_select_v_archive_dest_status = True
        caps.has_select_v_parameter = True
        caps.has_select_v_resource_limit = True
        caps.has_select_v_flash_recovery_area_usage = True
        caps.has_select_v_diag_alert_ext = True
        return caps
