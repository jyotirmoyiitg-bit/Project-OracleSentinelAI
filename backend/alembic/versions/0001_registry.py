"""Baseline schema matching the pre-Phase-2 registry."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "database_targets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("engine", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("tns_alias", sa.String(255)),
        sa.Column("environment", sa.String(50)),
        sa.Column("owner", sa.String(255)),
        sa.Column("enabled", sa.Boolean()),
        sa.Column("poll_profile", sa.JSON()),
        sa.Column("capability_snapshot", sa.JSON()),
        sa.Column("last_connection_at", sa.DateTime()),
        sa.Column("last_health_status", sa.String(50)),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_table(
        "target_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("secret_reference", sa.String(500), nullable=False),
        sa.Column("username", sa.String(255)),
        sa.Column("rotated_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_table(
        "observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_name", sa.String(255), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("value", sa.Float()),
        sa.Column("value_str", sa.Text()),
        sa.Column("unit", sa.String(50)),
        sa.Column("labels", sa.JSON()),
        sa.Column("quality", sa.String(20)),
        sa.Column("source", sa.String(255)),
    )
    op.create_table(
        "diagnostic_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(255)),
        sa.Column("source_event_key", sa.String(500), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("severity", sa.String(20)),
        sa.Column("code", sa.String(50)),
        sa.Column("message", sa.Text()),
        sa.Column("raw_payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("target_id", "source_event_key", name="uq_target_source_event"),
    )
    op.create_table(
        "collector_checkpoints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("collector_type", sa.String(100), nullable=False),
        sa.Column("cursor", sa.JSON()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("target_id", "collector_type", name="uq_target_collector"),
    )


def downgrade() -> None:
    for table in ("collector_checkpoints", "diagnostic_events", "observations", "target_credentials", "database_targets"):
        op.drop_table(table)
