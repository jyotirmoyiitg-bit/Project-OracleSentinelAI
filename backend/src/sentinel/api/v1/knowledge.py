"""Knowledge document list, hybrid search, and administrator reindex."""

import uuid
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, sessionmaker

from sentinel.api.v1.schemas import (
    KnowledgeAclRequest,
    KnowledgeCitationResponse,
    KnowledgeDocumentListResponse,
    KnowledgeDocumentResponse,
    KnowledgePassageResponse,
    KnowledgeReindexRequest,
    KnowledgeReindexResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller, caller_from_headers, copy_acl, is_admin_operator, normalize_acl
from sentinel.knowledge.constants import CATEGORIES, PRIORITIZATION
from sentinel.knowledge.retrieval import search
from sentinel.knowledge.service import KnowledgeIndexer
from sentinel.knowledge.store import list_documents, update_acl
from sentinel.knowledge.types import ScanInProgress
from sentinel.persistence.models import Document
from sentinel.persistence.session import get_session

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

SessionDep = Annotated[Session, Depends(get_session)]


def _settings(request: Request) -> Settings:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise HTTPException(status_code=503, detail="Application settings are not initialized")
    return settings


def _factory(request: Request) -> sessionmaker[Session]:
    factory = getattr(request.app.state, "session_factory", None)
    if not callable(getattr(factory, "begin", None)):
        raise HTTPException(status_code=503, detail="Database is not initialized")
    return cast(sessionmaker[Session], factory)


def _caller(request: Request) -> Caller:
    existing = getattr(request.state, "caller", None)
    if isinstance(existing, Caller):
        return existing
    try:
        return caller_from_headers(
            request.headers.get("X-Sentinel-Role"),
            request.headers.get("X-Sentinel-Principal"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _admin(request: Request) -> Caller:
    caller = _caller(request)
    if not is_admin_operator(caller.role):
        raise HTTPException(status_code=403, detail="Administrator role is required")
    return caller


def _document(session: Session, document_id: str) -> Document:
    try:
        identifier = uuid.UUID(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from exc
    document = session.get(Document, identifier)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _document_response(document: Document) -> KnowledgeDocumentResponse:
    return KnowledgeDocumentResponse(
        id=document.id,
        file_name=document.file_name,
        relative_path=document.relative_path,
        category=_category(document.category),
        version=document.version,
        ingestion_status=_status(document.ingestion_status),
        size_bytes=document.size_bytes,
        page_count=document.page_count,
        quality_score=document.quality_score,
        embedding_provider=document.embedding_provider,
        embedding_model=document.embedding_model,
        embedding_disclosure=document.embedding_disclosure,
        indexed_at=document.indexed_at,
        updated_at=document.updated_at,
        acl_roles=copy_acl(document.acl)["roles"],
    )


def _category(value: str) -> Literal["Knowledge", "Policy"]:
    if value == "Knowledge":
        return "Knowledge"
    if value == "Policy":
        return "Policy"
    raise HTTPException(status_code=500, detail="Stored document category is invalid")


def _status(value: str) -> Literal["indexed", "deleted", "quarantined"]:
    if value == "indexed":
        return "indexed"
    if value == "deleted":
        return "deleted"
    if value == "quarantined":
        return "quarantined"
    raise HTTPException(status_code=500, detail="Stored document status is invalid")


@router.get("/documents", response_model=KnowledgeDocumentListResponse)
def get_documents(
    session: SessionDep,
    caller: Annotated[Caller, Depends(_caller)],
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    category: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None, max_length=255),
) -> KnowledgeDocumentListResponse:
    if category is not None and category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Category must be Knowledge or Policy")
    if status is not None and status not in {"indexed", "deleted", "quarantined"}:
        raise HTTPException(status_code=400, detail="Unknown ingestion status")
    items, total = list_documents(
        session, caller, category=category, status=status, query=q, page=page, page_size=page_size,
    )
    return KnowledgeDocumentListResponse(
        items=[_document_response(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/reindex", response_model=KnowledgeReindexResponse)
def reindex_documents(
    body: KnowledgeReindexRequest,
    request: Request,
    _admin_caller: Annotated[Caller, Depends(_admin)],
    settings: Annotated[Settings, Depends(_settings)],
) -> KnowledgeReindexResponse:
    indexer = KnowledgeIndexer(_factory(request), settings)
    try:
        report = indexer.scan(body.paths)
    except ScanInProgress as exc:
        raise HTTPException(status_code=409, detail="A knowledge scan is already running") from exc
    return KnowledgeReindexResponse(
        scanned=report.scanned,
        indexed=report.indexed,
        updated=report.updated,
        unchanged=report.unchanged,
        deleted=report.deleted,
        quarantined=report.quarantined,
        skipped=report.skipped,
        errors=report.errors,
    )


@router.post("/search", response_model=KnowledgeSearchResponse)
def search_documents(
    body: KnowledgeSearchRequest,
    session: SessionDep,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> KnowledgeSearchResponse:
    passages = search(
        session,
        settings,
        body.query,
        caller,
        limit=body.limit,
        category=body.category,
        rerank=body.rerank,
    )
    return KnowledgeSearchResponse(
        query=body.query,
        prioritization=PRIORITIZATION,
        items=[
            KnowledgePassageResponse(
                chunk_id=uuid.UUID(item.chunk_id),
                excerpt=item.excerpt,
                score=item.score,
                authoritative_policy=item.authoritative_policy,
                citation=KnowledgeCitationResponse(
                    document_id=uuid.UUID(item.citation.document_id),
                    document_name=item.citation.document_name,
                    relative_path=item.citation.relative_path,
                    category=item.citation.category,
                    version=item.citation.version,
                    page_start=item.citation.page_start,
                    page_end=item.citation.page_end,
                    heading=item.citation.heading,
                    chunk_index=item.citation.chunk_index,
                ),
            )
            for item in passages
        ],
    )


@router.post("/documents/{document_id}/acl", response_model=KnowledgeDocumentResponse)
def replace_document_acl(
    document_id: str,
    body: KnowledgeAclRequest,
    session: SessionDep,
    _: Annotated[Caller, Depends(_admin)],
) -> KnowledgeDocumentResponse:
    document = _document(session, document_id)
    try:
        granted = normalize_acl(body.roles, body.principals)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    update_acl(session, document, granted)
    session.commit()
    return _document_response(document)
