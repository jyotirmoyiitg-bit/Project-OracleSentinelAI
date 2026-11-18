"""Scheduled and on-demand ingestion of the ora-19c-manual and Policy folders."""

import logging
import threading
import time
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.config.settings import Settings
from sentinel.knowledge.chunker import build_chunks
from sentinel.knowledge.embeddings import (
    HashingEmbeddingProvider,
    OllamaEmbeddingProvider,
    RemoteEmbeddingProvider,
    build_embedding_provider,
)
from sentinel.knowledge.parsers import parse_with_timeout
from sentinel.knowledge.paths import intake_root
from sentinel.knowledge.quarantine import quarantine_file
from sentinel.knowledge.store import chunk_texts, mark_missing, mark_quarantined, persist_document, reembed_document
from sentinel.knowledge.types import DiscoveredFile, DocumentRejected, EmbeddingError, ScanInProgress, ScanReport
from sentinel.knowledge.watcher import discover
from sentinel.persistence.models import Document

logger = logging.getLogger(__name__)
_SCAN_LOCK = threading.Lock()
Provider = HashingEmbeddingProvider | OllamaEmbeddingProvider | RemoteEmbeddingProvider
# Called once per file with its relative path, what happened, how many passages, and the seconds it took.
Progress = Callable[[str, str, int, float], None]


