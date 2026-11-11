"""Knowledge documents, chunk citations, and pgvector embeddings."""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

EMBEDDING_DIMENSIONS = 128


def upgrade() -> None:
    postgresql = _dialect() == "postgresql"
    if postgresql:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    embedding = Vector(EMBEDDING_DIMENSIONS) if postgresql else sa.JSON()
    search_vector = TSVECTOR() if postgresql else sa.Text()
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("relative_path", sa.String(700), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("previous_hash", sa.String(64)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("ingestion_status", sa.String(20), nullable=False),
        sa.Column("acl", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer()),
        sa.Column("quality_score", sa.Float()),
        sa.Column("quality_report", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("embedding_provider", sa.String(20), nullable=False),
        sa.Column("embedding_model", sa.String(100), nullable=False),
        sa.Column("embedding_disclosure", sa.Text(), nullable=False),
        sa.Column("indexed_at", sa.DateTime()),
        sa.Column("deleted_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("relative_path", name="uq_documents_relative_path"),
    )
    op.create_index("ix_documents_category_status", "documents", ["category", "ingestion_status"])
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(300), nullable=False),
        sa.Column("page_start", sa.Integer()),
        sa.Column("page_end", sa.Integer()),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", embedding),
        sa.Column("content_tsv", search_vector),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("acl", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "document_id", "document_version", "chunk_index", name="uq_document_chunk_version_index",
        ),
    )
    op.create_index("ix_document_chunks_document", "document_chunks", ["document_id", "document_version"])
    if postgresql:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_cosine "
            "ON document_chunks USING hnsw (embedding vector_cosine_ops)",
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_document_chunks_content_tsv ON document_chunks USING gin (content_tsv)",
        )


def downgrade() -> None:
    if _dialect() == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_cosine")
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_content_tsv")
    op.drop_index("ix_document_chunks_document", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_category_status", table_name="documents")
    op.drop_table("documents")


def _dialect() -> str:
    return op.get_context().dialect.name
