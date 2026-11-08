"""Domain enums for Oracle Sentinel."""

from enum import StrEnum


class Engine(StrEnum):
    """Database engine types."""

    ORACLE = "oracle"
    MYSQL = "mysql"
    SQLSERVER = "sqlserver"


class HealthStatus(StrEnum):
    """Database health status values."""

    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"
    OFFLINE = "offline"
    PERMISSION_DENIED = "permission_denied"


class IncidentStatus(StrEnum):
    """Incident lifecycle status."""

    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ComplianceResult(StrEnum):
    """Compliance policy evaluation result."""

    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class ObservationQuality(StrEnum):
    """Quality flag for observations."""

    MEASURED = "measured"
    ESTIMATED = "estimated"
    STALE = "stale"
    UNKNOWN = "unknown"
