"""Persist document versions, chunks, and ACL changes."""

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from sentinel.collectors.normalization import utcnow
from sentinel.knowledge.acl import Caller, allows, copy_acl, default_acl
from sentinel.knowledge.constants import EMBEDDING_DIMENSIONS
from sentinel.knowledge.text import tokenize
from sentinel.knowledge.types import ChunkDraft, DiscoveredFile, EmbeddingError, ParsedDocument
from sentinel.persistence.models import Document, DocumentChunk


def list_documents(
    session: Session,
    caller: Caller,
    *,
    category: str | None,
    status: str | None,
    query: str | None,
    page: int,
    page_size: int,
) -> tuple[list[Document], int]:
    statement = select(Document).order_by(Document.updated_at.desc(), Document.file_name)
    if category:
        statement = statement.where(Document.category == category)
    if status:
        statement = statement.where(Document.ingestion_status == status)
    if query:
        statement = statement.where(Document.file_name.ilike(_like(query), escape="\\"))
    visible = [row for row in session.scalars(statement) if allows(row.acl, caller)]
    start = (page - 1) * page_size
    return visible[start:start + page_size], len(visible)


def persist_document(
    session: Session,
    item: DiscoveredFile,
    parsed: ParsedDocument,
    drafts: list[ChunkDraft],
    vectors: list[list[float]],
    *,
    provider_name: str,
    model_name: str,
    disclosure: str,
) -> str:
    if len(drafts) != len(vectors) or any(len(vector) != EMBEDDING_DIMENSIONS for vector in vectors):
        raise EmbeddingError("embedding count or dimensions did not match the chunks")
    existing = session.scalar(select(Document).where(Document.relative_path == item.relative))
    if (
        existing is not None
        and existing.content_hash == item.content_hash
        and existing.ingestion_status == "indexed"
    ):
        return "unchanged"
    now = utcnow()
    if existing is None:
        document = Document(
            relative_path=item.relative,
            file_name=_file_name(item.relative),
            category=item.category,
            content_hash=item.content_hash,
            previous_hash=None,
            version=1,
            ingestion_status="indexed",
            acl=default_acl(),
            created_at=now,
        )
        outcome = "indexed"
    else:
        document = existing
        document.previous_hash = existing.content_hash
        document.content_hash = item.content_hash
        document.version = existing.version + 1
        document.ingestion_status = "indexed"
        outcome = "updated"
    document.file_name = _file_name(item.relative)
    document.category = item.category
    document.size_bytes = item.size_bytes
    document.page_count = parsed.page_count
    document.quality_score = parsed.quality_score
    document.quality_report = {"notes": parsed.notes}
    document.embedding_provider = provider_name
    document.embedding_model = model_name
    document.embedding_disclosure = disclosure
    document.indexed_at = now
    document.deleted_at = None
    document.updated_at = now
    session.add(document)
    session.flush()
    acl = copy_acl(document.acl)
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    for index, (draft, vector) in enumerate(zip(drafts, vectors, strict=True)):
        chunk = DocumentChunk(
            document_id=document.id,
            document_version=document.version,
            chunk_index=index,
            heading=draft.heading,
            page_start=draft.page_start,
            page_end=draft.page_end,
            content=draft.text,
            embedding=vector,
            category=document.category,
            acl=copy_acl(acl),
            created_at=now,
        )
        _assign_search_text(session, chunk)
        session.add(chunk)
    assign_postgres_vectors(session)
    return outcome


def update_acl(session: Session, document: Document, acl: dict[str, list[str]]) -> Document:
    granted = copy_acl(acl)
    document.acl = granted
    document.updated_at = utcnow()
    for chunk in session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document.id)):
        chunk.acl = copy_acl(granted)
    return document


def mark_quarantined(session: Session, relative: str) -> None:
    document = session.scalar(select(Document).where(Document.relative_path == relative))
    if document is None or document.ingestion_status == "quarantined":
        return
    document.ingestion_status = "quarantined"
    document.updated_at = utcnow()
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))


def mark_missing(session: Session, seen: set[str]) -> int:
    removed = 0
    rows = session.scalars(select(Document).where(Document.ingestion_status == "indexed")).all()
    for document in rows:
        if document.relative_path in seen:
            continue
        document.ingestion_status = "deleted"
        document.deleted_at = utcnow()
        document.updated_at = utcnow()
        session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        removed += 1
    return removed


def _assign_search_text(session: Session, chunk: DocumentChunk) -> None:
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        return
    chunk.content_tsv = " ".join(tokenize(f"{chunk.heading} {chunk.content}"))


def assign_postgres_vectors(session: Session) -> None:
    """Fill tsvector values after flush. SQLite keeps the token string written with the row."""
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    session.flush()
    for chunk in session.scalars(select(DocumentChunk).where(DocumentChunk.content_tsv.is_(None))):
        session.execute(
            text("UPDATE document_chunks SET content_tsv = to_tsvector('english', :content) WHERE id = :id"),
            {"content": chunk.content, "id": chunk.id},
        )


def _file_name(relative: str) -> str:
    return relative.rsplit("/", 1)[-1][:255]


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped.lower()}%"
