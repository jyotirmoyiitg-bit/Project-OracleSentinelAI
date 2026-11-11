"""Parser, chunk, and embedding behavior for knowledge ingestion."""

import time
from pathlib import Path

import httpx
import pymupdf
import pytest
from docx import Document
from pydantic import ValidationError

from sentinel.config.settings import Settings
from sentinel.knowledge.chunker import build_chunks, split_text
from sentinel.knowledge.embeddings import (
    HashingEmbeddingProvider,
    RemoteEmbeddingProvider,
    build_embedding_provider,
)
from sentinel.knowledge.parsers import parse_file, parse_with_timeout
from sentinel.knowledge.types import DocumentRejected, EmbeddingError, ParsedDocument, Section


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"_env_file": None, "synthetic_mode": True}
    values.update(overrides)
    return Settings(**values)


def write_pdf(path: Path, text: str, *, encrypt: bool = False) -> None:
    document = pymupdf.open()
    page = document.new_page()
    if text:
        page.insert_text((72, 72), text)
    if encrypt:
        document.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    else:
        document.save(path)
    document.close()


def test_pdf_docx_and_markdown_keep_section_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "archive.pdf"
    write_pdf(pdf_path, "ARCHIVELOG\nProduction databases must remain in ARCHIVELOG mode for recovery.")
    docx_path = tmp_path / "recovery.docx"
    document = Document()
    document.add_heading("Fast recovery area", level=1)
    document.add_paragraph("Free space is required before the archive destination fills.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Parameter"
    table.rows[0].cells[1].text = "DB_RECOVERY_FILE_DEST"
    document.save(docx_path)
    markdown_path = tmp_path / "ora.md"
    markdown_path.write_text("# ORA-00257\n\nThe fast recovery area is full.\n", encoding="utf-8")
    config = settings()

    pdf = parse_file(pdf_path, config)
    docx = parse_file(docx_path, config)
    markdown = parse_file(markdown_path, config)

    assert pdf.page_count == 1
    assert "ARCHIVELOG" in pdf.sections[0].text or pdf.sections[0].heading == "ARCHIVELOG"
    assert docx.sections[0].heading == "Fast recovery area"
    assert "DB_RECOVERY_FILE_DEST" in docx.sections[0].text
    assert markdown.sections[0].heading == "ORA-00257"
    assert "fast recovery area" in markdown.sections[0].text


def test_encrypted_scanned_and_corrupt_documents_are_rejected(tmp_path: Path) -> None:
    config = settings()
    encrypted = tmp_path / "secret.pdf"
    write_pdf(encrypted, "This encrypted page must not be indexed.", encrypt=True)
    scanned = tmp_path / "scan.pdf"
    write_pdf(scanned, "")
    corrupt = tmp_path / "broken.pdf"
    corrupt.write_bytes(b"this is not a pdf")

    with pytest.raises(DocumentRejected) as encrypted_error:
        parse_file(encrypted, config)
    with pytest.raises(DocumentRejected) as scanned_error:
        parse_file(scanned, config)
    with pytest.raises(DocumentRejected) as corrupt_error:
        parse_file(corrupt, config)

    assert encrypted_error.value.reason == "encrypted"
    assert scanned_error.value.reason == "scanned_pdf"
    assert corrupt_error.value.reason == "corrupt"


def test_parser_timeout_cancels_the_wait(tmp_path: Path) -> None:
    path = tmp_path / "slow.md"
    path.write_text("ARCHIVELOG standard text that would otherwise index.", encoding="utf-8")

    def slow(path: Path, settings: Settings) -> ParsedDocument:
        time.sleep(0.4)
        return ParsedDocument([Section("", "unused", None, None)], None, 1, [])

    with pytest.raises(DocumentRejected) as error:
        parse_with_timeout(path, settings(knowledge_parser_timeout_seconds=0.05), slow)
    assert error.value.reason == "parser_timeout"


def test_chunks_overlap_and_keep_the_heading() -> None:
    text = " ".join(f"token{index:03d}" for index in range(80))
    parts = split_text(text, 40, 10)
    assert len(parts) > 1
    overlap = parts[0][-10:].strip()
    assert overlap
    assert overlap.split()[-1] in parts[1]
    parsed = ParsedDocument([Section("ARCHIVELOG", text, 2, 2)], 1, 1, [])
    chunks = build_chunks(parsed, settings(knowledge_chunk_size=50, knowledge_chunk_overlap=10))
    assert chunks[0].heading == "ARCHIVELOG"
    assert chunks[0].page_start == 2
    assert "ARCHIVELOG" in chunks[0].text


def test_local_embeddings_are_deterministic_and_closer_for_related_text() -> None:
    provider = HashingEmbeddingProvider(128, "local-hashing-v1")
    archivelog = provider.embed_query_sync("archivelog fast recovery area")
    related = provider.embed_query_sync("archivelog fast recovery area is full")
    unrelated = provider.embed_query_sync("unrelated zebra quantum pastry")
    assert provider.embed_query_sync("archivelog fast recovery area") == archivelog
    assert len(archivelog) == 128
    assert _dot(archivelog, related) > _dot(archivelog, unrelated)


def test_remote_embeddings_require_approval_and_do_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        settings(embedding_provider="remote")
    config = settings(
        embedding_provider="remote",
        embedding_remote_approved=True,
        embedding_remote_url="http://embeddings.internal/v1",
        embedding_remote_privacy_disclosure="Approved for this lab corpus only.",
    )
    provider = build_embedding_provider(config)
    assert isinstance(provider, RemoteEmbeddingProvider)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("httpx.Client.post", fail)
    with pytest.raises(EmbeddingError):
        provider.embed_query_sync("archivelog")


def _dot(left: list[float], right: list[float]) -> float:
    return sum(one * other for one, other in zip(left, right, strict=True))
