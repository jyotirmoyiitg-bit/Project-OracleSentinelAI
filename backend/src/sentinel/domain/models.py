"""Engine-agnostic domain models for Oracle Sentinel."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class Observation:
    """A single metric observation from a database target."""

    database_id: str
    metric: str  # e.g. storage.tablespace.used_pct
    value: float | int | str | bool | None
    unit: str | None = None
    collected_at: datetime = field(default_factory=datetime.utcnow)
    labels: dict[str, str] = field(default_factory=dict)
    source: str = ""  # named diagnostic probe
    quality: str = "measured"


@dataclass(frozen=True)
class DiagnosticEvent:
    """A diagnostic event from a database target (e.g. alert log entry)."""

    database_id: str
    source_id: str  # stable source identifier
    occurred_at: datetime
    severity: str
    code: str | None = None
    message: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class CapabilityMatrix:
    """Detected capabilities of a database target after connection."""

    oracle_version: str = ""
    oracle_edition: str = ""
    is_cdb: bool = False
    container_name: str = ""
    supports_awr: bool = False
    supports_flashback: bool = False
    supports_vdiag_alert_ext: bool = False
    supports_tablespace_usage_metrics: bool = False
    supports_fra: bool = False
    has_select_v_instance: bool = False
    has_select_v_database: bool = False
    has_select_dba_tablespaces: bool = False
    has_select_dba_data_files: bool = False
    has_select_dba_free_space: bool = False
    has_select_v_recovery_file_dest: bool = False
    has_select_v_archive_dest_status: bool = False
    has_select_v_parameter: bool = False
    has_select_v_resource_limit: bool = False
    has_select_v_flash_recovery_area_usage: bool = False
    has_select_v_diag_alert_ext: bool = False
    detected_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "oracle_version": self.oracle_version,
            "oracle_edition": self.oracle_edition,
            "is_cdb": self.is_cdb,
            "container_name": self.container_name,
            "supports_awr": self.supports_awr,
            "supports_flashback": self.supports_flashback,
            "supports_vdiag_alert_ext": self.supports_vdiag_alert_ext,
            "supports_tablespace_usage_metrics": self.supports_tablespace_usage_metrics,
            "supports_fra": self.supports_fra,
            "has_select_v$instance": self.has_select_v_instance,
            "has_select_v$database": self.has_select_v_database,
            "has_select_dba_tablespaces": self.has_select_dba_tablespaces,
            "has_select_dba_data_files": self.has_select_dba_data_files,
            "has_select_dba_free_space": self.has_select_dba_free_space,
            "has_select_v$recovery_file_dest": self.has_select_v_recovery_file_dest,
            "has_select_v$archive_dest_status": self.has_select_v_archive_dest_status,
            "has_select_v$parameter": self.has_select_v_parameter,
            "has_select_v$resource_limit": self.has_select_v_resource_limit,
            "has_select_v$flash_recovery_area_usage": self.has_select_v_flash_recovery_area_usage,
            "has_select_v$diag_alert_ext": self.has_select_v_diag_alert_ext,
            "detected_at": self.detected_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityMatrix":
        """Create from dictionary."""
        if not data:
            return cls()
        serialized_names = {
            "has_select_v_instance": "has_select_v$instance",
            "has_select_v_database": "has_select_v$database",
            "has_select_v_recovery_file_dest": "has_select_v$recovery_file_dest",
            "has_select_v_archive_dest_status": "has_select_v$archive_dest_status",
            "has_select_v_parameter": "has_select_v$parameter",
            "has_select_v_resource_limit": "has_select_v$resource_limit",
            "has_select_v_flash_recovery_area_usage": "has_select_v$flash_recovery_area_usage",
            "has_select_v_diag_alert_ext": "has_select_v$diag_alert_ext",
        }
        kwargs = {}
        for key in cls.__dataclass_fields__:
            serialized_key = serialized_names.get(key, key)
            if key in data or serialized_key in data:
                val = data.get(key, data.get(serialized_key))
                if key == "detected_at" and isinstance(val, str):
                    val = datetime.fromisoformat(val)
                kwargs[key] = val
        return cls(**kwargs)


class DatabaseAdapter(Protocol):
    """Protocol for database engine adapters — engine-agnostic contract."""

    engine: str

    async def test_connection(self, database_id: str) -> dict[str, Any]:
        """Test connectivity and return connection info."""
        ...

    async def collect_health(self, database_id: str) -> list[Observation]:
        """Collect health metrics."""
        ...

    async def collect_capacity(self, database_id: str) -> list[Observation]:
        """Collect capacity metrics (tablespace, FRA, etc.)."""
        ...

    async def collect_configuration(self, database_id: str) -> list[Observation]:
        """Collect configuration metrics."""
        ...

    async def poll_events(
        self, database_id: str, checkpoint: dict[str, Any] | None = None
    ) -> tuple[list[DiagnosticEvent], dict[str, Any]]:
        """Poll diagnostic events and return new checkpoint."""
        ...

    async def run_named_diagnostic(
        self, database_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Run a named read-only diagnostic tool."""
        ...

    async def detect_capabilities(self, database_id: str) -> CapabilityMatrix:
        """Detect available views and privileges."""
        ...
