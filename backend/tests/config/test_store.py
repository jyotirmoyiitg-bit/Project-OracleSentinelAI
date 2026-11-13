"""Non-secret configuration is stored in PostgreSQL and secrets stay out."""

import pytest
from sqlalchemy import select

from sentinel.config.settings import Settings
from sentinel.config.store import ConfigUpdateError, public_config, sync_public_config, update_public_config
from sentinel.persistence.models import AppConfig


def _settings(tmp_path, **updates: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": f"sqlite:///{tmp_path / 'sentinel.db'}",
        "secret_key": "test-secret-key",
        "oracle_password": "gAAAAA-oracle-password",
        "oracle_password_secret_ref": "secret://ORACLE_PASSWORD",
        "ai_hosted_api_key_secret_ref": "secret://ANTHROPIC_API_KEY",
        "auth_bootstrap_password": "local-password",
        "redis_url": "redis://user:hidden@localhost:6379/0",
        "scheduler_enabled": False,
        "synthetic_mode": True,
    }
    values.update(updates)
    return Settings(**values)


def test_public_config_omits_passwords_and_secret_references(tmp_path) -> None:
    exposed = public_config(_settings(tmp_path, tns_admin="sk-not-a-path"))
    assert "scheduler_enabled" in exposed
    assert "oracle_username" in exposed
    for key in (
        "database_url", "redis_url", "secret_key", "oracle_password",
        "oracle_password_secret_ref", "ai_hosted_api_key_secret_ref", "auth_bootstrap_password",
        "tns_admin",
    ):
        assert key not in exposed
    assert all("secret://" not in str(value) and not str(value).startswith("gAAAAA") for value in exposed.values())


def test_development_startup_keeps_an_edited_value(tmp_path, sessions) -> None:
    original = _settings(tmp_path, app_env="dev", scheduler_enabled=False)
    with sessions.begin() as session:
        sync_public_config(session, original)
        rows = {row.key: row.value for row in session.scalars(select(AppConfig))}
        update_public_config(session, original, "scheduler_enabled", True)
    assert "oracle_password" not in rows
    assert "database_url" not in rows
    restarted = _settings(tmp_path, app_env="dev", scheduler_enabled=False)
    with sessions.begin() as session:
        sync_public_config(session, restarted)
        stored = session.get(AppConfig, "scheduler_enabled")
        assert stored is not None
        assert stored.value is True
    assert restarted.scheduler_enabled is True


def test_a_fixed_setting_cannot_be_changed(tmp_path, sessions) -> None:
    original = _settings(tmp_path, app_env="dev")
    with sessions.begin() as session:
        sync_public_config(session, original)
        with pytest.raises(ConfigUpdateError, match="fixed"):
            update_public_config(session, original, "ai_fallback_provider", "hosted")
        with pytest.raises(ConfigUpdateError, match="fixed"):
            update_public_config(session, original, "operations_execution_enabled", True)
    assert original.ai_fallback_provider == "disabled"
    assert original.operations_execution_enabled is False


def test_an_admin_cannot_store_a_secret(tmp_path, sessions) -> None:
    original = _settings(tmp_path)
    with sessions.begin() as session:
        sync_public_config(session, original)
        with pytest.raises(ConfigUpdateError):
            update_public_config(session, original, "oracle_password", "hidden")
        with pytest.raises(ConfigUpdateError):
            update_public_config(session, original, "log_level", "secret://ORACLE_PASSWORD")


def test_production_keeps_the_stored_value(tmp_path, sessions) -> None:
    with sessions.begin() as session:
        sync_public_config(session, _settings(tmp_path, app_env="production", log_level="INFO"))
    restarted = _settings(tmp_path, app_env="production", log_level="WARNING")
    with sessions.begin() as session:
        sync_public_config(session, restarted)
    assert restarted.log_level == "INFO"
