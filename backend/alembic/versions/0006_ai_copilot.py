"""Copilot threads, messages, and tool-execution audit."""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("principal", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_id", sa.String(36)),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_chat_threads_scope", "chat_threads", ["scope_type", "scope_id"])
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), sa.ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("tool_trace", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("llm_used", sa.Boolean(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_chat_messages_thread_created", "chat_messages", ["thread_id", "created_at"])
    op.create_table(
        "tool_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), sa.ForeignKey("chat_threads.id", ondelete="CASCADE")),
        sa.Column("message_id", sa.Uuid()),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("principal", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(36)),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("result_digest", sa.String(64), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tool_executions_thread", "tool_executions", ["thread_id", "created_at"])
    op.create_index("ix_tool_executions_correlation", "tool_executions", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_executions_correlation", table_name="tool_executions")
    op.drop_index("ix_tool_executions_thread", table_name="tool_executions")
    op.drop_table("tool_executions")
    op.drop_index("ix_chat_messages_thread_created", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_threads_scope", table_name="chat_threads")
    op.drop_table("chat_threads")
