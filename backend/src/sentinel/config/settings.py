"""Typed application settings using pydantic-settings."""

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


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
    tns_admin: Optional[str] = Field(
        default=None,
        description="Path to directory containing tnsnames.ora",
    )
    oracle_username: str = Field(default="GSYSTEM", description="Default Oracle monitoring username")
    oracle_password: Optional[str] = Field(
        default=None,
        description="Oracle GSYSTEM password. Can be plain-text or Fernet-encrypted (starts with gAAAAA). "
                     "Use `python scripts/encrypt-secret.py` to encrypt. The app decrypts it at startup.",
    )
    oracle_connection_timeout: int = Field(default=30, description="Oracle connection timeout in seconds")
    oracle_statement_timeout: int = Field(default=60, description="Oracle statement deadline in seconds")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis connection string")

    # AI / LLM
    ai_enabled: bool = Field(default=False, description="Enable AI copilot features")
    ai_provider: str = Field(default="disabled", description="AI provider: local, hosted, or disabled")
    ai_local_url: str = Field(default="http://localhost:11434", description="Ollama local URL")
    ai_hosted_api_key_secret_ref: Optional[str] = Field(
        default=None,
        description="Secret reference for hosted LLM API key",
    )
    ai_model_name: str = Field(default="llama3", description="Model name for the LLM provider")
    ai_timeout_seconds: int = Field(default=30, description="LLM request timeout")

    # SMTP Notifications
    smtp_host: Optional[str] = Field(default=None, description="SMTP server hostname")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_username: Optional[str] = Field(default=None, description="SMTP username")
    smtp_password_secret_ref: Optional[str] = Field(default=None, description="Secret reference for SMTP password")
    smtp_use_tls: bool = Field(default=True, description="Use TLS for SMTP")
    smtp_from_address: Optional[str] = Field(default=None, description="From address for notifications")

    # Scheduler
    scheduler_enabled: bool = Field(default=False, description="Enable APScheduler collection jobs")
    scheduler_default_interval_seconds: int = Field(default=300, description="Default polling interval in seconds")

    # Knowledge
    knowledge_intake_path: str = Field(
        default="./knowledge-intake",
        description="Path to knowledge intake folders",
    )
    knowledge_enabled: bool = Field(default=False, description="Enable knowledge ingestion")

    # Logging
    log_level: str = Field(default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR")

    # CORS
    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"],
        description="Allowed CORS origins",
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


def get_settings() -> Settings:
    """Get application settings singleton."""
    return Settings()
