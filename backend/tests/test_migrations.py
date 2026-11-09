from io import StringIO

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from alembic import command
from sentinel.persistence.models import Base


def test_fresh_migration_downgrade_and_existing_registry_upgrade(migration_config, settings):
    command.upgrade(migration_config, "0001")
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO database_targets (id,engine,display_name) "
            "VALUES ('00000000000000000000000000000001','oracle','existing')"
        ))
    command.upgrade(migration_config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT display_name FROM database_targets")) == "existing"
        assert "poll_runs" in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    command.downgrade(migration_config, "0001")
    command.upgrade(migration_config, "head")
    engine.dispose()


def test_postgresql_migration_sql_contains_jsonb_and_indexes(migration_config):
    output = StringIO()
    migration_config.output_buffer = output
    migration_config.attributes["database_url"] = "postgresql+psycopg2://localhost/sentinel_validation"
    command.upgrade(migration_config, "head", sql=True)
    sql = output.getvalue()
    assert "ALTER COLUMN cursor TYPE JSONB" in sql
    assert "CREATE INDEX ix_observations_target_metric_time" in sql
    assert "CREATE TABLE poll_runs" in sql
