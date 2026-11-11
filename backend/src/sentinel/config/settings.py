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
    ai_model_name: str = Field(default="llama3", description="Model name for the LLM provider")
    ai_timeout_seconds: int = Field(default=30, description="LLM request timeout")

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
    knowledge_max_pages: int = Field(default=500, ge=1)
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

    model_config = {
        "env_file": str(Path(__file__).resolve().parents[4] / ".env"),
        "env_file_encoding": "utf-8", "extra": "ignore",
    }


def get_settings() -> Settings:
    """Get application settings singleton."""
    return Settings()
