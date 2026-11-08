"""Oracle adapter package — TNS-based connection with read-only diagnostic probes."""

from sentinel.adapters.oracle.connection import OracleConnectionManager
from sentinel.adapters.oracle.probes import OracleProbes
from sentinel.adapters.oracle.capabilities import OracleCapabilityDetector
from sentinel.adapters.oracle.alert_log import OracleAlertLogPoller
from sentinel.adapters.oracle.adapter import OracleAdapter

__all__ = [
    "OracleConnectionManager",
    "OracleProbes",
    "OracleCapabilityDetector",
    "OracleAlertLogPoller",
    "OracleAdapter",
]
