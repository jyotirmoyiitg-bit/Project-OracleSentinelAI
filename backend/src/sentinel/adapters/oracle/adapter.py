"""Oracle adapter — implements DatabaseAdapter Protocol for Oracle databases."""

import logging
from typing import Any

from sentinel.adapters.oracle.alert_log import OracleAlertLogPoller
from sentinel.adapters.oracle.capabilities import OracleCapabilityDetector
from sentinel.adapters.oracle.connection import OracleConnectionManager
from sentinel.adapters.oracle.probes import OracleProbes
from sentinel.config.settings import Settings
from sentinel.domain.enums import Engine
from sentinel.domain.models import (
    CapabilityMatrix,
    DiagnosticEvent,
    Observation,
)

logger = logging.getLogger(__name__)


class OracleAdapter:
    """Oracle database adapter implementing the DatabaseAdapter Protocol.
    
    Provides read-only access to Oracle database health, capacity, configuration,
    and diagnostic events via TNS connections.
    """

    engine = Engine.ORACLE

    def __init__(self, settings: Settings):
        self.settings = settings
        self.connection_manager = OracleConnectionManager(settings)
        self.probes = OracleProbes()
        self.capability_detector = OracleCapabilityDetector()
        self.alert_log_poller = OracleAlertLogPoller()

    async def test_connection(self, database_id: str) -> dict[str, Any]:
        """Test connectivity to an Oracle database.
        
        Args:
            database_id: The database target ID containing TNS alias info
            
        Returns:
            Dict with connection status, version, and capabilities
        """
        # database_id should map to a registered target with TNS alias
        # For now, we use the database_id as the TNS alias directly
        tns_alias = database_id
        return await self.connection_manager.test_connection(tns_alias)

    async def collect_health(self, database_id: str) -> list[Observation]:
        """Collect health metrics from an Oracle database.
        
        Args:
            database_id: The database target ID (TNS alias)
            
        Returns:
            List of Observation objects with health metrics
        """
        tns_alias = database_id
        observations: list[Observation] = []
        
        try:
            conn = await self.connection_manager.connect(tns_alias)
            try:
                observations.extend(await self.probes.probe_instance_status(conn, database_id))
            finally:
                await self.connection_manager.close_connection(conn)
        except Exception as e:
            logger.error("Health collection failed for %s: %s", database_id, e)

        return observations

    async def collect_capacity(self, database_id: str) -> list[Observation]:
        """Collect capacity metrics (tablespace, FRA, temp) from an Oracle database.
        
        Args:
            database_id: The database target ID (TNS alias)
            
        Returns:
            List of Observation objects with capacity metrics
        """
        tns_alias = database_id
        observations: list[Observation] = []
        
        try:
            conn = await self.connection_manager.connect(tns_alias)
            try:
                observations.extend(await self.probes.probe_tablespace_usage(conn, database_id))
                observations.extend(await self.probes.probe_fra_usage(conn, database_id))
                observations.extend(await self.probes.probe_fra_detail(conn, database_id))
                observations.extend(await self.probes.probe_temp_usage(conn, database_id))
            finally:
                await self.connection_manager.close_connection(conn)
        except Exception as e:
            logger.error("Capacity collection failed for %s: %s", database_id, e)

        return observations

    async def collect_configuration(self, database_id: str) -> list[Observation]:
        """Collect configuration metrics from an Oracle database.
        
        Args:
            database_id: The database target ID (TNS alias)
            
        Returns:
            List of Observation objects with configuration metrics
        """
        tns_alias = database_id
        observations: list[Observation] = []
        
        try:
            conn = await self.connection_manager.connect(tns_alias)
            try:
                observations.extend(await self.probes.probe_configuration(conn, database_id))
                observations.extend(await self.probes.probe_archive_dest(conn, database_id))
                observations.extend(await self.probes.probe_resource_limits(conn, database_id))
            finally:
                await self.connection_manager.close_connection(conn)
        except Exception as e:
            logger.error("Configuration collection failed for %s: %s", database_id, e)

        return observations

    async def poll_events(
        self, database_id: str, checkpoint: dict[str, Any] | None = None
    ) -> tuple[list[DiagnosticEvent], dict[str, Any]]:
        """Poll diagnostic events (alert log) from an Oracle database.
        
        Args:
            database_id: The database target ID (TNS alias)
            checkpoint: Previous checkpoint for incremental polling
            
        Returns:
            Tuple of (list of DiagnosticEvent, new checkpoint dict)
        """
        tns_alias = database_id
        events: list[DiagnosticEvent] = []
        new_checkpoint = checkpoint or {}
        
        try:
            conn = await self.connection_manager.connect(tns_alias)
            try:
                events, new_checkpoint = await self.alert_log_poller.poll(conn, database_id, checkpoint)
            finally:
                await self.connection_manager.close_connection(conn)
        except Exception as e:
            logger.error("Event polling failed for %s: %s", database_id, e)

        return events, new_checkpoint

    async def run_named_diagnostic(
        self, database_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Run a named read-only diagnostic tool.
        
        Args:
            database_id: The database target ID (TNS alias)
            tool_name: Name of the diagnostic tool
            arguments: Tool-specific arguments
            
        Returns:
            Dict with diagnostic results
        """
        tns_alias = database_id
        result: dict[str, Any] = {
            "tool_name": tool_name,
            "database_id": database_id,
            "status": "error",
            "data": None,
        }

        try:
            conn = await self.connection_manager.connect(tns_alias)
            try:
                if tool_name == "instance_status":
                    observations = await self.probes.probe_instance_status(conn, database_id)
                    result["data"] = [
                        {"metric": obs.metric, "value": obs.value, "labels": obs.labels}
                        for obs in observations
                    ]
                elif tool_name == "tablespace_status":
                    observations = await self.probes.probe_tablespace_usage(conn, database_id)
                    result["data"] = [
                        {"metric": obs.metric, "value": obs.value, "labels": obs.labels}
                        for obs in observations
                    ]
                elif tool_name == "fra_status":
                    observations = await self.probes.probe_fra_usage(conn, database_id)
                    result["data"] = [
                        {"metric": obs.metric, "value": obs.value, "labels": obs.labels}
                        for obs in observations
                    ]
                elif tool_name == "resource_limits":
                    observations = await self.probes.probe_resource_limits(conn, database_id)
                    result["data"] = [
                        {"metric": obs.metric, "value": obs.value, "labels": obs.labels}
                        for obs in observations
                    ]
                elif tool_name == "configuration":
                    observations = await self.probes.probe_configuration(conn, database_id)
                    result["data"] = [
                        {"metric": obs.metric, "value": obs.value, "labels": obs.labels}
                        for obs in observations
                    ]
                else:
                    result["message"] = f"Unknown diagnostic tool: {tool_name}"
                    return result

                result["status"] = "success"
            finally:
                await self.connection_manager.close_connection(conn)
        except Exception as e:
            result["message"] = str(e)
            logger.error("Named diagnostic %s failed for %s: %s", tool_name, database_id, e)

        return result

    async def detect_capabilities(self, database_id: str) -> CapabilityMatrix:
        """Detect available views and privileges for an Oracle database.
        
        Args:
            database_id: The database target ID (TNS alias)
            
        Returns:
            CapabilityMatrix with detected capabilities
        """
        tns_alias = database_id
        caps = CapabilityMatrix()
        
        try:
            conn = await self.connection_manager.connect(tns_alias)
            try:
                caps = await self.capability_detector.detect(conn)
            finally:
                await self.connection_manager.close_connection(conn)
        except Exception as e:
            logger.error("Capability detection failed for %s: %s", database_id, e)

        return caps
