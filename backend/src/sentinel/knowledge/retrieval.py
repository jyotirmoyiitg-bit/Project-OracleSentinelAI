"""Hybrid vector and keyword retrieval with caller filtering before any passage is returned."""

import re
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller, allows
from sentinel.knowledge.embeddings import build_embedding_provider
from sentinel.knowledge.text import tokenize
from sentinel.knowledge.types import Citation, EmbeddingError, Passage
from sentinel.persistence.models import Document, DocumentChunk

CANDIDATE_POOL = 50
VECTOR_FLOOR = 0.15
EXCERPT_LIMIT = 700
# An Oracle message code such as ORA-00257, RMAN-06059, or PLS-00201. A vector cannot tell one number from
# another, and the all-words keyword search misses the entry when the question has other words in it.
_CODE = re.compile(r"\b([A-Za-z]{2,6}-\d{3,5})\b")
CODE_WEIGHT = 3.0
CODE_POOL = 10

VECTOR_CANDIDATE_SQL = """
SELECT document_chunks.id AS id
FROM document_chunks
JOIN documents ON documents.id = document_chunks.document_id
    AND documents.version = document_chunks.document_version
WHERE documents.ingestion_status = 'indexed'
  AND documents.embedding_provider = :provider
  AND documents.embedding_model = :model
  AND document_chunks.embedding IS NOT NULL
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

CODE_CANDIDATE_SQL = """
SELECT document_chunks.id AS id
FROM document_chunks
JOIN documents ON documents.id = document_chunks.document_id
    AND documents.version = document_chunks.document_version
WHERE documents.ingestion_status = 'indexed'
  AND document_chunks.content_tsv @@ phraseto_tsquery('english', :code)
  AND (:category = '' OR document_chunks.category = :category)
  AND (
    CAST(document_chunks.acl -> 'roles' AS jsonb) @> jsonb_build_array(CAST(:role AS text))
    OR (
      CAST(:principal AS text) <> ''
      AND CAST(document_chunks.acl -> 'principals' AS jsonb) @> jsonb_build_array(CAST(:principal AS text))
    )
  )
ORDER BY
  CASE WHEN upper(document_chunks.heading) LIKE upper(:code) || '%' THEN 0 ELSE 1 END,
  CASE WHEN position(upper(:code) || ':' IN upper(document_chunks.content)) > 0 THEN 0 ELSE 1 END,
  position(upper(:code) IN upper(document_chunks.content)),
  document_chunks.id
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
    try:
        query_vector = provider.embed_query_sync(cleaned)
    except EmbeddingError:
        # The embedding model is not answering. Keyword search still finds passages.
        query_vector = []
    codes = list(dict.fromkeys(match.upper() for match in _CODE.findall(cleaned)))[:2]
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        vector_ids, keyword_ids, code_ids, pairs = _postgres_candidates(
            session, cleaned, query_vector, caller, category, provider.provider_name, provider.model, codes,
        )
    else:
        pairs = _load_authorized(session, caller, category)
        chunks = [chunk for chunk, _document in pairs]
        current = {
            str(chunk.id) for chunk, document in pairs
            if document.embedding_provider == provider.provider_name and document.embedding_model == provider.model
        }
        vector_ids = [item for item in _rank_vector(chunks, query_vector) if item in current]
        keyword_ids = _rank_keyword(chunks, cleaned)
        code_ids = _rank_code(chunks, codes)
    allowed = {str(chunk.id): (chunk, document) for chunk, document in pairs}
    return _assemble(
        [item for item in vector_ids if item in allowed],
        [item for item in keyword_ids if item in allowed],
        allowed,
        cleaned,
        query_vector,
        limit,
        rerank,
        semantic=provider.provider_name != "local",
        code_ids=[item for item in code_ids if item in allowed],
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
    provider_name: str,
    model_name: str,
    codes: list[str],
) -> tuple[list[str], list[str], list[str], list[tuple[DocumentChunk, Document]]]:
    params = {
        "query": query,
        "role": caller.role,
        "principal": caller.principal,
        "category": category or "",
        "limit": CANDIDATE_POOL,
    }
    vector_ids: list[str] = []
    if query_vector:
        # Only passages embedded with the current model are compared, so a half-finished re-index cannot mix dimensions.
        vector_params = {
            **params,
            "embedding": "[" + ",".join(f"{value:.8f}" for value in query_vector) + "]",
            "provider": provider_name,
            "model": model_name,
        }
        vector_ids = [str(row[0]) for row in session.execute(text(VECTOR_CANDIDATE_SQL), vector_params)]
    keyword_ids = [str(row[0]) for row in session.execute(text(KEYWORD_CANDIDATE_SQL), params)]
    code_ids: list[str] = []
    for code in codes:
        code_params = {**params, "code": code, "limit": CODE_POOL}
        del code_params["query"]
        code_ids.extend(str(row[0]) for row in session.execute(text(CODE_CANDIDATE_SQL), code_params))
    identifiers = list(dict.fromkeys(vector_ids + keyword_ids + code_ids))
    if not identifiers:
        return [], [], [], []
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
    return vector_ids, keyword_ids, code_ids, visible


