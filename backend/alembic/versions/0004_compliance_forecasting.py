"""Versioned compliance policies and results."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("policy_id", sa.String(100), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("engine", sa.String(20), server_default="oracle", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("approval_status", sa.String(20), server_default="approved", nullable=False),
        sa.Column("severity", sa.String(20), server_default="warning", nullable=False),
        sa.Column("applicability", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("evaluation_config", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("environment_overrides", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("recommended_action", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("policy_id", "version", name="uq_policy_definition_version"),
    )
    op.create_index("ix_policy_definitions_policy_id", "policy_definitions", ["policy_id"])
    op.create_table(
        "policy_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_id", sa.String(100), nullable=False),
        sa.Column("policy_version", sa.String(50), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("result", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("evidence", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("recommended_action", sa.Text(), server_default="", nullable=False),
        sa.UniqueConstraint("target_id", "policy_id", "policy_version", name="uq_policy_result_target_version"),
    )
    op.create_index("ix_policy_results_target_observed", "policy_results", ["target_id", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_policy_results_target_observed", table_name="policy_results")
    op.drop_table("policy_results")
    op.drop_index("ix_policy_definitions_policy_id", table_name="policy_definitions")
    op.drop_table("policy_definitions")
