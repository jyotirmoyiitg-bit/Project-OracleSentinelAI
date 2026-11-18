"""Agent runs, steps, evidence snapshots, findings, and per-database agent settings."""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agent", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=True),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("playbook", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("principal", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("llm_used", sa.Boolean(), nullable=False),
        sa.Column("steps_used", sa.Integer(), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_agent_runs_created", "agent_runs", ["created_at"])
    op.create_index("ix_agent_runs_target", "agent_runs", ["target_id", "created_at"])
    op.create_index("ix_agent_runs_incident", "agent_runs", ["incident_id"])

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_steps_run", "agent_steps", ["run_id", "seq"])

    op.create_table(
        "evidence_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pack", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("taken_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("restricted", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_evidence_snapshots_target_kind", "evidence_snapshots", ["target_id", "kind", "taken_at"])
    op.create_index("ix_evidence_snapshots_batch", "evidence_snapshots", ["batch_id"])
    op.create_index("ix_evidence_snapshots_taken", "evidence_snapshots", ["taken_at"])

    op.create_table(
        "agent_findings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=True),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("next_steps", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("verifier_note", sa.String(length=300), nullable=False),
        sa.Column("written_by", sa.String(length=20), nullable=False),
        sa.Column("restricted", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("decided_by", sa.String(length=200), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_findings_decision", "agent_findings", ["decision", "created_at"])
    op.create_index("ix_agent_findings_incident", "agent_findings", ["incident_id"])
    op.create_index("ix_agent_findings_target", "agent_findings", ["target_id", "created_at"])

    op.create_table(
        "agent_database_settings",
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("database_targets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("changed_by", sa.String(length=200), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("agent_database_settings")
    op.drop_table("agent_findings")
    op.drop_table("evidence_snapshots")
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
