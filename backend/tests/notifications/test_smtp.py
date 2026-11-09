from sentinel.config.settings import Settings
from sentinel.notifications.smtp import SMTPNotifier


def make_settings(**overrides):
    defaults = dict(
        _env_file=None, smtp_enabled=True, smtp_host="smtp.example.com", smtp_from_address="sentinel@example.com",
        notification_max_retries=2, notification_retry_backoff_seconds=0.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class FakeSMTPClient:
    def __init__(self, send_message_side_effect=None):
        self.sent_messages = []
        self.quit_called = False
        self._side_effect = send_message_side_effect

    def send_message(self, message):
        if self._side_effect is not None:
            effect = self._side_effect
            if callable(effect):
                effect()
        self.sent_messages.append(message)

    def quit(self):
        self.quit_called = True


def test_returns_not_configured_when_smtp_not_set():
    settings = make_settings(smtp_host=None)
    notifier = SMTPNotifier(settings, smtp_client_factory=lambda: FakeSMTPClient())
    sent, error_class = notifier.send("ops@example.com", "subject", "body")
    assert (sent, error_class) == (False, "SMTPNotConfigured")


def test_disabled_smtp_never_creates_client_even_with_relay_configured():
    def factory():
        raise AssertionError("Disabled SMTP must not open a connection")

    notifier = SMTPNotifier(make_settings(smtp_enabled=False), smtp_client_factory=factory)
    assert notifier.send("ops@example.com", "subject", "body") == (False, "SMTPDisabled")


def test_smtp_is_opt_in_and_loads_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_ENABLED", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert Settings(_env_file=None).smtp_enabled is False
    env_file = tmp_path / ".env"
    env_file.write_text("SMTP_ENABLED=true\nSMTP_HOST=relay.example.com\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)
    assert settings.smtp_enabled is True
    assert settings.smtp_host == "relay.example.com"


def test_successful_send_calls_send_message_once_and_quits():
    client = FakeSMTPClient()
    settings = make_settings()
    notifier = SMTPNotifier(settings, smtp_client_factory=lambda: client)
    sent, error_class = notifier.send("ops@example.com", "subject", "body")
    assert (sent, error_class) == (True, None)
    assert len(client.sent_messages) == 1
    assert client.quit_called is True


def test_persistent_failure_retries_max_retries_plus_one_and_sleeps_between_not_after(): # noqa: E501
    settings = make_settings(notification_max_retries=2)
    sleeps = []

    def factory():
        raise ConnectionRefusedError("boom")

    notifier = SMTPNotifier(settings, smtp_client_factory=factory, sleep_fn=sleeps.append)
    sent, error_class = notifier.send("ops@example.com", "subject", "body")
    assert (sent, error_class) == (False, "ConnectionRefusedError")
    assert len(sleeps) == 2  # slept between attempts 1->2 and 2->3, never after the last


def test_retries_until_success_after_transient_failures():
    settings = make_settings(notification_max_retries=2)
    attempts = {"count": 0}
    sleeps = []

    class FlakyThenHealthyFactory:
        def __call__(self):
            attempts["count"] += 1
            if attempts["count"] <= 2:
                raise TimeoutError("transient")
            return FakeSMTPClient()

    notifier = SMTPNotifier(settings, smtp_client_factory=FlakyThenHealthyFactory(), sleep_fn=sleeps.append)
    sent, error_class = notifier.send("ops@example.com", "subject", "body")
    assert (sent, error_class) == (True, None)
    assert attempts["count"] == 3
    assert len(sleeps) == 2


def test_exact_backoff_values_passed_to_sleep_fn():
    settings = make_settings(notification_max_retries=2, notification_retry_backoff_seconds=5.0)
    sleeps = []

    def factory():
        raise RuntimeError("boom")

    notifier = SMTPNotifier(settings, smtp_client_factory=factory, sleep_fn=sleeps.append)
    notifier.send("ops@example.com", "subject", "body")
    assert sleeps == [5.0, 10.0]
