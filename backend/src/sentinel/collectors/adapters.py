"""Explicit adapter selection; synthetic mode is isolated from live credentials."""

import os

from sentinel.adapters.oracle.adapter import OracleAdapter
from sentinel.adapters.oracle.synthetic import SyntheticOracleAdapter
from sentinel.config.settings import Settings
from sentinel.persistence.models import DatabaseTarget


def adapter_for(target: DatabaseTarget, settings: Settings) -> OracleAdapter | SyntheticOracleAdapter:
    if target.engine != "oracle":
        raise ValueError("Phase 2 supports Oracle targets only")
    if settings.synthetic_mode:
        return SyntheticOracleAdapter(
            simulate_error=target.tns_alias == "SYNTHETIC_OFFLINE",
            simulate_permission_denied=target.tns_alias == "SYNTHETIC_DENIED",
            used_pct=97.0 if target.tns_alias == "SYNTHETIC_CRITICAL" else 65.5,
            container_name="SYNTHETIC_PDB",
        )
    credentials = target.credentials
    reference = credentials.secret_reference if credentials else settings.oracle_password_secret_ref
    if reference:
        if not reference.startswith("env:") or not os.environ.get(reference[4:]):
            raise ValueError("Secret reference must identify a populated env:VARIABLE")
        settings = settings.model_copy(update={
            "oracle_password": os.environ[reference[4:]],
            "oracle_username": credentials.username if credentials else settings.oracle_username,
        })
    return OracleAdapter(settings)
