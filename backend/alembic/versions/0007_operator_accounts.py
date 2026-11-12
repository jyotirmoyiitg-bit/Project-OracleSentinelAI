"""Local operator accounts for sign-in when the directory is unavailable."""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operator_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_operator_accounts_username", "operator_accounts", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_operator_accounts_username", table_name="operator_accounts")
    op.drop_table("operator_accounts")
