"""Oracle capability detection — checks available views and privileges."""

import logging
from datetime import datetime

import oracledb

from sentinel.domain.models import CapabilityMatrix

logger = logging.getLogger(__name__)


class OracleCapabilityDetector:
    """Detects available views and privileges after connecting to Oracle."""

    # Views to check for SELECT privilege
    VIEWS_TO_CHECK = {
        "v$instance": "has_select_v$instance",
        "v$database": "has_select_v$database",
        "dba_tablespaces": "has_select_dba_tablespaces",
        "dba_data_files": "has_select_dba_data_files",
        "dba_free_space": "has_select_dba_free_space",
        "v$recovery_file_dest": "has_select_v$recovery_file_dest",
        "v$archive_dest_status": "has_select_v$archive_dest_status",
        "v$parameter": "has_select_v$parameter",
        "v$resource_limit": "has_select_v$resource_limit",
        "v$flash_recovery_area_usage": "has_select_v$flash_recovery_area_usage",
        "v$diag_alert_ext": "has_select_v$diag_alert_ext",
    }

    async def detect(self, connection: oracledb.Connection) -> CapabilityMatrix:
        """Detect all capabilities of the connected Oracle instance.
        
        Args:
            connection: Active Oracle connection
            
        Returns:
            CapabilityMatrix with detected capabilities
        """
        caps = CapabilityMatrix()
        cursor = connection.cursor()

        try:
            # Detect version and edition
            await self._detect_version(cursor, caps)
            
            # Detect CDB/PDB context
            await self._detect_cdb_context(cursor, caps)
            
            # Check view access
            await self._check_view_access(cursor, caps)
            
            # Infer higher-level capabilities
            caps.supports_fra = caps.has_select_v$recovery_file_dest
            caps.supports_flashback = caps.has_select_v$database
            caps.supports_vdiag_alert_ext = caps.has_select_v$diag_alert_ext
            caps.supports_tablespace_usage_metrics = (
                caps.has_select_dba_tablespaces and caps.has_select_dba_data_files
            )
            # AWR requires specific licensing — default to False
            caps.supports_awr = False

        except oracledb.Error as e:
            logger.error("Capability detection failed: %s", e)
        finally:
            cursor.close()

        logger.info(
            "Capabilities detected: version=%s, edition=%s, is_cdb=%s, container=%s",
            caps.oracle_version,
            caps.oracle_edition,
            caps.is_cdb,
            caps.container_name,
        )
        return caps

    async def _detect_version(self, cursor: oracledb.Cursor, caps: CapabilityMatrix) -> None:
        """Detect Oracle version and edition."""
        try:
            cursor.execute("SELECT banner FROM v$version WHERE rownum = 1")
            row = cursor.fetchone()
            if row:
                banner = row[0]
                caps.oracle_version = banner
                # Extract edition
                if "Enterprise" in banner:
                    caps.oracle_edition = "Enterprise"
                elif "Standard" in banner:
                    caps.oracle_edition = "Standard"
                else:
                    caps.oracle_edition = "Unknown"
        except oracledb.Error as e:
            logger.warning("Version detection failed: %s", e)

    async def _detect_cdb_context(self, cursor: oracledb.Cursor, caps: CapabilityMatrix) -> None:
        """Detect CDB/PDB context."""
        try:
            cursor.execute("SELECT SYS_CONTEXT('USERENV', 'CON_NAME') FROM dual")
            row = cursor.fetchone()
            if row:
                caps.container_name = row[0] or ""

            cursor.execute("SELECT SYS_CONTEXT('USERENV', 'CON_ID') FROM dual")
            row = cursor.fetchone()
            if row:
                con_id = row[0]
                caps.is_cdb = (con_id == 0) if con_id is not None else False
        except oracledb.Error as e:
            logger.warning("CDB context detection failed: %s", e)

    async def _check_view_access(self, cursor: oracledb.Cursor, caps: CapabilityMatrix) -> None:
        """Check SELECT access for each required view."""
        for view_name, cap_attr in self.VIEWS_TO_CHECK.items():
            try:
                cursor.execute(f"SELECT 1 FROM {view_name} WHERE rownum = 1")
                cursor.fetchone()
                setattr(caps, cap_attr, True)
                logger.debug("View accessible: %s", view_name)
            except oracledb.Error as e:
                setattr(caps, cap_attr, False)
                logger.debug("View not accessible (%s): %s", view_name, e)
