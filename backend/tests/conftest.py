from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from alembic import command
from sentinel.collectors.adapters import adapter_for
from sentinel.collectors.jobs import CollectionRunner
from sentinel.config.settings import Settings
from sentinel.persistence.models import DatabaseTarget

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'sentinel.db'}",
        synthetic_mode=True, scheduler_enabled=False,
    )


@pytest.fixture
def migration_config(settings):
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = settings.database_url
    return config


@pytest.fixture
def sessions(settings, migration_config):
    command.upgrade(migration_config, "head")
    engine = create_engine(settings.database_url)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def make_target(sessions):
    def create(alias="SYNTHETIC_HEALTHY", **kwargs):
        with sessions.begin() as session:
            target = DatabaseTarget(
                engine="oracle", display_name=alias, tns_alias=alias, environment="test", **kwargs,
            )
            session.add(target)
            session.flush()
            return target.id
    return create


@pytest.fixture
def runner(sessions, settings):
    return CollectionRunner(sessions, settings, lambda target: adapter_for(target, settings))
