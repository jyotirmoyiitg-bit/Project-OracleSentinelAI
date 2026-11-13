"""Operation proposals, entitlements, and an append-only audit table."""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "database_targets",
        sa.Column("maintenance_restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "database_entitlements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("principal", sa.String(200), nullable=False),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE")),
        sa.Column("environment", sa.String(50), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_database_entitlements_principal", "database_entitlements", ["principal"])
    op.create_index("ix_database_entitlements_target", "database_entitlements", ["target_id"])
    op.create_table(
        "operation_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("requester", sa.String(200), nullable=False),
        sa.Column("requester_role", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("preview_sql", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("risk", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("approver", sa.String(200), nullable=False),
        sa.Column("decided_at", sa.DateTime()),
        sa.Column("decision_reason", sa.String(500), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(80)),
        sa.Column("execution_log", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_operation_proposals_target_status", "operation_proposals", ["target_id", "status"])
    op.create_index(
        "ix_operation_proposals_requester_key",
        "operation_proposals",
        ["requester", "idempotency_key"],
        unique=True,
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("principal", sa.String(200), nullable=False),
        sa.Column("service", sa.String(40), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target", sa.String(80), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
    )
    op.create_index("ix_audit_events_occurred", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_events_correlation", "audit_events", ["correlation_id"])
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION audit_events_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'audit events are immutable';
            END;
            $$ LANGUAGE plpgsql
            """,
        )
        op.execute(
            """
            CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
            FOR EACH ROW EXECUTE PROCEDURE audit_events_immutable()
            """,
        )
        op.execute(
            """
            CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
            FOR EACH ROW EXECUTE PROCEDURE audit_events_immutable()
            """,
        )
    else:
        op.execute(
            "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
            "BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END",
        )
        op.execute(
            "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events "
            "BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events")
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete ON audit_events")
        op.execute("DROP FUNCTION IF EXISTS audit_events_immutable()")
    else:
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete")
    op.drop_index("ix_audit_events_correlation", table_name="audit_events")
    op.drop_index("ix_audit_events_occurred", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_operation_proposals_requester_key", table_name="operation_proposals")
    op.drop_index("ix_operation_proposals_target_status", table_name="operation_proposals")
    op.drop_table("operation_proposals")
    op.drop_index("ix_database_entitlements_target", table_name="database_entitlements")
    op.drop_index("ix_database_entitlements_principal", table_name="database_entitlements")
    op.drop_table("database_entitlements")
    op.drop_column("database_targets", "maintenance_restricted")
