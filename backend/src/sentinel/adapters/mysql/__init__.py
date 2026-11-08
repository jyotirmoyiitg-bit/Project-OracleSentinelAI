"""MySQL adapter placeholder — not implemented in Release 1."""

from typing import Any

from sentinel.domain.enums import Engine
from sentinel.domain.models import (
    CapabilityMatrix,
    DiagnosticEvent,
    Observation,
)


class MySQLAdapter:
    """MySQL adapter placeholder — returns not_supported for all operations."""

    engine = Engine.MYSQL

    async def test_connection(self, database_id: str) -> dict[str, Any]:
        return {"status": "not_supported", "message": "MySQL adapter not implemented in Release 1"}

    async def collect_health(self, database_id: str) -> list[Observation]:
        return []

    async def collect_capacity(self, database_id: str) -> list[Observation]:
        return []

    async def collect_configuration(self, database_id: str) -> list[Observation]:
        return []

    async def poll_events(
        self, database_id: str, checkpoint: dict[str, Any] | None = None
    ) -> tuple[list[DiagnosticEvent], dict[str, Any]]:
        return [], {}

    async def run_named_diagnostic(
        self, database_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        return {"status": "not_supported", "message": "MySQL adapter not implemented"}

    async def detect_capabilities(self, database_id: str) -> CapabilityMatrix:
        return CapabilityMatrix()
