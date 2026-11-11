"""Private runtime helper for Verify_Phase4_Real.ps1; never prints credentials."""

import argparse
import json
import logging
import os
import uuid
from pathlib import Path

import oracledb
from sentinel.config.settings import Settings
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


def load_settings() -> Settings:
    """Load root credentials plus Phase 4 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase4\\.env are required")
    # Clear only model-field overrides in this child, not arbitrary env: secret
    # variables. This prevents a previous demo shell from overriding live settings.
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if make_url(settings.database_url).get_backend_name() != "postgresql":
        raise ValueError("Live verification requires a PostgreSQL DATABASE_URL")
    tns = Path(settings.tns_admin or "tns")
    if not tns.is_absolute():
        tns = ROOT / tns
    if not (tns / "tnsnames.ora").is_file():
        raise ValueError("TNS_ADMIN must contain tnsnames.ora")
    settings.tns_admin = str(tns.resolve())
    if not settings.oracle_password and not settings.oracle_password_secret_ref:
        raise ValueError("Configure ORACLE_PASSWORD or ORACLE_PASSWORD_SECRET_REF in the root .env")
    # API module creation and Alembic call get_settings too. Ensure they see the
    # same absolute TNS directory while preserving the explicit file precedence.
    os.environ["TNS_ADMIN"] = settings.tns_admin
    return settings


def migrate(settings: Settings) -> None:
    from alembic import command
    from alembic.config import Config

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            if "database_targets" in tables and "alembic_version" not in tables:
                raise ValueError(
                    "Unversioned existing schema detected. Back up and compare the Phase 1 baseline "
                    "before manually stamping 0001; this launcher never stamps automatically."
                )
    finally:
        engine.dispose()
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    command.upgrade(config, "head")
    print("Application schema upgraded to Alembic head (through the Phase 4 compliance/forecast tables).", flush=True)


def simulate_noncompliance(settings: Settings, alias: str, result: str, run_id: str | None = None) -> None:
    """Drive the real non-compliance notification pipeline without touching Oracle.

    Forcing a genuine non-compliant state (e.g. disabling ARCHIVELOG, removing OMF) on a
    live production database on demand is unsafe, so this calls the production
    `sync_compliance_notifications()` directly with one synthetic `PolicyEvaluation` for a
    uniquely named test-only policy id, through the real database session. Everything it
    exercises -- incident fingerprinting/dedup, the Phase 3 incident lifecycle, the portal
    `Notification` row, and optional SMTP dispatch with retry -- is the unmodified
    production code path running against the real PostgreSQL application database.
    Because the policy id is unique to this run, it can never collide with the real
    catalog policies the scheduler evaluates automatically after instance/configuration/
    fra_archive polls. Reuse the same --run-id across calls to exercise dedup and the
    pass-resolves / fail-after-resolve-reopens lifecycle.
    """
    from sentinel.collectors.normalization import utcnow
    from sentinel.notifications.smtp import SMTPNotifier
    from sentinel.persistence.models import DatabaseTarget, Incident
    from sentinel.rules.compliance import PolicyEvaluation
    from sentinel.rules.compliance_store import sync_compliance_notifications

    if result not in ("fail", "pass"):
        raise ValueError("simulate-noncompliance --result must be 'fail' or 'pass'")
    run_id = str(uuid.UUID(run_id)) if run_id is not None else str(uuid.uuid4())
    policy_id = f"test_real_phase4_{run_id.replace('-', '')}"
    rule_id = f"compliance.{policy_id}"

    engine = create_engine(settings.database_url)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with sessions.begin() as session:
            target = session.scalar(select(DatabaseTarget).where(DatabaseTarget.tns_alias == alias))
            if target is None:
                raise ValueError(f"No registered target with TNS alias {alias!r}. Register it first (Verify_Phase2_Real.ps1).")
            target_id = target.id
            evaluation = PolicyEvaluation(
                policy_id=policy_id, version="1", result=result, severity="critical",
                evidence={"synthetic": True, "run_id": run_id, "source": "test_real_phase4"},
                recommended_action=(
                    "This is a synthetic test_real_phase4 policy injected for live verification; "
                    "no real database remediation is required."
                ),
            )
            sync_compliance_notifications(session, target, [evaluation], settings, SMTPNotifier(settings), utcnow())
        with sessions() as session:
            # A resolved incident and its post-resolution successor share the same rule_id,
            # so always pick the most recently created one (mirrors IncidentLifecycle.open_or_update's
            # own lookup ordering) -- otherwise an unordered query can non-deterministically
            # return the earlier, already-resolved incident instead of the fresh one.
            incident = session.scalar(select(Incident).where(
                Incident.target_id == target_id, Incident.rule_id == rule_id,
            ).order_by(Incident.created_at.desc()))
        print(json.dumps({
            "target_id": str(target_id), "run_id": run_id, "policy_id": policy_id, "rule_id": rule_id,
            "result": result, "incident_id": str(incident.id) if incident else None,
            "incident_state": incident.state if incident else None,
        }), flush=True)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "serve", "simulate-noncompliance"))
    parser.add_argument("--alias", help="TNS alias of the already-registered target (simulate-noncompliance)")
    parser.add_argument("--result", default="fail", help="Synthetic policy result to inject: fail or pass")
    parser.add_argument("--run-id", help="Verification run UUID; reuse to exercise dedup/resolve/reopen")
    args = parser.parse_args()
    settings = load_settings()
    if args.action == "preflight":
        aliases = oracledb.ConnectParams(config_dir=settings.tns_admin).get_network_service_names()
        if not aliases:
            raise ValueError("No TNS aliases found in tnsnames.ora")
        # Return names only, never descriptor hostnames, usernames or passwords.
        print(json.dumps({"aliases": sorted(aliases)}), flush=True)
    elif args.action == "migrate":
        migrate(settings)
    elif args.action == "simulate-noncompliance":
        if not args.alias:
            raise ValueError("simulate-noncompliance requires --alias <registered TNS alias>")
        simulate_noncompliance(settings, args.alias, args.result, args.run_id)
    else:
        import uvicorn
        from sentinel.main import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, SQLAlchemyError, OSError, oracledb.Error) as exc:
        # Driver/validation exception text can embed a credential-bearing URL.
        # Our explicit ValueErrors are actionable and safe; never print driver text.
        if type(exc) is ValueError:
            logger.error("%s", exc)
        else:
            logger.error("Live preparation failed (%s). Check database access and local configuration.", type(exc).__name__)
        raise SystemExit(1) from None
