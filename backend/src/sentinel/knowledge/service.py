"""Scheduled and on-demand ingestion of the Knowledge and Policy folders."""

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.config.settings import Settings
from sentinel.knowledge.chunker import build_chunks
from sentinel.knowledge.embeddings import (
    HashingEmbeddingProvider,
    RemoteEmbeddingProvider,
    build_embedding_provider,
)
from sentinel.knowledge.parsers import parse_with_timeout
from sentinel.knowledge.paths import intake_root
from sentinel.knowledge.quarantine import quarantine_file
from sentinel.knowledge.store import mark_missing, mark_quarantined, persist_document
from sentinel.knowledge.types import DiscoveredFile, DocumentRejected, EmbeddingError, ScanInProgress, ScanReport
from sentinel.knowledge.watcher import discover
from sentinel.persistence.models import Document

logger = logging.getLogger(__name__)
_SCAN_LOCK = threading.Lock()


class KnowledgeIndexer:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings) -> None:
        self.sessions = sessions
        self.settings = settings

    def scan(self, paths: list[str] | None = None) -> ScanReport:
        if not _SCAN_LOCK.acquire(blocking=False):
            raise ScanInProgress("A knowledge scan is already running")
        try:
            return self._scan(paths)
        finally:
            _SCAN_LOCK.release()

    def scan_safe(self) -> None:
        try:
            self.scan()
        except ScanInProgress:
            logger.info("Knowledge scan already running")
        except Exception:
            logger.exception("Knowledge scan failed")

    def _scan(self, paths: list[str] | None) -> ScanReport:
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
                    report.errors.append(f"{relative}: not an allowlisted file under Knowledge or Policy")
                else:
                    selected.append(item)
        report.scanned = len(selected)
        provider = build_embedding_provider(self.settings)
        for item in selected:
            self._ingest(item, provider, report)
        if paths is None:
            seen = {item.relative for item in discovery.files}
            try:
                with self.sessions.begin() as session:
                    report.deleted = mark_missing(session, seen)
            except Exception as exc:
                logger.exception("Knowledge deletion pass failed")
                report.errors.append(f"deletion: {exc.__class__.__name__}")
        return report

    def _ingest(
        self,
        item: DiscoveredFile,
        provider: HashingEmbeddingProvider | RemoteEmbeddingProvider,
        report: ScanReport,
    ) -> None:
        if self._unchanged(item):
            report.unchanged += 1
            return
        try:
            parsed = parse_with_timeout(item.path, self.settings)
            drafts = build_chunks(parsed, self.settings)
            vectors = provider.embed_documents_sync([draft.text for draft in drafts])
        except DocumentRejected as exc:
            self._reject(item, exc.reason, exc.detail, report)
            return
        except EmbeddingError as exc:
            report.errors.append(f"{item.relative}: {exc}")
            return
        except Exception as exc:
            logger.exception("Knowledge ingest failed for %s", item.relative)
            report.errors.append(f"{item.relative}: {exc.__class__.__name__}")
            return
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
            return
        if outcome == "indexed":
            report.indexed += 1
        elif outcome == "updated":
            report.updated += 1
        else:
            report.unchanged += 1

    def _unchanged(self, item: DiscoveredFile) -> bool:
        with self.sessions() as session:
            existing = session.scalar(select(Document).where(Document.relative_path == item.relative))
            return (
                existing is not None
                and existing.content_hash == item.content_hash
                and existing.ingestion_status == "indexed"
            )

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
