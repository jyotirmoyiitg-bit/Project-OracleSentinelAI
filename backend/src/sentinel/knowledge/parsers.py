"""PDF, DOCX, and text parsers. Extracted text is data and is never executed."""

import re
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from typing import Any

import pymupdf
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from sentinel.config.settings import Settings
from sentinel.knowledge.types import DocumentRejected, ParsedDocument, Section

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_CHARS_PER_PAGE = 3000


def parse_with_timeout(
    path: Path,
    settings: Settings,
    parser: Callable[[Path, Settings], ParsedDocument] | None = None,
) -> ParsedDocument:
    function = parser or parse_file
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="knowledge-parse")
    future = executor.submit(function, path, settings)
    try:
        return future.result(timeout=settings.knowledge_parser_timeout_seconds)
    except TimeoutError as exc:
        raise DocumentRejected("parser_timeout", "the parser exceeded the configured timeout") from exc
    finally:
        executor.shutdown(wait=future.done(), cancel_futures=not future.done())


def parse_file(path: Path, settings: Settings) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path, settings)
    if suffix == ".docx":
        return parse_docx(path, settings)
    if suffix in {".txt", ".md"}:
        return parse_text(path, settings)
    raise DocumentRejected("corrupt", "extension was not allowlisted")


def parse_pdf(path: Path, settings: Settings) -> ParsedDocument:
    payload = path.read_bytes()
    opener: Any = pymupdf.open
    try:
        document: Any = opener(stream=payload, filetype="pdf")
    except Exception as exc:
        raise DocumentRejected("corrupt", "PDF could not be opened") from exc
    try:
        if document.needs_pass or document.is_encrypted:
            raise DocumentRejected("encrypted", "encrypted PDF files are not ingested")
        page_count = int(document.page_count)
        if page_count > settings.knowledge_max_pages:
            raise DocumentRejected("page_limit", f"{page_count} pages exceeds the configured limit")
        sections: list[Section] = []
        empty_pages: list[int] = []
        for index, page in enumerate(document, start=1):
            try:
                extracted = str(page.get_text("text")).strip()
            except ValueError as exc:
                if "encrypted" in str(exc).lower():
                    raise DocumentRejected("encrypted", "encrypted PDF files are not ingested") from exc
                raise DocumentRejected("corrupt", "PDF text could not be read") from exc
            if not extracted:
                empty_pages.append(index)
                continue
            heading, body = _pdf_heading(extracted, index)
            sections.append(Section(heading, body, index, index))
        notes = [f"pages without text: {', '.join(str(page) for page in empty_pages)}"] if empty_pages else []
        scanned = "scanned_pdf" if page_count and not sections else None
        return _finish(sections, page_count, notes, settings, empty_reason=scanned)
    finally:
        document.close()


def parse_docx(path: Path, settings: Settings) -> ParsedDocument:
    payload = path.read_bytes()
    try:
        document = Document(BytesIO(payload))
    except (zipfile.BadZipFile, PackageNotFoundError, ValueError, OSError) as exc:
        raise DocumentRejected("corrupt", "DOCX could not be opened") from exc
    sections: list[Section] = []
    heading = ""
    body: list[str] = []

    def flush() -> None:
        text = "\n".join(part for part in body if part.strip()).strip()
        if heading or text:
            sections.append(Section(heading, text or heading, None, None))
        body.clear()

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            style_name = paragraph.style.name if paragraph.style is not None else ""
            text = paragraph.text.strip()
            if style_name.startswith("Heading"):
                flush()
                heading = text
            elif text:
                body.append(text)
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
            rendered = "\n".join(row for row in rows if row.strip())
            if rendered:
                body.append(rendered)
    flush()
    return _finish(sections, None, [], settings, empty_reason=None)


def parse_text(path: Path, settings: Settings) -> ParsedDocument:
    raw = _read_text(path)
    sections: list[Section] = []
    heading = ""
    body: list[str] = []

    def flush() -> None:
        text = "\n".join(body).strip()
        if heading or text:
            sections.append(Section(heading, text or heading, None, None))
        body.clear()

    for line in raw.splitlines():
        match = _HEADING.match(line.strip())
        if match:
            flush()
            heading = match.group(2).strip()
        else:
            body.append(line)
    flush()
    if not sections and raw.strip():
        sections.append(Section("", raw.strip(), None, None))
    return _finish(sections, None, [], settings, empty_reason=None)


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if b"\x00" in data:
        raise DocumentRejected("corrupt", "text file contains null bytes")
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentRejected("corrupt", "text encoding was not recognized")


def _pdf_heading(text: str, page_number: int) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return f"Page {page_number}", ""
    first = lines[0]
    if len(first) <= 80 and not first.endswith((".", ";", ":")):
        return first[:300], "\n".join(lines[1:]) or first
    return f"Page {page_number}", "\n".join(lines)


def _finish(
    sections: list[Section],
    page_count: int | None,
    notes: list[str],
    settings: Settings,
    *,
    empty_reason: str | None,
) -> ParsedDocument:
    characters = sum(len(section.text) + len(section.heading) for section in sections)
    if characters < settings.knowledge_min_extracted_chars:
        reason = empty_reason if empty_reason and characters == 0 else "low_quality"
        detail = "no text could be extracted" if characters == 0 else f"only {characters} characters were extracted"
        raise DocumentRejected(reason or "low_quality", detail)
    if characters > settings.knowledge_max_pages * _CHARS_PER_PAGE:
        raise DocumentRejected("page_limit", "extracted text exceeds the configured page budget")
    score = min(1.0, characters / 200)
    return ParsedDocument(sections, page_count, score, notes)
