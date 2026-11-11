"""Hybrid vector and keyword retrieval with caller filtering before any passage is returned."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller, allows
from sentinel.knowledge.embeddings import build_embedding_provider
from sentinel.knowledge.text import tokenize
from sentinel.knowledge.types import Citation, Passage
from sentinel.persistence.models import Document, DocumentChunk

CANDIDATE_POOL = 50
VECTOR_FLOOR = 0.15
EXCERPT_LIMIT = 700

VECTOR_CANDIDATE_SQL = """
SELECT document_chunks.id AS id
FROM document_chunks
JOIN documents ON documents.id = document_chunks.document_id
    AND documents.version = document_chunks.document_version
WHERE documents.ingestion_status = 'indexed'
  AND (:category = '' OR document_chunks.category = :category)
  AND (
    CAST(document_chunks.acl -> 'roles' AS jsonb) @> jsonb_build_array(CAST(:role AS text))
    OR (
      CAST(:principal AS text) <> ''
      AND CAST(document_chunks.acl -> 'principals' AS jsonb) @> jsonb_build_array(CAST(:principal AS text))
    )
  )
ORDER BY document_chunks.embedding <=> CAST(:embedding AS vector)
LIMIT :limit
"""

KEYWORD_CANDIDATE_SQL = """
SELECT document_chunks.id AS id
FROM document_chunks
JOIN documents ON documents.id = document_chunks.document_id
    AND documents.version = document_chunks.document_version
WHERE documents.ingestion_status = 'indexed'
  AND document_chunks.content_tsv @@ plainto_tsquery('english', :query)
  AND (:category = '' OR document_chunks.category = :category)
  AND (
    CAST(document_chunks.acl -> 'roles' AS jsonb) @> jsonb_build_array(CAST(:role AS text))
    OR (
      CAST(:principal AS text) <> ''
      AND CAST(document_chunks.acl -> 'principals' AS jsonb) @> jsonb_build_array(CAST(:principal AS text))
    )
  )
