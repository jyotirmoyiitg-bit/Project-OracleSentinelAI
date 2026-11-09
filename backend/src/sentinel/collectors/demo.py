"""Create a credential-free synthetic fleet in the configured application database."""

import argparse
from typing import Any

from sqlalchemy import select

from sentinel.collectors.adapters import adapter_for
from sentinel.collectors.jobs import CollectionRunner
from sentinel.collectors.normalization import PROBES
from sentinel.config.settings import Settings
from sentinel.persistence import session as persistence
from sentinel.persistence.models import DatabaseTarget


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True, help="Dedicated test database, never a production database")
    args = parser.parse_args()
    options: dict[str, Any] = {"_env_file": None, "database_url": args.database_url, "synthetic_mode": True}
    settings = Settings(**options)
    persistence.init_db(settings)
    sessions, engine = persistence.SessionLocal, persistence.engine
    if sessions is None or engine is None:
        raise RuntimeError("Database initialization failed")
    runner = CollectionRunner(sessions, settings, lambda target: adapter_for(target, settings))
    for alias, name in (
        ("SYNTHETIC_HEALTHY", "Demo healthy"), ("SYNTHETIC_CRITICAL", "Demo critical"),
        ("SYNTHETIC_OFFLINE", "Demo offline"), ("SYNTHETIC_DENIED", "Demo missing permissions"),
    ):
        with sessions.begin() as session:
            target = session.scalar(select(DatabaseTarget).where(DatabaseTarget.tns_alias == alias))
            if target is None:
                target = DatabaseTarget(engine="oracle", display_name=name, tns_alias=alias, environment="demo")
                session.add(target)
                session.flush()
            target_id = target.id
        for probe in PROBES:
            print(f"{name}: {probe}: {runner.run(target_id, probe)}")
    engine.dispose()


if __name__ == "__main__":
    main()
