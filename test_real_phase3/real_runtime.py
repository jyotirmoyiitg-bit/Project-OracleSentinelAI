"""Private runtime helper for Verify_Phase3_Real.ps1; never prints credentials."""

import argparse
import json
import logging
import os
import uuid
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

import oracledb
import yaml
from sentinel.config.settings import Settings
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


def load_settings() -> Settings:
    """Load root credentials plus Phase 3 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase3\\.env are required")
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
    rules_dir = Path(settings.rules_path)
    if not rules_dir.is_absolute():
        rules_dir = ROOT / rules_dir
    if not rules_dir.is_dir() or not list(rules_dir.glob("*.yaml")):
        raise ValueError("RULES_PATH must point at a directory containing at least one *.yaml rule file")
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
    print("Application schema upgraded to Alembic head (through the Phase 3 alert/incident tables).", flush=True)


def simulate_alert(settings: Settings, alias: str, code: str, run_id: str | None = None) -> None:
    """Seed one synthetic alert-log record for the registered target and run exactly one
    alert-evaluation pass through the REAL rules/incidents/notifications pipeline.

    Forcing a genuine Oracle archiver failure (ORA-00257) or similar error condition on a
    live database on demand is unsafe, so this injects a single synthetic
    `DiagnosticEvent` carrying the requested ORA code in place of a live `V$DIAG_ALERT_EXT`
    row. Everything downstream of that point -- deduplication, rule evaluation with
    hysteresis/cooldown, incident fingerprinting/lifecycle, and SMTP notification with
    retry -- is the unmodified production code path exercised against the real PostgreSQL
    application database. Each run uses a uniquely named copy of the configured YAML
    event rule so existing incidents and production cooldowns are never changed.
    Portal alerts are always persisted; SMTP delivery runs only when SMTP_ENABLED=true.
    """
    from sentinel.collectors.alert_flow import AlertEvaluationRunner
    from sentinel.collectors.normalization import utcnow
    from sentinel.domain.models import DiagnosticEvent
    from sentinel.notifications.smtp import SMTPNotifier
    from sentinel.persistence.models import DatabaseTarget, Incident
    from sentinel.rules.yaml_loader import load_rules

    run_id = str(uuid.UUID(run_id)) if run_id is not None else str(uuid.uuid4())
    base_rule_id = {"ORA-00257": "ora_00257", "ORA-01653": "ora_01653", "ORA-04031": "ora_04031"}.get(code)
    rule = next((item for item in load_rules(settings.rules_path) if item.id == base_rule_id), None)
    if rule is None or not rule.enabled or not rule.is_event_rule or not rule.condition.matches(code):
        raise ValueError("Synthetic verification requires an enabled YAML event rule matching the requested ORA code")
    if rule.hysteresis != 1:
        raise ValueError("Single-event synthetic verification requires YAML rule hysteresis=1")
    rule_id = f"verify_phase3_{run_id.replace('-', '')}_{rule.id}"
    if len(rule_id) > 100:
        raise ValueError("Synthetic verification rule id exceeds the 100-character schema limit")

    engine = create_engine(settings.database_url)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with sessions.begin() as session:
            target = session.scalar(select(DatabaseTarget).where(DatabaseTarget.tns_alias == alias))
            if target is None:
                raise ValueError(f"No registered target with TNS alias {alias!r}. Register it first (Verify_Phase2_Real.ps1).")
            target_id = target.id

        class SyntheticAlertAdapter:
            """Injects one synthetic record without creating an Oracle connection."""

            async def poll_events(self, poll_alias: str, checkpoint: dict | None):
                event = DiagnosticEvent(
                    database_id=poll_alias,
                    source_id=f"synthetic_test_real_phase3:{run_id}",
                    occurred_at=utcnow(),
                    severity="critical",
                    code=code,
                    message=f"{code}: synthetic event injected by test_real_phase3 for end-to-end verification.",
                    attributes={"synthetic": True, "run_id": run_id, "base_rule_id": rule.id},
                )
                return [event], dict(checkpoint or {})

        with TemporaryDirectory(prefix="OracleSentinel-Phase3-Rules-") as rules_dir:
            definition = asdict(rule)
            definition.pop("condition")
            definition.update(id=rule_id, message=f"[Synthetic verification] {rule.message}")
            Path(rules_dir, "verification.yaml").write_text(
                yaml.safe_dump({"rules": [definition]}), encoding="utf-8",
            )
            verification_settings = settings.model_copy(update={"rules_path": rules_dir})
            runner = AlertEvaluationRunner(
                sessions, verification_settings, lambda _target: SyntheticAlertAdapter(), SMTPNotifier(settings),
            )
            outcome = runner.run(target_id)
        incident_id = None
        if outcome == "success":
            with sessions() as session:
                incident_id = session.execute(select(Incident.id).where(
                    Incident.target_id == target_id, Incident.rule_id == rule_id,
                )).scalar_one_or_none()
            if incident_id is None:
                raise ValueError("Synthetic poll completed but created no verification incident; inspect rule configuration")
        print(json.dumps({
            "target_id": str(target_id), "run_outcome": outcome, "run_id": run_id,
            "rule_id": rule_id, "incident_id": str(incident_id) if incident_id else None,
        }), flush=True)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "serve", "simulate-alert"))
    parser.add_argument("--alias", help="TNS alias of the already-registered target (simulate-alert)")
    parser.add_argument("--code", default="ORA-00257", help="Synthetic ORA code to inject (simulate-alert)")
    parser.add_argument("--run-id", help="Verification run UUID; reuse only for lease retries or dedup checks")
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
    elif args.action == "simulate-alert":
        if not args.alias:
            raise ValueError("simulate-alert requires --alias <registered TNS alias>")
        simulate_alert(settings, args.alias, args.code, args.run_id)
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
