"""SMTP adapter with retry/backoff. Monitoring, rules, and incidents never depend on this
module succeeding — a notification failure is recorded and retried, never raised upward."""

import logging
import os
import smtplib
import time
from email.message import EmailMessage
from typing import Callable

from sentinel.config.settings import Settings

logger = logging.getLogger(__name__)

SMTPClientFactory = Callable[[], smtplib.SMTP]


class SMTPNotifier:
    """Sends one notification email, retrying with exponential backoff on transient failures."""

    def __init__(
        self, settings: Settings, smtp_client_factory: SMTPClientFactory | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self._smtp_client_factory = smtp_client_factory or self._default_client
        self._sleep = sleep_fn

    def _default_client(self) -> smtplib.SMTP:
        if not self.settings.smtp_host:
            raise RuntimeError("SMTP_HOST is not configured")
        client = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30)
        if self.settings.smtp_use_tls:
            client.starttls()
        password = self._resolve_password()
        if self.settings.smtp_username and password:
            client.login(self.settings.smtp_username, password)
        return client

    def _resolve_password(self) -> str | None:
        reference = self.settings.smtp_password_secret_ref
        if not reference:
            return None
        if not reference.startswith("env:"):
            raise ValueError("SMTP password secret reference must identify a populated env:VARIABLE")
        return os.environ.get(reference[4:])

    def send(self, recipient: str, subject: str, body: str) -> tuple[bool, str | None]:
        """Attempt delivery with retries. Returns `(sent, error_class)`."""
        if not self.settings.smtp_enabled:
            return False, "SMTPDisabled"
        if not self.settings.smtp_host or not self.settings.smtp_from_address:
            return False, "SMTPNotConfigured"
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.smtp_from_address
        message["To"] = recipient
        message.set_content(body)

        attempts = max(1, self.settings.notification_max_retries + 1)
        last_error: str | None = None
        for attempt in range(attempts):
            try:
                client = self._smtp_client_factory()
                try:
                    client.send_message(message)
                finally:
                    client.quit()
                return True, None
            except Exception as exc:  # noqa: BLE001 -- SMTP failures must never crash the collector job
                last_error = type(exc).__name__
                logger.warning("SMTP send attempt %d/%d failed: %s", attempt + 1, attempts, last_error)
                if attempt < attempts - 1:
                    self._sleep(self.settings.notification_retry_backoff_seconds * (2 ** attempt))
        return False, last_error
