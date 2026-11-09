"""TNS-based Oracle connection manager using python-oracledb."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import oracledb

from sentinel.config.secrets import get_resolver
from sentinel.config.settings import Settings

logger = logging.getLogger(__name__)


def tns_directory(settings: Settings) -> Path:
    if not settings.tns_admin:
        raise ValueError("Configure TNS_ADMIN on the backend to list database aliases")
    directory = Path(settings.tns_admin)
    if not directory.is_absolute():
        directory = Path(__file__).resolve().parents[5] / directory
    return directory


def network_aliases(settings: Settings) -> list[str]:
    directory = tns_directory(settings)
    if not (directory / "tnsnames.ora").is_file():
        raise ValueError("TNS_ADMIN must contain a readable tnsnames.ora file")
    return sorted(oracledb.ConnectParams(config_dir=str(directory)).get_network_service_names())


class OracleConnectionManager:
    """Manages TNS-based Oracle connections with bounded pooling and timeouts."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def get_connection_string(self, tns_alias: str, username: str | None = None) -> str:
        """Build connection string from TNS alias."""
        user = username or self.settings.oracle_username
        return f"{user}/{tns_alias}"

    def _resolve_password(self, password: str | None) -> str:
        """Resolve a password, decrypting if it's Fernet-encrypted.

        If no password is provided, falls back to the encrypted value from
        settings (ORACLE_PASSWORD in .env).  The secret resolver handles both
        encrypted (gAAAAA...) and plain-text values transparently.
        """
        if password is not None:
            return password
        resolver = get_resolver()
        resolved = resolver.resolve(self.settings.oracle_password)
        if self.settings.oracle_password and (resolved is None or resolved.startswith(resolver.FERNET_PREFIX)):
            raise ValueError("Oracle password could not be decrypted")
        if resolved:
            return resolved
        return ""

    async def connect(
        self,
        tns_alias: str,
        username: str | None = None,
        password: str | None = None,
    ) -> oracledb.Connection:
        """Create a new Oracle connection with timeout settings.

        The password parameter is optional.  If not provided, the manager
        automatically resolves the encrypted ORACLE_PASSWORD from .env using
        the secret resolver.

        Args:
            tns_alias: TNS alias from tnsnames.ora
            username: Override default username
            password: Password for authentication (optional — resolved from .env if omitted)

        Returns:
            Oracle connection object

        Raises:
            oracledb.Error: If connection fails
        """
        user = username or self.settings.oracle_username
        resolved_password = self._resolve_password(password)
        try:
            connection = oracledb.connect(
                user=user,
                password=resolved_password,
                dsn=tns_alias,
                config_dir=str(tns_directory(self.settings)) if self.settings.tns_admin else None,
                tcp_connect_timeout=self.settings.oracle_connection_timeout,
            )
            connection.call_timeout = self.settings.oracle_statement_timeout * 1000
            connection.stmtcachesize = 0  # Disable statement cache for read-only
            logger.info("Connected to Oracle via TNS alias: %s", tns_alias)
            return connection
        except oracledb.Error as e:
            logger.error("Failed to connect to Oracle via TNS alias %s: %s", tns_alias, e)
            raise

    async def close_connection(self, connection: oracledb.Connection) -> None:
        """Close an Oracle connection safely."""
        try:
            if connection:
                connection.close()
                logger.debug("Oracle connection closed")
        except oracledb.Error as e:
            logger.warning("Error closing Oracle connection: %s", e)

    @asynccontextmanager
    async def connection_context(
        self,
        tns_alias: str,
        username: str | None = None,
        password: str | None = None,
    ):
        """Context manager for Oracle connections."""
        conn = None
        try:
            conn = await self.connect(tns_alias, username, password)
            yield conn
        finally:
            if conn:
                await self.close_connection(conn)

    async def test_connection(self, tns_alias: str, username: str | None = None, password: str | None = None) -> dict[str, Any]:
        """Test connectivity to an Oracle database.
        
        Returns:
            Dict with connection info including version, status, and capabilities
        """
        conn = None
        try:
            conn = await self.connect(tns_alias, username, password)
            version = conn.version if hasattr(conn, 'version') else "unknown"
            
            # Query instance info
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT banner FROM v$version WHERE rownum = 1")
                row = cursor.fetchone()
                banner = row[0] if row else "unknown"
            except oracledb.Error:
                banner = "unknown"
            finally:
                cursor.close()

            return {
                "status": "connected",
                "tns_alias": tns_alias,
                "version": str(version),
                "banner": banner,
                "message": "Connection successful",
            }
        except oracledb.Error as e:
            error_msg = str(e)
            # Redact sensitive information from error messages
            if "password" in error_msg.lower():
                error_msg = "Authentication failed"
            return {
                "status": "error",
                "tns_alias": tns_alias,
                "message": error_msg,
            }
        finally:
            if conn:
                await self.close_connection(conn)