def _rank_code(chunks: list[DocumentChunk], codes: list[str]) -> list[str]:
    """Passages that contain a message code, the entry that defines it first."""
    ranked: list[str] = []
    for code in codes:
        scored: list[tuple[int, int, int, str]] = []
        for chunk in chunks:
            content = chunk.content.upper()
            position = content.find(code)
            if position < 0:
                continue
            scored.append((
                0 if chunk.heading.upper().startswith(code) else 1,
                0 if f"{code}:" in content else 1,
                position, str(chunk.id),
            ))
        scored.sort()
        ranked.extend(item[3] for item in scored[:CODE_POOL])
    return ranked


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
    *,
    semantic: bool = False,
    code_ids: list[str] | None = None,
) -> list[Passage]:
    fused = _reciprocal_rank({
        "vector": [item for item in vector_ids if item in chunks],
        "keyword": [item for item in keyword_ids if item in chunks],
    })
    for chunk_id, score in _reciprocal_rank({"code": list(dict.fromkeys(code_ids or []))}).items():
        fused[chunk_id] = fused.get(chunk_id, 0.0) + CODE_WEIGHT * score
    tokens = tokenize(query)
    codes = [match.upper() for match in _CODE.findall(query)]
    ranked = sorted(
        fused,
        key=lambda chunk_id: _sort_key(
            chunk_id, fused[chunk_id], chunks, tokens, query, query_vector, rerank, semantic,
        ),
    )
    passages: list[Passage] = []
    for chunk_id in ranked[:limit]:
        chunk, document = chunks[chunk_id]
        bonus = _rerank_bonus(chunk, query, semantic) if rerank else 0.0
        passages.append(Passage(
            chunk_id=str(chunk.id),
            excerpt=_excerpt(chunk.content, codes),
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
    semantic: bool = False,
) -> tuple[float, int, float, str]:
    chunk, _document = chunks[chunk_id]
    bonus = _rerank_bonus(chunk, query, semantic) if rerank else 0.0
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


def _rerank_bonus(chunk: DocumentChunk, query: str, semantic: bool = False) -> float:
    """Extra score for an exact phrase or a heading word.

    With an embedding model the vector rank already carries the meaning. A heading-word bonus then pushes
    index and contents pages above the passage that answers the question, so only a small phrase bonus is kept.
    """
    bonus = 0.0
    phrase = " ".join(query.lower().split())
    if phrase and phrase in chunk.content.lower():
        bonus += 0.02 if semantic else 0.15
    if semantic:
        return bonus
    heading = chunk.heading.lower()
    if any(token in heading for token in tokenize(query)):
        bonus += 0.05
    return bonus


def _excerpt(content: str, codes: list[str] | None = None) -> str:
    """The start of a passage, or the part from a message code the question named."""
    text = content.strip()
    for code in codes or []:
        entry = text.upper().find(f"{code}:")
        position = entry if entry >= 0 else text.upper().find(code)
        if position > 0:
            text = text[position:]
            break
    if len(text) <= EXCERPT_LIMIT:
        return text
    clipped = text[:EXCERPT_LIMIT].rsplit(" ", 1)[0].rstrip()
    return f"{clipped or text[:EXCERPT_LIMIT].rstrip()}…"
