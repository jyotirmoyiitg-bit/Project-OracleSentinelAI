"""Split parsed sections into overlapping chunks that keep their citation."""

from sentinel.config.settings import Settings
from sentinel.knowledge.types import ChunkDraft, DocumentRejected, ParsedDocument, Section


def split_text(text: str, size: int, overlap: int) -> list[str]:
    if size < 1:
        raise ValueError("chunk size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be smaller than chunk size")
    normalized = " ".join(text.split())
    if not normalized:
        return []
    if len(normalized) <= size:
        return [normalized]
    parts: list[str] = []
    start = 0
    guard = 0
    while start < len(normalized):
        guard += 1
        if guard > len(normalized):
            break
        end = min(len(normalized), start + size)
        if end < len(normalized):
            window = normalized.rfind(" ", start + max(overlap, 1), end)
            if window != -1:
                end = window
        part = normalized[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(normalized):
            break
        next_start = end - overlap if overlap else end
        start = end if next_start <= start else next_start
    return parts


def build_chunks(parsed: ParsedDocument, settings: Settings) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for section in parsed.sections:
        drafts.extend(_section_chunks(section, settings.knowledge_chunk_size, settings.knowledge_chunk_overlap))
    if not drafts:
        raise DocumentRejected("low_quality", "no indexable text")
    return drafts


def _section_chunks(section: Section, size: int, overlap: int) -> list[ChunkDraft]:
    body = section.text.strip()
    source = f"{section.heading}\n{body}".strip() if section.heading else body
    heading = section.heading[:300]
    return [
        ChunkDraft(heading, part, section.page_start, section.page_end)
        for part in split_text(source, size, overlap)
    ]