ORDER BY ts_rank(document_chunks.content_tsv, plainto_tsquery('english', :query)) DESC
LIMIT :limit
"""


def search(
    session: Session,
    settings: Settings,
    query: str,
    caller: Caller,
    *,
    limit: int = 8,
    category: str | None = None,
    rerank: bool = True,
) -> list[Passage]:
    """Retrieve cited passages. This path does not call a chat model."""
    cleaned = " ".join(query.split())
    if not cleaned:
        return []
    provider = build_embedding_provider(settings)
    query_vector = provider.embed_query_sync(cleaned)
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        vector_ids, keyword_ids, pairs = _postgres_candidates(
            session, cleaned, query_vector, caller, category,
        )
    else:
        pairs = _load_authorized(session, caller, category)
        chunks = [chunk for chunk, _document in pairs]
        vector_ids = _rank_vector(chunks, query_vector)
        keyword_ids = _rank_keyword(chunks, cleaned)
    allowed = {str(chunk.id): (chunk, document) for chunk, document in pairs}
    return _assemble(
        [item for item in vector_ids if item in allowed],
        [item for item in keyword_ids if item in allowed],
        allowed,
        cleaned,
        query_vector,
        limit,
        rerank,
    )


def _load_authorized(
    session: Session, caller: Caller, category: str | None,
) -> list[tuple[DocumentChunk, Document]]:
    statement = (
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            Document.ingestion_status == "indexed",
            DocumentChunk.document_version == Document.version,
        )
        .order_by(DocumentChunk.id)
    )
    if category:
        statement = statement.where(DocumentChunk.category == category)
    return [
        (chunk, document)
        for chunk, document in session.execute(statement).all()
        if allows(chunk.acl, caller) and allows(document.acl, caller)
    ]


def _postgres_candidates(
    session: Session,
    query: str,
    query_vector: list[float],
    caller: Caller,
    category: str | None,
) -> tuple[list[str], list[str], list[tuple[DocumentChunk, Document]]]:
    params = {
        "query": query,
        "embedding": "[" + ",".join(f"{value:.8f}" for value in query_vector) + "]",
        "role": caller.role,
        "principal": caller.principal,
        "category": category or "",
        "limit": CANDIDATE_POOL,
    }
    vector_ids = [str(row[0]) for row in session.execute(text(VECTOR_CANDIDATE_SQL), params)]
    keyword_ids = [str(row[0]) for row in session.execute(text(KEYWORD_CANDIDATE_SQL), params)]
    identifiers = list(dict.fromkeys(vector_ids + keyword_ids))
    if not identifiers:
        return [], [], []
    rows = session.execute(
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.id.in_([UUID(item) for item in identifiers])),
    ).all()
    visible = [
        (chunk, document)
        for chunk, document in rows
        if allows(chunk.acl, caller) and allows(document.acl, caller)
    ]
    return vector_ids, keyword_ids, visible


def _rank_keyword(chunks: list[DocumentChunk], query: str) -> list[str]:
    tokens = tokenize(query)
    scored: list[tuple[int, str]] = []
    for chunk in chunks:
        overlap = _overlap(chunk, tokens)
        if overlap:
            scored.append((overlap, str(chunk.id)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[1] for item in scored[:CANDIDATE_POOL]]


def _rank_vector(chunks: list[DocumentChunk], query_vector: list[float]) -> list[str]:
    scored: list[tuple[float, str]] = []
    for chunk in chunks:
        similarity = _similarity(chunk.embedding, query_vector)
        if similarity >= VECTOR_FLOOR:
            scored.append((similarity, str(chunk.id)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[1] for item in scored[:CANDIDATE_POOL]]


def _assemble(
    vector_ids: list[str],
    keyword_ids: list[str],
    chunks: dict[str, tuple[DocumentChunk, Document]],
    query: str,
    query_vector: list[float],
    limit: int,
    rerank: bool,
) -> list[Passage]:
    fused = _reciprocal_rank({
        "vector": [item for item in vector_ids if item in chunks],
        "keyword": [item for item in keyword_ids if item in chunks],
    })
    tokens = tokenize(query)
    ranked = sorted(
        fused,
        key=lambda chunk_id: _sort_key(chunk_id, fused[chunk_id], chunks, tokens, query, query_vector, rerank),
    )
    passages: list[Passage] = []
    for chunk_id in ranked[:limit]:
        chunk, document = chunks[chunk_id]
        bonus = _rerank_bonus(chunk, query) if rerank else 0.0
        passages.append(Passage(
            chunk_id=str(chunk.id),
            excerpt=_excerpt(chunk.content),
            score=round(fused[chunk_id] + bonus, 6),
            authoritative_policy=document.category == "Policy",
            citation=Citation(
                document_id=str(document.id),
                document_name=document.file_name,
                relative_path=document.relative_path,
                category=document.category,
                version=document.version,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                heading=chunk.heading,
                chunk_index=chunk.chunk_index,
            ),
        ))
    return passages


def _sort_key(
    chunk_id: str,
    fused_score: float,
    chunks: dict[str, tuple[DocumentChunk, Document]],
    tokens: list[str],
    query: str,
    query_vector: list[float],
    rerank: bool,
) -> tuple[float, int, float, str]:
    chunk, _document = chunks[chunk_id]
    bonus = _rerank_bonus(chunk, query) if rerank else 0.0
    return (
        -(fused_score + bonus),
        -_overlap(chunk, tokens),
        -_similarity(chunk.embedding, query_vector),
        chunk_id,
    )


def _reciprocal_rank(rankings: dict[str, list[str]], *, constant: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings.values():
        for index, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (constant + index)
    return scores


def _overlap(chunk: DocumentChunk, tokens: list[str]) -> int:
    haystack = set(tokenize(f"{chunk.heading} {chunk.content}"))
    return sum(1 for token in tokens if token in haystack)


def _similarity(stored: object, query_vector: list[float]) -> float:
    vector = _as_vector(stored)
    if len(vector) != len(query_vector) or not vector:
        return 0.0
    return sum(left * right for left, right in zip(vector, query_vector, strict=True))


def _as_vector(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    vector: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return []
        vector.append(float(item))
    return vector


def _rerank_bonus(chunk: DocumentChunk, query: str) -> float:
    bonus = 0.0
    phrase = " ".join(query.lower().split())
    if phrase and phrase in chunk.content.lower():
        bonus += 0.15
    heading = chunk.heading.lower()
    if any(token in heading for token in tokenize(query)):
        bonus += 0.05
    return bonus


def _excerpt(content: str) -> str:
    text = content.strip()
    if len(text) <= EXCERPT_LIMIT:
        return text
    clipped = text[:EXCERPT_LIMIT].rsplit(" ", 1)[0].rstrip()
    return f"{clipped or text[:EXCERPT_LIMIT].rstrip()}…"
