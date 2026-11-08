"""Alert-log polling via V$DIAG_ALERT_EXT."""

import logging
import re
from datetime import datetime
from typing import Any

import oracledb

from sentinel.domain.models import DiagnosticEvent

logger = logging.getLogger(__name__)

# Regex to extract ORA- codes from messages
ORA_CODE_PATTERN = re.compile(r"ORA-\d{4,5}", re.IGNORECASE)

# Severity mapping based on message patterns
SEVERITY_PATTERNS = [
    (re.compile(r"\b(error|fatal|critical|shutdown|abort|crash)\b", re.IGNORECASE), "critical"),
    (re.compile(r"\b(warning|ORA-00\d{3})\b", re.IGNORECASE), "warning"),
    (re.compile(r"\b(info|started|completed|success)\b", re.IGNORECASE), "info"),
]


def classify_severity(message: str) -> str:
    """Classify alert message severity based on patterns."""
    for pattern, severity in SEVERITY_PATTERNS:
        if pattern.search(message):
            return severity
    return "info"


def extract_ora_code(message: str) -> str | None:
    """Extract ORA- code from message."""
    match = ORA_CODE_PATTERN.search(message)
    return match.group(0).upper() if match else None


def sanitize_message(message: str) -> str:
    """Sanitize alert message for storage."""
    if not message:
        return ""
    # Remove control characters
    sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", message)
    return sanitized.strip()


class OracleAlertLogPoller:
    """Polls V$DIAG_ALERT_EXT for new alert log entries."""

    # Query for incremental alert log entries
    ALERT_LOG_SQL = """
        SELECT
            record_id,
            originating_timestamp,
            message_text,
            message_type,
            module,
            client_id
        FROM v$diag_alert_ext
        WHERE originating_timestamp > :since
        ORDER BY originating_timestamp ASC, record_id ASC
    """

    def __init__(self, max_page_size: int = 1000, max_lookback_hours: int = 24):
        self.max_page_size = max_page_size
        self.max_lookback_hours = max_lookback_hours

    async def poll(
        self,
        connection: oracledb.Connection,
        database_id: str,
        checkpoint: dict[str, Any] | None = None,
    ) -> tuple[list[DiagnosticEvent], dict[str, Any]]:
        """Poll for new alert log entries since the last checkpoint.
        
        Args:
            connection: Active Oracle connection
            database_id: Target database identifier
            checkpoint: Previous checkpoint with 'since' timestamp and 'last_record_id'
            
        Returns:
            Tuple of (list of DiagnosticEvent, new checkpoint dict)
        """
        events: list[DiagnosticEvent] = []
        cursor = connection.cursor()

        try:
            # Determine the "since" timestamp
            since = datetime.utcnow()
            if checkpoint and "since" in checkpoint:
                since_str = checkpoint["since"]
                if isinstance(since_str, str):
                    since = datetime.fromisoformat(since_str)
                elif isinstance(since_str, datetime):
                    since = since_str

            # Enforce maximum lookback
            min_since = datetime.utcnow()
            # Subtract hours (simple approach)
            from datetime import timedelta
            min_since = min_since - timedelta(hours=self.max_lookback_hours)
            if since < min_since:
                since = min_since

            # Execute query with row factory
            cursor.arraysize = self.max_page_size
            cursor.execute(self.ALERT_LOG_SQL, {"since": since})

            columns = [col[0] for col in cursor.description]
            last_record_id = checkpoint.get("last_record_id", 0) if checkpoint else 0

            for row in cursor.fetchall():
                data = dict(zip(columns, row))
                record_id = data.get("RECORD_ID", 0) or 0

                # Skip already processed records
                if record_id <= last_record_id:
                    continue

                last_record_id = record_id

                # Parse the message
                message_text = data.get("MESSAGE_TEXT", "") or ""
                message_text = sanitize_message(message_text)
                if not message_text:
                    continue

                occurred_at = data.get("ORIGINATING_TIMESTAMP")
                if isinstance(occurred_at, datetime):
                    pass
                else:
                    occurred_at = datetime.utcnow()

                # Classify the event
                severity = classify_severity(message_text)
                ora_code = extract_ora_code(message_text)

                event = DiagnosticEvent(
                    database_id=database_id,
                    source_id=f"alert_log:{record_id}",
                    occurred_at=occurred_at,
                    severity=severity,
                    code=ora_code,
                    message=message_text,
                    attributes={
                        "record_id": record_id,
                        "message_type": data.get("MESSAGE_TYPE", ""),
                        "module": data.get("MODULE", ""),
                        "client_id": data.get("CLIENT_ID", ""),
                    },
                )
                events.append(event)

        except oracledb.Error as e:
            logger.error("Alert log polling failed for %s: %s", database_id, e)
        finally:
            cursor.close()

        # Build new checkpoint
        new_checkpoint = {
            "since": datetime.utcnow().isoformat(),
            "last_record_id": last_record_id,
        }

        logger.info(
            "Alert log poll for %s: %d events since %s",
            database_id,
            len(events),
            since.isoformat(),
        )
        return events, new_checkpoint
