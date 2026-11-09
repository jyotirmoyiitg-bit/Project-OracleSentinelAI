"""Central collection state, latest projection and indexed observation history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("database_targets", sa.Column("health_valid_until", sa.DateTime()))
    op.add_column("database_targets", sa.Column("last_seen", sa.DateTime()))
    op.add_column("database_targets", sa.Column("configuration_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("collector_checkpoints", sa.Column("lease_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("collector_checkpoints", sa.Column("lease_expires_at", sa.DateTime()))
    op.add_column("collector_checkpoints", sa.Column("last_success_at", sa.DateTime()))
    op.add_column("collector_checkpoints", sa.Column("last_attempt_at", sa.DateTime()))
    op.add_column("collector_checkpoints", sa.Column("status", sa.String(20), nullable=False, server_default="pending"))
    op.add_column("collector_checkpoints", sa.Column("error_class", sa.String(100)))
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("collector_checkpoints", "cursor", type_=JSONB(), postgresql_using="cursor::jsonb")
    op.create_table(
        "poll_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("probe_type", sa.String(100), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_class", sa.String(100)),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("lease_version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "current_observations",
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("metric_name", sa.String(255), primary_key=True),
        sa.Column("label_key", sa.String(64), primary_key=True),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("probe_type", sa.String(100), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("value", sa.Float()),
        sa.Column("value_str", sa.Text()),
        sa.Column("unit", sa.String(50)),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
    )
    op.create_index("ix_observations_target_metric_time", "observations", ["target_id", "metric_name", "timestamp"])
    op.create_index("ix_observations_target_time", "observations", ["target_id", "timestamp"])
    op.create_index("ix_observations_timestamp", "observations", ["timestamp"])
    op.create_index("ix_poll_runs_target_probe_started", "poll_runs", ["target_id", "probe_type", "started_at"])
    op.create_index("ix_poll_runs_started", "poll_runs", ["started_at"])


def downgrade() -> None:
    op.drop_table("current_observations")
    op.drop_table("poll_runs")
    for name in ("ix_observations_target_metric_time", "ix_observations_target_time", "ix_observations_timestamp"):
        op.drop_index(name, "observations")
    for name in ("error_class", "status", "last_attempt_at", "last_success_at", "lease_expires_at", "lease_version"):
        op.drop_column("collector_checkpoints", name)
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("collector_checkpoints", "cursor", type_=sa.JSON(), postgresql_using="cursor::json")
    op.drop_column("database_targets", "last_seen")
    op.drop_column("database_targets", "configuration_version")
    op.drop_column("database_targets", "health_valid_until")