class KnowledgeIndexer:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings) -> None:
        self.sessions = sessions
        self.settings = settings

    def scan(self, paths: list[str] | None = None, progress: Progress | None = None) -> ScanReport:
        """Index new and changed files. A file whose content and embedding model are unchanged is skipped.

        Each file is committed on its own, so a stopped run continues with the next unfinished file.
        """
        if not _SCAN_LOCK.acquire(blocking=False):
            raise ScanInProgress("A knowledge scan is already running")
        try:
            return self._scan(paths, progress)
        finally:
            _SCAN_LOCK.release()

    def scan_safe(self) -> None:
        try:
            self.scan()
        except ScanInProgress:
            logger.info("Knowledge scan already running")
        except Exception:
            logger.exception("Knowledge scan failed")

    def _scan(self, paths: list[str] | None, progress: Progress | None = None) -> ScanReport:
        report = ScanReport()
        discovery = discover(self.settings)
        report.skipped = discovery.skipped
        report.errors.extend(discovery.errors)
        report.quarantined = len(discovery.quarantined)
        for event in discovery.quarantined:
            self._update_status(event.relative)
        selected = discovery.files
        if paths is not None:
            available = {item.relative: item for item in discovery.files}
            selected = []
            for relative in paths:
                item = available.get(relative)
                if item is None:
                    report.errors.append(f"{relative}: not an allowlisted file under ora-19c-manual or Policy")
                else:
                    selected.append(item)
        report.scanned = len(selected)
        provider = build_embedding_provider(self.settings)
        for item in selected:
            started = time.monotonic()
            before = (report.indexed, report.updated, report.reembedded, report.unchanged, report.quarantined, len(report.errors))
            passages = self._ingest(item, provider, report)
            if progress is not None:
                after = (report.indexed, report.updated, report.reembedded, report.unchanged, report.quarantined, len(report.errors))
                names = ("indexed", "updated", "re-embedded", "unchanged", "quarantined", "failed")
                outcome = next((name for name, old, new in zip(names, before, after, strict=True) if new > old), "unchanged")
                progress(item.relative, outcome, passages, time.monotonic() - started)
        if paths is None:
            seen = {item.relative for item in discovery.files}
            try:
                with self.sessions.begin() as session:
                    report.deleted = mark_missing(session, seen)
            except Exception as exc:
                logger.exception("Knowledge deletion pass failed")
                report.errors.append(f"deletion: {exc.__class__.__name__}")
        return report

    def _ingest(self, item: DiscoveredFile, provider: Provider, report: ScanReport) -> int:
        """Index one file and return how many passages were embedded."""
        state = self._state(item, provider)
        if state == "unchanged":
            report.unchanged += 1
            return 0
        if state == "reembed":
            return self._reembed(item, provider, report)
        try:
            parsed = parse_with_timeout(item.path, self.settings)
            drafts = build_chunks(parsed, self.settings)
            vectors = provider.embed_documents_sync([draft.text for draft in drafts])
        except DocumentRejected as exc:
            self._reject(item, exc.reason, exc.detail, report)
            return 0
        except EmbeddingError as exc:
            report.errors.append(f"{item.relative}: {exc}")
            return 0
        except Exception as exc:
            logger.exception("Knowledge ingest failed for %s", item.relative)
            report.errors.append(f"{item.relative}: {exc.__class__.__name__}")
            return 0
        try:
            with self.sessions.begin() as session:
                outcome = persist_document(
                    session,
                    item,
                    parsed,
                    drafts,
                    vectors,
                    provider_name=provider.provider_name,
                    model_name=provider.model,
                    disclosure=provider.disclosure,
                )
        except Exception as exc:
            logger.exception("Knowledge persist failed for %s", item.relative)
            report.errors.append(f"{item.relative}: {exc.__class__.__name__}")
            return 0
        if outcome == "indexed":
            report.indexed += 1
        elif outcome == "updated":
            report.updated += 1
        else:
            report.unchanged += 1
            return 0
        return len(drafts)

    def _state(self, item: DiscoveredFile, provider: Provider) -> str:
        """unchanged: skip. reembed: same file, different embedding model. ingest: new or changed file."""
        with self.sessions() as session:
            existing = session.scalar(select(Document).where(Document.relative_path == item.relative))
            if existing is None or existing.content_hash != item.content_hash or existing.ingestion_status != "indexed":
                return "ingest"
            if existing.embedding_provider == provider.provider_name and existing.embedding_model == provider.model:
                return "unchanged"
            return "reembed"

    def _reembed(self, item: DiscoveredFile, provider: Provider, report: ScanReport) -> int:
        """Embed the stored passages again with the current model. The file is not parsed again."""
        try:
            with self.sessions.begin() as session:
                document = session.scalar(select(Document).where(Document.relative_path == item.relative))
                if document is None:
                    return 0
                pairs = chunk_texts(session, document)
                vectors = provider.embed_documents_sync([text for _chunk, text in pairs])
                reembed_document(
                    session, document, [chunk for chunk, _text in pairs], vectors,
                    provider_name=provider.provider_name, model_name=provider.model, disclosure=provider.disclosure,
                )
        except EmbeddingError as exc:
            report.errors.append(f"{item.relative}: {exc}")
            return 0
        except Exception as exc:
            logger.exception("Knowledge re-embedding failed for %s", item.relative)
            report.errors.append(f"{item.relative}: {exc.__class__.__name__}")
            return 0
        report.reembedded += 1
        return len(vectors)

    def _reject(self, item: DiscoveredFile, reason: str, detail: str, report: ScanReport) -> None:
        try:
            quarantine_file(intake_root(self.settings), item.path, item.relative, reason, detail)
        except OSError as exc:
            report.errors.append(f"{item.relative}: {exc.__class__.__name__}")
            return
        self._update_status(item.relative)
        report.quarantined += 1

    def _update_status(self, relative: str) -> None:
        try:
            with self.sessions.begin() as session:
                mark_quarantined(session, relative)
        except Exception:
            logger.exception("Could not record quarantine for %s", relative)


class KnowledgeScheduler:
    """Folder scan schedule. It is independent of Oracle collection."""

    def __init__(self, indexer: KnowledgeIndexer, interval_seconds: int) -> None:
        self.scheduler = BackgroundScheduler(
            timezone="UTC",
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
        )
        self.scheduler.add_job(
            indexer.scan_safe,
            "interval",
            seconds=interval_seconds,
            id="knowledge-scan",
            replace_existing=True,
        )

    def start(self) -> None:
        self.scheduler.start()
        logger.info("Knowledge scheduler started")

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Knowledge scheduler stopped")
