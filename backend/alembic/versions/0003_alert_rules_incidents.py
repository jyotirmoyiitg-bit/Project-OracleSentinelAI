"""Alert-log rules, incidents, incident events, and notifications."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rule_definitions",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true()),
        sa.Column("scope", sa.String(50), server_default="all"),
        sa.Column("expression", sa.Text(), server_default=""),
        sa.Column("hysteresis", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("severity", sa.String(20), server_default="warning"),
        sa.Column("message", sa.Text(), server_default=""),
        sa.Column("source", sa.String(20), server_default="yaml"),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_table(
        "rule_states",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rule_id", sa.String(100), nullable=False),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope_key", sa.String(255), server_default=""),
        sa.Column("consecutive_matches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_fired_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("rule_id", "target_id", "scope_key", name="uq_rule_target_scope"),
    )
    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fingerprint", sa.String(255), nullable=False),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", sa.String(100), nullable=False),
        sa.Column("state", sa.String(20), server_default="new"),
        sa.Column("severity", sa.String(20), server_default="warning"),
        sa.Column("message", sa.Text(), server_default=""),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("owner", sa.String(255)),
        sa.Column("root_cause_candidate", sa.String(255)),
        sa.Column("evidence", sa.JSON(), server_default="{}"),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_incidents_fingerprint", "incidents", ["fingerprint"])
    op.create_index("ix_incidents_target_state", "incidents", ["target_id", "state"])
    op.create_table(
        "incident_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("actor", sa.String(255), server_default="system"),
        sa.Column("reason", sa.Text()),
        sa.Column("before", sa.JSON(), server_default="{}"),
        sa.Column("after", sa.JSON(), server_default="{}"),
    )
    op.create_index("ix_incident_events_incident_time", "incident_events", ["incident_id", "timestamp"])
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(20), server_default="smtp"),
        sa.Column("recipient", sa.String(500), server_default=""),
        sa.Column("subject", sa.String(500), server_default=""),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("dedup_id", sa.String(255), server_default=""),
        sa.Column("error_class", sa.String(100)),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("sent_at", sa.DateTime()),
    )
    op.create_index("ix_notifications_dedup", "notifications", ["dedup_id"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_index("ix_incident_events_incident_time", "incident_events")
    op.drop_table("incident_events")
    op.drop_index("ix_incidents_target_state", "incidents")
    op.drop_index("ix_incidents_fingerprint", "incidents")
    op.drop_table("incidents")
    op.drop_table("rule_states")
    op.drop_table("rule_definitions")
