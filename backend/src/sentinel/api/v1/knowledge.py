"""Knowledge document list, hybrid search, administrator upload, and administrator reindex."""

import asyncio
import os
import re
import uuid
from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
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
from sentinel.knowledge.constants import ALLOWED_EXTENSIONS, CATEGORIES, INTAKE_FOLDERS, PRIORITIZATION
from sentinel.knowledge.paths import ensure_layout, intake_root, is_within
from sentinel.knowledge.retrieval import search
from sentinel.knowledge.service import KnowledgeIndexer
from sentinel.knowledge.store import list_documents, update_acl
from sentinel.knowledge.types import ScanInProgress
from sentinel.observability.audit import record_action
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
    with _factory(request).begin() as audit_session:
        record_action(
            audit_session, _admin_caller.principal, "knowledge.reindex", "knowledge-intake", "scanned",
            {"scanned": report.scanned, "indexed": report.indexed, "updated": report.updated,
             "quarantined": report.quarantined, "deleted": report.deleted},
        )
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
    admin_caller: Annotated[Caller, Depends(_admin)],
) -> KnowledgeDocumentResponse:
    document = _document(session, document_id)
    try:
        granted = normalize_acl(body.roles, body.principals)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    update_acl(session, document, granted)
    record_action(
        session, admin_caller.principal, "knowledge.acl", str(document.id), "updated",
        {"roles": granted["roles"], "principals": len(granted["principals"])},
    )
    session.commit()
    return _document_response(document)


class KnowledgeUploadResponse(BaseModel):
    relative_path: str
    category: Literal["Knowledge", "Policy"]
    size_bytes: int
    replaced: bool
    scanned: int
    indexed: int
    updated: int
    unchanged: int
    quarantined: int
    errors: list[str]
    note: str


_UPLOAD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,180}$")
_UPLOAD_CHUNK_LIMIT = 64 * 1024


def _upload_name(filename: str) -> str:
    """One plain file name with an allowed extension. No folder, drive, or traversal."""
    name = filename.strip()
    if name != Path(name).name or "/" in name or "\\" in name or ":" in name or name in {".", ".."}:
        raise HTTPException(status_code=422, detail="Give a file name without a folder.")
    if _UPLOAD_NAME.fullmatch(name) is None:
        raise HTTPException(status_code=422, detail="Use letters, digits, spaces, dots, dashes, underscores, or brackets in the file name.")
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail="Only PDF, DOCX, TXT, and Markdown files are accepted.")
    return name


def _looks_like(name: str, content: bytes) -> bool:
    """The first bytes must agree with the extension. The intake scan still parses and can quarantine."""
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        return content.startswith(b"%PDF-")
    if suffix == ".docx":
        return content.startswith(b"PK")
    if b"\x00" in content[:8192]:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


@router.post("/upload", response_model=KnowledgeUploadResponse, status_code=201)
async def upload_document(
    request: Request,
    admin_caller: Annotated[Caller, Depends(_admin)],
    settings: Annotated[Settings, Depends(_settings)],
    filename: str = Query(min_length=1, max_length=200),
    category: str = Query("Knowledge"),
    replace: bool = Query(False),
) -> KnowledgeUploadResponse:
    """Save one document into its intake folder and index it. The body is the raw file."""
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Category must be Knowledge or Policy")
    name = _upload_name(filename)
    limit = settings.knowledge_max_file_bytes
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail=f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status_code=422, detail="The file is empty.")
    if not _looks_like(name, content):
        raise HTTPException(status_code=422, detail="The file content does not match its extension.")
    folder = next(key for key, value in INTAKE_FOLDERS.items() if value == category)
    intake = intake_root(settings)
    ensure_layout(intake)
    destination = intake / folder / name
    if not is_within(intake / folder, destination):
        raise HTTPException(status_code=422, detail="Give a file name without a folder.")
    replaced = destination.exists()
    if replaced and not replace:
        raise HTTPException(status_code=409, detail="A document with this name already exists. Choose replace to overwrite it.")
    temporary = destination.with_name(f".{uuid.uuid4().hex}.upload")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="The document could not be saved.") from exc
    relative = f"{folder}/{name}"
    category_name = _category(category)
    note = "Saved and indexed."
    scanned = indexed = updated = unchanged = quarantined = 0
    errors: list[str] = []
    try:
        report = await asyncio.to_thread(KnowledgeIndexer(_factory(request), settings).scan, [relative])
        scanned, indexed, updated = report.scanned, report.indexed, report.updated
        unchanged, quarantined, errors = report.unchanged, report.quarantined, list(report.errors)
        if quarantined or (errors and not (indexed or updated or unchanged)):
            note = "Saved, but the intake scan did not index it. See the errors and the Quarantine folder."
    except ScanInProgress:
        note = "Saved. A scan is already running, so reindex when it finishes."
    with _factory(request).begin() as audit_session:
        record_action(
            audit_session, admin_caller.principal, "knowledge.upload", name[:80],
            "replaced" if replaced else "created",
            {"relative_path": relative, "size_bytes": size, "indexed": indexed, "updated": updated,
             "quarantined": quarantined},
        )
    return KnowledgeUploadResponse(
        relative_path=relative, category=category_name, size_bytes=size, replaced=replaced,
        scanned=scanned, indexed=indexed, updated=updated, unchanged=unchanged,
        quarantined=quarantined, errors=errors[:10], note=note,
    )
