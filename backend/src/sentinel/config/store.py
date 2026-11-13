"""Copy non-secret settings from the environment into PostgreSQL.

Startup inserts a key that is not stored yet. An existing value stays, including a
value saved by an administrator. Passwords, secret references, and
credential-bearing URLs are never written.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.persistence.models import AppConfig

EXCLUDED_KEYS = frozenset({
    "database_url",
    "redis_url",
    "secret_key",
})
# Fixed for this deployment. An administrator can read them and cannot save a new value.
LOCKED_KEYS = frozenset({
    "app_name",
    "app_version",
    "app_env",
    "debug",
    "database_pool_size",
    "database_max_overflow",
    "tns_admin",
    "oracle_username",
    "cors_origins",
    "rules_path",
    "knowledge_intake_path",
    "knowledge_malware_scanner",
    "synthetic_mode",
    "ai_fallback_provider",
    "embedding_dimensions",
    "embedding_provider",
    "embedding_model",
    "embedding_remote_url",
    "embedding_remote_approved",
    "embedding_remote_privacy_disclosure",
    "operations_execution_enabled",
    "operations_execution_approved",
    "operations_require_dual_approval",
    "auth_required",
    "auth_cookie_secure",
    "auth_bootstrap_username",
    "auth_bootstrap_role",
    "auth_ad_url",
    "auth_ad_domain",
    "auth_ad_base_dn",
    "auth_ad_operator_group",
    "auth_ad_dba_group",
    "auth_ad_admin_group",
    "auth_oidc_issuer",
    "auth_oidc_client_id",
    "auth_oidc_redirect_uri",
    "auth_oidc_success_url",
})
CONFIG_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Application", ("app_name", "app_version", "app_env", "debug", "cors_origins")),
    ("Database", ("database_pool_size", "database_max_overflow")),
    ("Oracle", ("tns_admin", "oracle_username", "oracle_connection_timeout", "oracle_statement_timeout")),
    ("Collection", (
        "scheduler_enabled", "scheduler_default_interval_seconds", "scheduler_workers",
        "scheduler_reconcile_seconds", "collector_lease_seconds", "observation_retention_days",
        "poll_intervals", "stale_after_seconds", "synthetic_mode",
    )),
    ("Alerts", ("rules_path", "alert_poll_interval_seconds", "alert_max_lookback_hours")),
    ("Language model", (
        "ai_enabled", "ai_provider", "ai_fallback_provider", "ai_local_url", "ai_model_name",
        "ai_timeout_seconds", "ai_max_tool_calls", "ai_max_output_tokens", "ai_max_context_tokens",
        "ai_require_citations", "ai_redact_sensitive_telemetry", "ai_hosted_approved", "ai_hosted_vendor",
        "ai_hosted_api_base", "ai_hosted_model", "ai_hosted_privacy_disclosure",
        "ai_local_model", "ai_local_privacy_disclosure", "ai_local_timeout_seconds", "ai_local_max_output_tokens",
        "ai_anthropic_model", "ai_anthropic_api_base", "ai_anthropic_privacy_disclosure",
        "ai_anthropic_timeout_seconds", "ai_anthropic_max_output_tokens",
        "ai_openai_model", "ai_openai_api_base", "ai_openai_privacy_disclosure",
        "ai_openai_timeout_seconds", "ai_openai_max_output_tokens",
    )),
    ("Knowledge", (
        "knowledge_intake_path", "knowledge_enabled", "knowledge_scan_interval_seconds",
        "knowledge_max_file_bytes", "knowledge_max_pages", "knowledge_parser_timeout_seconds",
        "knowledge_min_extracted_chars", "knowledge_chunk_size", "knowledge_chunk_overlap",
        "knowledge_malware_scanner", "embedding_provider", "embedding_model", "embedding_dimensions",
        "embedding_remote_url", "embedding_remote_approved", "embedding_remote_privacy_disclosure",
    )),
    ("Mail", (
        "smtp_enabled", "smtp_host", "smtp_port", "smtp_username", "smtp_use_tls", "smtp_from_address",
        "notification_recipients", "notification_max_retries", "notification_retry_backoff_seconds",
    )),
    ("Sign-in", (
        "auth_required", "auth_session_hours", "auth_cookie_secure", "auth_bootstrap_username",
        "auth_bootstrap_role", "auth_ad_url", "auth_ad_domain", "auth_ad_base_dn",
        "auth_ad_operator_group", "auth_ad_dba_group", "auth_ad_admin_group",
        "auth_oidc_issuer", "auth_oidc_client_id", "auth_oidc_redirect_uri", "auth_oidc_success_url",
    )),
    ("Operations", (
        "proposal_expiry_hours", "operations_execution_enabled", "operations_execution_approved",
        "operations_require_dual_approval",
    )),
    ("Logging", ("log_level", "log_json")),
)
_SECTION_BY_KEY = {key: name for name, keys in CONFIG_SECTIONS for key in keys}


def public_config(settings: Settings) -> dict[str, Any]:
    """Return settings that are safe to store. Secret-shaped values are omitted."""
    stored: dict[str, Any] = {}
    for key, value in settings.model_dump().items():
        if _excluded(key) or _secret_value(value):
            continue
        stored[key] = value
    return stored


class ConfigUpdateError(Exception):
    """An administrator change cannot be stored."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def sync_public_config(session: Session, settings: Settings) -> None:
    """Insert missing public settings and apply the stored values in place."""
    incoming = public_config(settings)
    existing = {row.key: row for row in session.scalars(select(AppConfig))}
    now = utcnow()
    for key, value in incoming.items():
        if key not in existing:
            session.add(AppConfig(key=key, value=value, updated_at=now))
    session.flush()
    _apply(settings, _stored(session))


