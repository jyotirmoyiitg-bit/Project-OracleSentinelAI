"""Fixed SQL probes for read-only Oracle diagnostics."""

import logging
from datetime import datetime
from typing import Any

import oracledb

from sentinel.domain.models import Observation

logger = logging.getLogger(__name__)


class OracleProbes:
    """Read-only SQL probes for Oracle database health, capacity, and configuration."""

    # Instance status probe
    INSTANCE_STATUS_SQL = """
        SELECT
            i.instance_name,
            i.status AS instance_status,
            i.database_status,
            i.host_name,
            i.version,
            i.startup_time,
            d.name AS db_name,
            d.open_mode,
            d.database_role,
            d.log_mode,
            d.flashback_on,
            d.platform_name,
            d.created
        FROM v$instance i
        JOIN v$database d ON 1=1
    """

    # Tablespace usage probe
    TABLESPACE_USAGE_SQL = """
        SELECT
            df.tablespace_name,
            df.bytes_total,
            df.bytes_used,
            df.bytes_free,
            CASE WHEN df.bytes_total > 0
                THEN ROUND(df.bytes_used * 100.0 / df.bytes_total, 2)
                ELSE 0
            END AS used_pct,
            ts.status AS ts_status,
            ts.contents AS ts_contents,
            ts.logging AS ts_logging,
            ts.extent_management
        FROM (
            SELECT
                tablespace_name,
                SUM(bytes) AS bytes_total,
                SUM(bytes) - NVL(SUM(fs.bytes), 0) AS bytes_used,
                NVL(SUM(fs.bytes), 0) AS bytes_free
            FROM dba_data_files df
            LEFT JOIN (
                SELECT tablespace_name, SUM(bytes) AS bytes
                FROM dba_free_space
                GROUP BY tablespace_name
            ) fs ON df.tablespace_name = fs.tablespace_name
            GROUP BY tablespace_name
        ) df
        JOIN dba_tablespaces ts ON df.tablespace_name = ts.tablespace_name
        ORDER BY df.tablespace_name
    """

    # FRA usage probe
    FRA_USAGE_SQL = """
        SELECT
            name,
            space_limit,
            space_used,
            space_reclaimable,
            number_of_files,
            ROUND(space_used * 100.0 / NULLIF(space_limit, 0), 2) AS used_pct
        FROM v$recovery_file_dest
    """

    # FRA detailed usage by type
    FRA_DETAIL_SQL = """
        SELECT
            file_type,
            percent_space_used,
            percent_space_reclaimable,
            number_of_files
        FROM v$flash_recovery_area_usage
        WHERE percent_space_used > 0
        ORDER BY percent_space_used DESC
    """

    # Archive log destination status
    ARCHIVE_DEST_SQL = """
        SELECT
            destination,
            status,
            target,
            archiver,
            register,
            alternate,
            reuse_setting,
            registration_timestamp,
            sequence# AS sequence_num
        FROM v$archive_dest_status
        WHERE status IS NOT NULL
        ORDER BY destination
    """

    # Resource limits
    RESOURCE_LIMIT_SQL = """
        SELECT
            resource_name,
            current_utilization,
            max_utilization,
            initial_allocation,
            limit_value
        FROM v$resource_limit
        WHERE resource_name IN (
            'processes', 'sessions', 'transactions',
            'distributed_locks', 'enqueue_locks', 'enqueue_resources'
        )
    """

    # Configuration parameters
    CONFIG_PARAMS_SQL = """
        SELECT
            name,
            value,
            issys_modifiable
        FROM v$parameter
        WHERE name IN (
            'db_create_file_dest',
            'db_create_online_log_dest_1',
            'db_recovery_file_dest',
            'db_recovery_file_dest_size',
            'log_archive_dest_1',
            'audit_trail',
            'control_file_record_keep_time'
        )
        ORDER BY name
    """

    # Temporary tablespace usage
    TEMP_USAGE_SQL = """
        SELECT
            tablespace_name,
            bytes_used,
            bytes_free,
            ROUND(bytes_used * 100.0 / NULLIF(bytes_used + bytes_free, 0), 2) AS used_pct
        FROM v$temp_space_header
        ORDER BY tablespace_name
    """

    def __init__(self):
        pass

    async def probe_instance_status(self, connection: oracledb.Connection, database_id: str) -> list[Observation]:
        """Collect instance status metrics."""
        observations = []
        cursor = connection.cursor()
        try:
            cursor.execute(self.INSTANCE_STATUS_SQL)
            columns = [col[0] for col in cursor.description]
            row = cursor.fetchone()
            if row:
                data = dict(zip(columns, row))
                now = datetime.utcnow()
                observations.extend([
                    Observation(
                        database_id=database_id,
                        metric="instance.name",
                        value=data.get("INSTANCE_NAME", ""),
                        collected_at=now,
                        labels={"source": "v$instance"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="instance.status",
                        value=data.get("INSTANCE_STATUS", ""),
                        collected_at=now,
                        labels={"source": "v$instance"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="instance.database_status",
                        value=data.get("DATABASE_STATUS", ""),
                        collected_at=now,
                        labels={"source": "v$instance"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="instance.version",
                        value=data.get("VERSION", ""),
                        collected_at=now,
                        labels={"source": "v$instance"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="database.name",
                        value=data.get("DB_NAME", ""),
                        collected_at=now,
                        labels={"source": "v$database"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="database.open_mode",
                        value=data.get("OPEN_MODE", ""),
                        collected_at=now,
                        labels={"source": "v$database"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="database.role",
                        value=data.get("DATABASE_ROLE", ""),
                        collected_at=now,
                        labels={"source": "v$database"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="database.log_mode",
                        value=data.get("LOG_MODE", ""),
                        collected_at=now,
                        labels={"source": "v$database"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="database.flashback_on",
                        value=data.get("FLASHBACK_ON", ""),
                        collected_at=now,
                        labels={"source": "v$database"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="database.platform",
                        value=data.get("PLATFORM_NAME", ""),
                        collected_at=now,
                        labels={"source": "v$database"},
                    ),
                ])
        except oracledb.Error as e:
            logger.error("Instance status probe failed for %s: %s", database_id, e)
        finally:
            cursor.close()
        return observations

    async def probe_tablespace_usage(self, connection: oracledb.Connection, database_id: str) -> list[Observation]:
        """Collect tablespace usage metrics."""
        observations = []
        cursor = connection.cursor()
        try:
            cursor.execute(self.TABLESPACE_USAGE_SQL)
            columns = [col[0] for col in cursor.description]
            now = datetime.utcnow()
            for row in cursor.fetchall():
                data = dict(zip(columns, row))
                ts_name = data.get("TABLESPACE_NAME", "unknown")
                bytes_total = data.get("BYTES_TOTAL", 0) or 0
                bytes_used = data.get("BYTES_USED", 0) or 0
                bytes_free = data.get("BYTES_FREE", 0) or 0
                used_pct = data.get("USED_PCT", 0) or 0

                observations.extend([
                    Observation(
                        database_id=database_id,
                        metric="storage.tablespace.used_pct",
                        value=float(used_pct),
                        unit="percent",
                        collected_at=now,
                        labels={"tablespace": ts_name, "source": "dba_tablespaces"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="storage.tablespace.bytes_total",
                        value=int(bytes_total),
                        unit="bytes",
                        collected_at=now,
                        labels={"tablespace": ts_name, "source": "dba_data_files"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="storage.tablespace.bytes_used",
                        value=int(bytes_used),
                        unit="bytes",
                        collected_at=now,
                        labels={"tablespace": ts_name, "source": "dba_data_files"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="storage.tablespace.bytes_free",
                        value=int(bytes_free),
                        unit="bytes",
                        collected_at=now,
                        labels={"tablespace": ts_name, "source": "dba_free_space"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="storage.tablespace.status",
                        value=data.get("TS_STATUS", ""),
                        collected_at=now,
                        labels={"tablespace": ts_name, "source": "dba_tablespaces"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="storage.tablespace.contents",
                        value=data.get("TS_CONTENTS", ""),
                        collected_at=now,
                        labels={"tablespace": ts_name, "source": "dba_tablespaces"},
                    ),
                ])
        except oracledb.Error as e:
            logger.error("Tablespace usage probe failed for %s: %s", database_id, e)
        finally:
            cursor.close()
        return observations

    async def probe_fra_usage(self, connection: oracledb.Connection, database_id: str) -> list[Observation]:
        """Collect FRA usage metrics."""
        observations = []
        cursor = connection.cursor()
        try:
            cursor.execute(self.FRA_USAGE_SQL)
            columns = [col[0] for col in cursor.description]
            now = datetime.utcnow()
            for row in cursor.fetchall():
                data = dict(zip(columns, row))
                space_limit = data.get("SPACE_LIMIT", 0) or 0
                space_used = data.get("SPACE_USED", 0) or 0
                used_pct = data.get("USED_PCT", 0) or 0

                observations.extend([
                    Observation(
                        database_id=database_id,
                        metric="storage.fra.used_pct",
                        value=float(used_pct),
                        unit="percent",
                        collected_at=now,
                        labels={"source": "v$recovery_file_dest"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="storage.fra.space_limit",
                        value=int(space_limit),
                        unit="bytes",
                        collected_at=now,
                        labels={"source": "v$recovery_file_dest"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="storage.fra.space_used",
                        value=int(space_used),
                        unit="bytes",
                        collected_at=now,
                        labels={"source": "v$recovery_file_dest"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="storage.fra.space_reclaimable",
                        value=int(data.get("SPACE_RECLAIMABLE", 0) or 0),
                        unit="bytes",
                        collected_at=now,
                        labels={"source": "v$recovery_file_dest"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="storage.fra.file_count",
                        value=int(data.get("NUMBER_OF_FILES", 0) or 0),
                        collected_at=now,
                        labels={"source": "v$recovery_file_dest"},
                    ),
                ])
        except oracledb.Error as e:
            logger.error("FRA usage probe failed for %s: %s", database_id, e)
        finally:
            cursor.close()
        return observations

    async def probe_fra_detail(self, connection: oracledb.Connection, database_id: str) -> list[Observation]:
        """Collect FRA usage by file type."""
        observations = []
        cursor = connection.cursor()
        try:
            cursor.execute(self.FRA_DETAIL_SQL)
            columns = [col[0] for col in cursor.description]
            now = datetime.utcnow()
            for row in cursor.fetchall():
                data = dict(zip(columns, row))
                file_type = data.get("FILE_TYPE", "unknown")
                observations.append(Observation(
                    database_id=database_id,
                    metric="storage.fra.usage_by_type",
                    value=float(data.get("PERCENT_SPACE_USED", 0) or 0),
                    unit="percent",
                    collected_at=now,
                    labels={"file_type": file_type, "source": "v$flash_recovery_area_usage"},
                ))
        except oracledb.Error as e:
            logger.error("FRA detail probe failed for %s: %s", database_id, e)
        finally:
            cursor.close()
        return observations

    async def probe_archive_dest(self, connection: oracledb.Connection, database_id: str) -> list[Observation]:
        """Collect archive destination status."""
        observations = []
        cursor = connection.cursor()
        try:
            cursor.execute(self.ARCHIVE_DEST_SQL)
            columns = [col[0] for col in cursor.description]
            now = datetime.utcnow()
            for row in cursor.fetchall():
                data = dict(zip(columns, row))
                dest = data.get("DESTINATION", "unknown")
                observations.append(Observation(
                    database_id=database_id,
                    metric="storage.archive.dest_status",
                    value=data.get("STATUS", ""),
                    collected_at=now,
                    labels={"destination": str(dest), "source": "v$archive_dest_status"},
                ))
        except oracledb.Error as e:
            logger.error("Archive dest probe failed for %s: %s", database_id, e)
        finally:
            cursor.close()
        return observations

    async def probe_resource_limits(self, connection: oracledb.Connection, database_id: str) -> list[Observation]:
        """Collect resource limit metrics."""
        observations = []
        cursor = connection.cursor()
        try:
            cursor.execute(self.RESOURCE_LIMIT_SQL)
            columns = [col[0] for col in cursor.description]
            now = datetime.utcnow()
            for row in cursor.fetchall():
                data = dict(zip(columns, row))
                resource = data.get("RESOURCE_NAME", "unknown")
                current = data.get("CURRENT_UTILIZATION", 0) or 0
                maximum = data.get("MAX_UTILIZATION", 0) or 0
                limit_val = data.get("LIMIT_VALUE", 0) or 0

                observations.extend([
                    Observation(
                        database_id=database_id,
                        metric="resources.limit.current",
                        value=int(current),
                        collected_at=now,
                        labels={"resource": resource, "source": "v$resource_limit"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="resources.limit.maximum",
                        value=int(maximum),
                        collected_at=now,
                        labels={"resource": resource, "source": "v$resource_limit"},
                    ),
                    Observation(
                        database_id=database_id,
                        metric="resources.limit.value",
                        value=int(limit_val),
                        collected_at=now,
                        labels={"resource": resource, "source": "v$resource_limit"},
                    ),
                ])
        except oracledb.Error as e:
            logger.error("Resource limit probe failed for %s: %s", database_id, e)
        finally:
            cursor.close()
        return observations

    async def probe_configuration(self, connection: oracledb.Connection, database_id: str) -> list[Observation]:
        """Collect configuration parameter values."""
        observations = []
        cursor = connection.cursor()
        try:
            cursor.execute(self.CONFIG_PARAMS_SQL)
            columns = [col[0] for col in cursor.description]
            now = datetime.utcnow()
            for row in cursor.fetchall():
                data = dict(zip(columns, row))
                param_name = data.get("NAME", "unknown")
                param_value = data.get("VALUE", "")
                observations.append(Observation(
                    database_id=database_id,
                    metric="config.parameter",
                    value=str(param_value) if param_value is not None else "",
                    collected_at=now,
                    labels={"parameter": param_name, "source": "v$parameter"},
                ))
        except oracledb.Error as e:
            logger.error("Configuration probe failed for %s: %s", database_id, e)
        finally:
            cursor.close()
        return observations

    async def probe_temp_usage(self, connection: oracledb.Connection, database_id: str) -> list[Observation]:
        """Collect temporary tablespace usage."""
        observations = []
        cursor = connection.cursor()
        try:
            cursor.execute(self.TEMP_USAGE_SQL)
            columns = [col[0] for col in cursor.description]
            now = datetime.utcnow()
            for row in cursor.fetchall():
                data = dict(zip(columns, row))
                ts_name = data.get("TABLESPACE_NAME", "unknown")
                observations.append(Observation(
                    database_id=database_id,
                    metric="storage.temp.used_pct",
                    value=float(data.get("USED_PCT", 0) or 0),
                    unit="percent",
                    collected_at=now,
                    labels={"tablespace": ts_name, "source": "v$temp_space_header"},
                ))
        except oracledb.Error as e:
            logger.error("Temp usage probe failed for %s: %s", database_id, e)
        finally:
            cursor.close()
        return observations
