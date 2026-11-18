"""Passage vectors grow from 128 to 768 numbers, so a local embedding model can replace word hashing.

Stored vectors are cleared and every document is marked for embedding again. The passages themselves
are kept, so the files are not parsed again. Keyword search keeps working in the meantime.
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _resize(new: int) -> None:
    postgresql = op.get_bind().dialect.name == "postgresql"
    if postgresql:
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_cosine")
        op.execute(f"ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector({new}) USING NULL::vector({new})")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_cosine "
            "ON document_chunks USING hnsw (embedding vector_cosine_ops)",
        )
    else:
        op.execute("UPDATE document_chunks SET embedding = NULL")
    # An empty model name never matches a provider, so the next scan embeds the stored passages again.
    op.execute("UPDATE documents SET embedding_model = ''")
    # The stored setting must match the column, or the settings check stops the API at start.
    op.execute(f"UPDATE app_config SET value = '{int(new)}' WHERE key = 'embedding_dimensions'")


def upgrade() -> None:
    _resize(768)


def downgrade() -> None:
    _resize(128)