def update_public_config(session: Session, settings: Settings, key: str, value: Any) -> Any:
    """Validate one public setting, store it, and apply it to the running settings."""
    if not key.isidentifier() or key not in Settings.model_fields or _excluded(key) or key not in _SECTION_BY_KEY:
        raise ConfigUpdateError("That setting cannot be stored.")
    if key in LOCKED_KEYS:
        raise ConfigUpdateError("That setting is fixed for this deployment.")
    if _secret_value(value):
        raise ConfigUpdateError("Passwords and secrets cannot be stored.")
    row = session.get(AppConfig, key)
    if row is None:
        raise ConfigUpdateError("That setting is not in the configuration table.")
    merged = settings.model_dump()
    merged[key] = value
    try:
        checked = Settings.model_validate(merged)
    except ValueError as exc:
        raise ConfigUpdateError("That value is not valid for this setting.") from exc
    validated = getattr(checked, key)
    if _secret_value(validated):
        raise ConfigUpdateError("Passwords and secrets cannot be stored.")
    row.value = validated
    row.updated_at = utcnow()
    session.flush()
    setattr(settings, key, validated)
    return validated


def list_public_config(session: Session) -> list[dict[str, Any]]:
    """Return stored public settings. Secret-shaped rows are left out."""
    items: list[dict[str, Any]] = []
    for row in session.scalars(select(AppConfig).order_by(AppConfig.key)):
        if _excluded(row.key) or _secret_value(row.value):
            continue
        items.append({
            "key": row.key,
            "value": row.value,
            "updated_at": row.updated_at.isoformat(),
            "section": _SECTION_BY_KEY.get(row.key, "Other"),
            "editable": row.key in _SECTION_BY_KEY and row.key not in LOCKED_KEYS,
        })
    section_order = {name: index for index, (name, _keys) in enumerate(CONFIG_SECTIONS)}
    items.sort(key=lambda item: (section_order.get(str(item["section"]), 99), str(item["key"])))
    return items


def _stored(session: Session) -> dict[str, Any]:
    return {row.key: row.value for row in session.scalars(select(AppConfig))}


def _apply(settings: Settings, stored: Mapping[str, Any]) -> None:
    updates = {
        key: value
        for key, value in stored.items()
        if key in Settings.model_fields and not _excluded(key) and not _secret_value(value)
    }
    if not updates:
        return
    merged = settings.model_dump()
    merged.update(updates)
    checked = Settings.model_validate(merged)
    for key in updates:
        setattr(settings, key, getattr(checked, key))


def _excluded(key: str) -> bool:
    lowered = key.lower()
    return key in EXCLUDED_KEYS or "password" in lowered or "secret" in lowered


def _secret_value(value: Any) -> bool:
    if isinstance(value, str):
        text = value.strip()
        lowered = text.lower()
        if text.startswith(("gAAAAA", "sk-")) or lowered.startswith(("secret://", "env:")):
            return True
        remainder = lowered.split("://", 1)[-1] if "://" in lowered else ""
        return "@" in remainder
    if isinstance(value, dict):
        return any(_secret_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_secret_value(item) for item in value)
    return False
