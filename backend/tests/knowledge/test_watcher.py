"""Folder watching, quarantine, versioning, and deletion."""

import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import func, select

from sentinel.config.settings import Settings
from sentinel.knowledge.paths import validate_requested_path
from sentinel.knowledge.service import KnowledgeIndexer, KnowledgeScheduler
from sentinel.persistence.models import Document, DocumentChunk


@pytest.fixture
def settings(tmp_path: Path):
    intake = tmp_path / "intake"
    for name in ("Knowledge", "Policy", "Quarantine"):
        (intake / name).mkdir(parents=True)
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'sentinel.db'}",
        synthetic_mode=True,
        scheduler_enabled=False,
        knowledge_enabled=False,
        knowledge_intake_path=str(intake),
    )


@pytest.fixture
def intake(settings: Settings) -> Path:
    return Path(settings.knowledge_intake_path)


def test_requested_paths_cannot_escape_the_intake() -> None:
    assert validate_requested_path("Knowledge/internal-runbooks/ora-00257.md") == (
        "Knowledge/internal-runbooks/ora-00257.md"
    )
    for relative in ("../.env", "Knowledge/../../.env", "/etc/passwd", "notes/secret.md", "Knowledge/../Policy/a.md"):
        with pytest.raises(ValueError):
            validate_requested_path(relative)


def test_corrupt_oversized_and_malware_files_are_quarantined(
    settings, intake, sessions, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (intake / "Knowledge" / "broken.pdf").write_bytes(b"not a pdf")
    (intake / "Knowledge" / "huge.md").write_text("ARCHIVELOG " * 20, encoding="utf-8")
    scanner = intake / "scanner.exe"
    scanner.write_bytes(b"")

    def scanner_result(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        code = 1 if str(args[1]).endswith("unsafe.md") else 0
        return subprocess.CompletedProcess(args, code)

    monkeypatch.setattr("sentinel.knowledge.quarantine.subprocess.run", scanner_result)
    settings.knowledge_max_file_bytes = 32
    settings.knowledge_malware_scanner = str(scanner)
    (intake / "Knowledge" / "unsafe.md").write_text("Unsafe scanner note.", encoding="utf-8")
    report = KnowledgeIndexer(sessions, settings).scan()
    reasons = _reasons(intake)
    assert report.quarantined >= 2
    assert "corrupt" in reasons
    assert "too_large" in reasons
    assert "malware" in reasons
    assert not (intake / "Knowledge" / "broken.pdf").exists()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DocumentChunk)) == 0


def test_new_changed_and_deleted_documents_update_the_index(settings, intake, sessions) -> None:
    path = intake / "Knowledge" / "runbook.md"
    path.write_text("# ORA-00257\n\nThe fast recovery area is full and the archiver is waiting.\n", encoding="utf-8")
    indexer = KnowledgeIndexer(sessions, settings)
    first = indexer.scan()
    second = indexer.scan()
    path.write_text(
        "# ORA-00257\n\nThe fast recovery area was enlarged and the archiver can continue.\n",
        encoding="utf-8",
    )
    third = indexer.scan()
    path.unlink()
    fourth = indexer.scan()

    assert first.indexed == 1
    assert second.unchanged == 1
    assert third.updated == 1
    assert fourth.deleted == 1
    with sessions() as session:
        document = session.scalar(select(Document))
        assert document is not None
        assert document.version == 2
        assert document.ingestion_status == "deleted"
        assert document.previous_hash
        assert session.scalar(select(func.count()).select_from(DocumentChunk)) == 0


def test_symlink_outside_the_intake_is_not_indexed(settings, intake, sessions, tmp_path: Path) -> None:
    secret = tmp_path / "secret.md"
    secret.write_text("secret credential material that must stay outside the index.", encoding="utf-8")
    link = intake / "Knowledge" / "secret.md"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks are not permitted in this environment")
    report = KnowledgeIndexer(sessions, settings).scan()
    assert secret.exists()
    assert secret.read_text(encoding="utf-8").startswith("secret credential")
    assert report.indexed == 0
    assert report.quarantined == 1
    assert "symlink" in _reasons(intake)


def test_document_text_is_stored_and_not_executed(settings, intake, sessions, tmp_path: Path) -> None:
    marker = tmp_path / "executed.txt"
    (intake / "Knowledge" / "trap.md").write_text(
        "'); DROP TABLE documents; --\n"
        "import os; os.system('echo executed')\n"
        "ARCHIVELOG remains the required production log mode.\n",
        encoding="utf-8",
    )
    KnowledgeIndexer(sessions, settings).scan()
    assert not marker.exists()
    with sessions() as session:
        document = session.scalar(select(Document))
        chunk = session.scalar(select(DocumentChunk))
        assert document is not None and chunk is not None
        assert "DROP TABLE" in chunk.content


def test_scan_failure_is_contained_and_scheduler_job_is_registered(settings, sessions, monkeypatch) -> None:
    indexer = KnowledgeIndexer(sessions, settings)

    def explode(_paths: list[str] | None = None):
        raise RuntimeError("boom")

    monkeypatch.setattr(indexer, "scan", explode)
    indexer.scan_safe()
    scheduler = KnowledgeScheduler(indexer, 300)
    assert scheduler.scheduler.get_job("knowledge-scan") is not None
    scheduler.stop()


def _reasons(intake: Path) -> set[str]:
    log = intake / "Quarantine" / "quarantine.log"
    return {json.loads(line)["reason"] for line in log.read_text(encoding="utf-8").splitlines()}
