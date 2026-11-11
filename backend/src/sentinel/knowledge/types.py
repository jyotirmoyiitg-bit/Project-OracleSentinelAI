"""Value types for parsing, scanning, and cited retrieval."""

from dataclasses import dataclass, field
from pathlib import Path


class DocumentRejected(Exception):
    """A file cannot be indexed and should be quarantined."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


class EmbeddingError(Exception):
    """The configured embedding provider could not produce vectors."""


class ScanInProgress(Exception):
    """Another scan in this process already holds the knowledge lock."""


@dataclass(frozen=True)
class Section:
    heading: str
    text: str
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class ParsedDocument:
    sections: list[Section]
    page_count: int | None
    quality_score: float
    notes: list[str]


@dataclass(frozen=True)
class ChunkDraft:
    heading: str
    text: str
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class DiscoveredFile:
    path: Path
    relative: str
    category: str
    size_bytes: int
    content_hash: str


@dataclass(frozen=True)
class QuarantineEvent:
    relative: str
    reason: str
    detail: str
    destination: str


@dataclass
class Discovery:
    files: list[DiscoveredFile] = field(default_factory=list)
    quarantined: list[QuarantineEvent] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ScanReport:
    scanned: int = 0
    indexed: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    quarantined: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Citation:
    document_id: str
    document_name: str
    relative_path: str
    category: str
    version: int
    page_start: int | None
    page_end: int | None
    heading: str
    chunk_index: int


@dataclass(frozen=True)
class Passage:
    chunk_id: str
    excerpt: str
    score: float
    authoritative_policy: bool
    citation: Citation
