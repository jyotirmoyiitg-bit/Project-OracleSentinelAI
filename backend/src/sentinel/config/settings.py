"""Typed application settings using pydantic-settings."""

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

from sentinel.knowledge.constants import EMBEDDING_DIMENSIONS


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    # Application
    app_name: str = "Oracle Sentinel AI"
    app_version: str = "0.1.0"
    app_env: str = Field(default="dev", description="dev, test, or production. Model switching is off in production.")
    debug: bool = False
    secret_key: str = Field(default="change-me-in-production", description="Secret key for signing tokens")

    # PostgreSQL
    database_url: str = Field(
        default="postgresql+psycopg2://postgres:postgres@localhost:5432/sentinel",
        description="PostgreSQL connection string with pgvector",
    )
    database_pool_size: int = 5
    database_max_overflow: int = 10

    # Oracle
    tns_admin: str | None = Field(
        default=None,
        description="Path to directory containing tnsnames.ora",
    )
    oracle_username: str = Field(default="GSYSTEM", description="Default Oracle monitoring username")
    oracle_password: str | None = Field(
        default=None,
        description="Oracle GSYSTEM password. Can be plain-text or Fernet-encrypted (starts with gAAAAA). "
        "Use `python scripts/encrypt-secret.py` to encrypt. The app decrypts it at startup.",
    )
    oracle_password_secret_ref: str | None = Field(
        default=None,
        description="Secret reference for Oracle password (never the actual password)",
    )
    oracle_connection_timeout: int = Field(default=30, description="Oracle connection timeout in seconds")
    oracle_statement_timeout: int = Field(default=60, description="Oracle statement deadline in seconds")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis connection string")

    # AI / LLM
    ai_enabled: bool = Field(default=False, description="Enable AI copilot features")
    ai_provider: str = Field(default="disabled", description="AI provider: local, hosted, or disabled")
    ai_local_url: str = Field(default="http://localhost:11434", description="Ollama local URL")
    ai_hosted_api_key_secret_ref: str | None = Field(
        default=None,
        description="Secret reference for hosted LLM API key",
    )
    ai_model_name: str = Field(default="llama3", description="Local model name")
    ai_timeout_seconds: int = Field(default=30, description="LLM request timeout")
    ai_fallback_provider: str = Field(
        default="disabled",
        description="Only disabled is allowed. A local outage must not send data to a hosted model.",
    )
    ai_hosted_vendor: str = Field(
        default="openai",
        description="Hosted API shape: openai or anthropic. Used only when ai_provider is hosted.",
    )
    ai_hosted_api_base: str = Field(
        default="",
        description="Hosted base URL. Empty uses https://api.openai.com/v1 or https://api.anthropic.com.",
    )
    ai_hosted_model: str = Field(default="", description="Hosted model name. Empty uses no hosted model name.")
    ai_max_tool_calls: int = Field(default=5, ge=1, le=8)
    ai_max_output_tokens: int = Field(default=600, ge=64, le=4000)
    ai_max_context_tokens: int = Field(default=12000, ge=500, le=32000)
    ai_require_citations: bool = Field(default=True)
    ai_redact_sensitive_telemetry: bool = Field(default=True)
    ai_hosted_approved: bool = Field(
        default=False,
        description="Hosted chat stays disabled until this is set with a model, secret reference, and disclosure",
    )
    ai_hosted_privacy_disclosure: str = Field(default="")
    ai_openai_model: str = Field(default="gpt-4o", description="OpenAI model used when the dev admin selects OpenAI")
    ai_openai_api_base: str = Field(default="", description="OpenAI base URL. Empty uses https://api.openai.com/v1.")
    ai_openai_api_key_secret_ref: str = Field(
        default="secret://OPENAI_API_KEY",
        description="Secret reference for the OpenAI key. The value stays in OPENAI_API_KEY.",
    )
    ai_openai_privacy_disclosure: str = Field(
        default="",
        description="Shown when the dev admin selects OpenAI. Empty uses the OpenAI sentence.",
    )
    ai_openai_timeout_seconds: int = Field(default=0, ge=0, le=120, description="OpenAI timeout. 0 uses ai_timeout_seconds.")
    ai_openai_max_output_tokens: int = Field(default=0, ge=0, le=4000, description="OpenAI output limit. 0 uses ai_max_output_tokens.")
    ai_anthropic_model: str = Field(default="", description="Claude model used when the dev admin selects Anthropic.")
    ai_anthropic_api_base: str = Field(default="", description="Anthropic base URL. Empty uses the configured hosted base or https://api.anthropic.com.")
    ai_anthropic_api_key_secret_ref: str = Field(
        default="",
        description="Secret reference for the Anthropic key. The value stays in ANTHROPIC_API_KEY.",
    )
    ai_anthropic_privacy_disclosure: str = Field(
        default="",
        description="Shown when the dev admin selects Claude. Empty uses the configured hosted disclosure.",
    )
    ai_anthropic_timeout_seconds: int = Field(default=0, ge=0, le=120, description="Anthropic timeout. 0 uses ai_timeout_seconds.")
    ai_anthropic_max_output_tokens: int = Field(default=0, ge=0, le=4000, description="Anthropic output limit. 0 uses ai_max_output_tokens.")
    ai_local_model: str = Field(default="", description="Local model used when the dev admin selects local. Empty uses ai_model_name.")
    ai_local_api_key_secret_ref: str = Field(
        default="",
        description="Optional secret reference for the local endpoint. Empty sends no API key.",
    )
    ai_local_privacy_disclosure: str = Field(
        default="",
        description="Shown when the dev admin selects the local model. Empty uses the local-endpoint sentence.",
    )
    ai_local_timeout_seconds: int = Field(default=0, ge=0, le=120, description="Local timeout. 0 uses ai_timeout_seconds.")
    ai_local_max_output_tokens: int = Field(default=0, ge=0, le=4000, description="Local output limit. 0 uses ai_max_output_tokens.")

    # SMTP Notifications
    smtp_enabled: bool = Field(default=False, description="Opt in to SMTP email delivery; portal alerts remain enabled")
    smtp_host: str | None = Field(default=None, description="SMTP server hostname")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_username: str | None = Field(default=None, description="SMTP username")
    smtp_password_secret_ref: str | None = Field(default=None, description="Secret reference for SMTP password")
    smtp_use_tls: bool = Field(default=True, description="Use TLS for SMTP")
    smtp_from_address: str | None = Field(default=None, description="From address for notifications")
    notification_recipients: list[str] = Field(
        default_factory=list, description="Default recipient email addresses for incident notifications",
    )
    notification_max_retries: int = Field(default=3, ge=0, description="Max SMTP send retries per notification")
    notification_retry_backoff_seconds: float = Field(
        default=5.0, ge=0, description="Base exponential backoff delay between SMTP retries",
    )

    # Alert-log rules and incidents
    rules_path: str = Field(default="./rules", description="Path to YAML rule definition files")
    alert_poll_interval_seconds: int = Field(default=120, ge=10, description="Alert-log polling and rule evaluation interval")
    alert_max_lookback_hours: int = Field(default=24, ge=1, description="Maximum alert-log backfill lookback")

    # Scheduler
    scheduler_enabled: bool = Field(default=False, description="Enable APScheduler collection jobs")
    scheduler_default_interval_seconds: int = Field(default=300, description="Default polling interval in seconds")
    scheduler_workers: int = Field(default=4, ge=1, le=32)
    scheduler_reconcile_seconds: int = Field(default=30, ge=1)
    collector_lease_seconds: int = Field(default=1800, ge=60)
    observation_retention_days: int = Field(default=30, ge=1)
    poll_intervals: dict[str, int] = Field(default_factory=lambda: {
        "connectivity": 60, "instance": 300, "storage": 300,
        "fra_archive": 180, "resources": 180, "configuration": 1800,
    })
    stale_after_seconds: dict[str, int] = Field(default_factory=lambda: {
        "connectivity": 180, "instance": 900, "storage": 900,
        "fra_archive": 540, "resources": 540, "configuration": 5400,
    })
    synthetic_mode: bool = Field(default=False, description="Use synthetic adapters; never connect to Oracle")

    @model_validator(mode="after")
    def validate_collection_intervals(self) -> Settings:
        probes = {"connectivity", "instance", "storage", "fra_archive", "resources", "configuration"}
        for name, values in (("poll_intervals", self.poll_intervals),
                             ("stale_after_seconds", self.stale_after_seconds)):
            if set(values) != probes or any(isinstance(v, bool) or v < 1 for v in values.values()):
                raise ValueError(f"{name} must contain a positive interval for each of {sorted(probes)}")
        return self

    # Knowledge ingestion and local embeddings
    knowledge_intake_path: str = Field(
        default="./knowledge-intake",
        description="Path to knowledge intake folders",
    )
    knowledge_enabled: bool = Field(default=False, description="Schedule scans of the knowledge intake folders")
    knowledge_scan_interval_seconds: int = Field(default=300, ge=30, le=86400)
    knowledge_max_file_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    knowledge_max_pages: int = Field(default=800, ge=1)
    knowledge_parser_timeout_seconds: float = Field(default=30.0, ge=0.01, le=120)
    knowledge_min_extracted_chars: int = Field(default=20, ge=1)
    knowledge_chunk_size: int = Field(default=800, ge=40)
    knowledge_chunk_overlap: int = Field(default=120, ge=0)
    knowledge_malware_scanner: str = Field(
        default="",
        description="Optional scanner executable. It receives the file path as its only argument; exit code 1 quarantines.",
    )
    embedding_provider: str = Field(default="local", description="local or explicitly approved remote")
    embedding_model: str = Field(default="local-hashing-v1")
    embedding_dimensions: int = Field(default=EMBEDDING_DIMENSIONS)
    embedding_remote_url: str = Field(default="")
    embedding_remote_approved: bool = Field(
        default=False,
        description="Remote embeddings stay disabled until this is set with a privacy disclosure",
    )
    embedding_remote_privacy_disclosure: str = Field(default="")

    # Logging
    log_level: str = Field(default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR")

    # CORS
    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"],
        description="Allowed CORS origins",
    )

    @model_validator(mode="after")
    def validate_knowledge(self) -> Settings:
        if self.embedding_dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError(
                "embedding_dimensions is fixed at "
                f"{EMBEDDING_DIMENSIONS} until a migration changes the pgvector column",
            )
        if self.embedding_provider not in {"local", "remote"}:
            raise ValueError("embedding_provider must be local or remote")
        if self.knowledge_chunk_overlap >= self.knowledge_chunk_size:
            raise ValueError("knowledge_chunk_overlap must be smaller than knowledge_chunk_size")
        if self.embedding_provider == "remote" and (
            not self.embedding_remote_approved
            or not self.embedding_remote_url.strip()
            or not self.embedding_remote_privacy_disclosure.strip()
        ):
            raise ValueError(
                "Remote embeddings require embedding_remote_approved, embedding_remote_url, "
                "and embedding_remote_privacy_disclosure",
            )
        return self

    auth_required: bool = Field(default=False, description="Require an operator sign-in before API use")
    auth_session_hours: int = Field(default=8, ge=1, le=24)
    auth_cookie_secure: bool = Field(default=False, description="Mark the session cookie Secure")
    auth_password_iterations: int = Field(default=200_000, ge=1000, le=1_000_000)
    auth_bootstrap_username: str = Field(default="", description="Local account created once when sign-in is enabled")
    auth_bootstrap_password: str = Field(default="")
    auth_bootstrap_role: str = Field(default="admin")
    auth_ad_url: str = Field(default="", description="ldap:// or ldaps:// domain controller")
    auth_ad_domain: str = Field(default="", description="Active Directory DNS name, such as contoso.com")
    auth_ad_base_dn: str = Field(default="", description="Search base; derived from the domain when empty")
    auth_ad_operator_group: str = Field(default="")
    auth_ad_dba_group: str = Field(default="")
    auth_ad_admin_group: str = Field(default="")
    auth_oidc_issuer: str = Field(default="", description="Enterprise IdP issuer, or empty when OIDC is off")
    auth_oidc_client_id: str = Field(default="")
    auth_oidc_client_secret_ref: str = Field(default="", description="secret:// reference; never the client secret")
    auth_oidc_redirect_uri: str = Field(default="http://127.0.0.1:8000/api/v1/auth/oidc/callback")
    auth_oidc_success_url: str = Field(default="http://127.0.0.1:5173/")
    proposal_expiry_hours: int = Field(default=24)
    operations_execution_enabled: bool = Field(
        default=False,
        description="Privileged execution stays off in Release 1. Turning this on also requires operations_execution_approved.",
    )
    operations_execution_approved: bool = Field(
        default=False,
        description="Explicit review acknowledgement. Release 1 still does not execute a statement.",
    )
    operations_require_dual_approval: bool = Field(
        default=True,
        description="Future approval rule. Release 1 does not execute even when this is set.",
    )
    log_json: bool = Field(default=False, description="Emit one JSON object per log line")

    @model_validator(mode="after")
    def validate_ai(self) -> Settings:
        if self.ai_provider not in {"local", "hosted", "disabled"}:
            raise ValueError("ai_provider must be local, hosted, or disabled")
        if self.ai_hosted_vendor not in {"openai", "anthropic"}:
            raise ValueError("ai_hosted_vendor must be openai or anthropic")
        if self.app_env.strip().lower() not in {"dev", "test", "production"}:
            raise ValueError("app_env must be dev, test, or production")
        self.app_env = self.app_env.strip().lower()
        if self.ai_fallback_provider != "disabled":
            raise ValueError("ai_fallback_provider must stay disabled")
        if self.ai_timeout_seconds < 1 or self.ai_timeout_seconds > 120:
            raise ValueError("ai_timeout_seconds must be between 1 and 120")
        for name in (
            "ai_openai_max_output_tokens",
            "ai_anthropic_max_output_tokens",
            "ai_local_max_output_tokens",
        ):
            limit = getattr(self, name)
            if limit != 0 and limit < 64:
                raise ValueError(f"{name} must be 0 or at least 64")
        return self

    @model_validator(mode="after")
    def validate_auth(self) -> Settings:
        if self.auth_ad_url.strip() and not self.auth_ad_url.lower().startswith(("ldap://", "ldaps://")):
            raise ValueError("auth_ad_url must start with ldap:// or ldaps://")
        if self.auth_bootstrap_role not in {
            "viewer", "operator", "dba", "admin", "auditor", "platform_admin", "security_auditor",
        }:
            raise ValueError("auth_bootstrap_role is not a known role")
        if self.auth_oidc_issuer.strip() and not self.auth_oidc_issuer.lower().startswith("https://"):
            raise ValueError("auth_oidc_issuer must start with https://")
        if self.proposal_expiry_hours < 1 or self.proposal_expiry_hours > 168:
            raise ValueError("proposal_expiry_hours must be between 1 and 168")
        if self.operations_execution_enabled and not self.operations_execution_approved:
            raise ValueError(
                "OPERATIONS_EXECUTION_ENABLED requires OPERATIONS_EXECUTION_APPROVED after a security review",
            )
        if self.auth_required and not self.auth_ad_url.strip() and not self.auth_bootstrap_username.strip():
            raise ValueError("Sign-in requires AUTH_AD_URL or AUTH_BOOTSTRAP_USERNAME")
        if self.auth_required and self.auth_bootstrap_username.strip() and len(self.auth_bootstrap_password) < 8:
            raise ValueError("AUTH_BOOTSTRAP_PASSWORD must be at least 8 characters")
        return self

    model_config = {
        "env_file": str(Path(__file__).resolve().parents[4] / ".env"),
        "env_file_encoding": "utf-8", "extra": "ignore",
    }


def get_settings() -> Settings:
    """Get application settings singleton."""
    return Settings()
