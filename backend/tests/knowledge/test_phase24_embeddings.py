"""Phase 24: a local embedding model, indexing that runs once, and search that tolerates a half-finished re-index."""

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text

from alembic import command
from sentinel.config.settings import Settings
from sentinel.config.store import ConfigUpdateError, list_public_config, sync_public_config, update_public_config
from sentinel.knowledge import retrieval, service
from sentinel.knowledge.acl import Caller
from sentinel.knowledge.chunker import build_chunks
from sentinel.knowledge.constants import EMBEDDING_DIMENSIONS
from sentinel.knowledge.embeddings import (
    HashingEmbeddingProvider,
    OllamaEmbeddingProvider,
    build_embedding_provider,
)
from sentinel.knowledge.retrieval import _excerpt, _rank_code, _rerank_bonus, search
from sentinel.knowledge.service import KnowledgeIndexer
from sentinel.knowledge.types import EmbeddingError, ParsedDocument, Section
from sentinel.persistence.models import Document, DocumentChunk

ADMIN = Caller("admin", "sentinel")


@pytest.fixture
def settings(tmp_path: Path):
    intake = tmp_path / "intake"
    for name in ("ora-19c-manual", "Policy", "Quarantine"):
        (intake / name).mkdir(parents=True)
    (intake / "ora-19c-manual" / "undo.md").write_text(
        "# Undo retention\n\nOracle keeps undo so that a long query can read data as it was when the query began.\n",
        encoding="utf-8",
    )
    (intake / "ora-19c-manual" / "redo.md").write_text(
        "# Redo log\n\nThe redo log records every change so that committed work survives an instance failure.\n",
        encoding="utf-8",
    )
    (intake / "Policy" / "archivelog-standard.md").write_text(
        "# ARCHIVELOG standard\n\nEvery production database must run in ARCHIVELOG mode.\n", encoding="utf-8",
    )
    return Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'sentinel.db'}", synthetic_mode=True,
        scheduler_enabled=False, knowledge_enabled=False, knowledge_intake_path=str(intake),
    )


class _Embedder:
    """A stand-in embedding model: 768 numbers, deterministic, and it counts what it was asked to embed."""

    provider_name = "ollama"
    disclosure = "Embedded on this computer."

    def __init__(self, model: str = "stub-embed") -> None:
        self.model = model
        self.documents: list[str] = []
        self.fail = False

    def embed_documents_sync(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise EmbeddingError("The local embedding model did not answer. Check that Ollama is running.")
        self.documents.extend(texts)
        return [self._vector(item) for item in texts]

    def embed_query_sync(self, text_: str) -> list[float]:
        if self.fail:
            raise EmbeddingError("The local embedding model did not answer. Check that Ollama is running.")
        return self._vector(text_)

    @staticmethod
    def _vector(value: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        folded = value.lower()
        vector[0] = 1.0 if "undo" in folded or "long query" in folded else 0.0
        vector[1] = 1.0 if "redo" in folded or "crash" in folded else 0.0
        vector[2] = 1.0 if "archivelog" in folded else 0.0
        if not any(vector[:3]):
            vector[3] = 1.0
        return vector


def _use(monkeypatch, embedder: _Embedder) -> None:
    monkeypatch.setattr(service, "build_embedding_provider", lambda _settings: embedder)
    monkeypatch.setattr(retrieval, "build_embedding_provider", lambda _settings: embedder)


def _documents(sessions) -> dict[str, Document]:
    with sessions() as session:
        return {row.relative_path: row for row in session.scalars(select(Document))}


def _server(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_the_local_model_is_asked_in_batches_with_its_task_prefix() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path == "/api/embed"
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0] + [0.0] * (EMBEDDING_DIMENSIONS - 2) for _ in body["input"]]})

    provider = OllamaEmbeddingProvider("http://127.0.0.1:11434", "nomic-embed-text:latest", 30, batch_size=2, transport=_server(handler))
    vectors = provider.embed_documents_sync(["one", "two", "three"])
    assert [len(item["input"]) for item in seen] == [2, 1]
    assert seen[0]["input"] == ["search_document: one", "search_document: two"]
    assert seen[0]["model"] == "nomic-embed-text:latest" and seen[0]["truncate"] is True
    assert len(vectors) == 3 and len(vectors[0]) == EMBEDDING_DIMENSIONS
    assert vectors[0][:2] == [0.6, 0.8]
    provider.embed_query_sync("why")
    assert seen[-1]["input"] == ["search_query: why"]
    plain = OllamaEmbeddingProvider("http://localhost:11434/", "other-model", 30, transport=_server(handler))
    plain.embed_documents_sync(["one"])
    assert seen[-1]["input"] == ["one"]
    assert provider.embed_documents_sync([]) == []


def test_local_model_failures_are_plain_messages() -> None:
    def missing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    def wrong_size(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[1.0] * 1024 for _ in json.loads(request.content)["input"]]})

    def short(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": []})

    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    cases = (
        (missing, "The embedding model nomic-embed-text is not installed. Run: ollama pull nomic-embed-text"),
        (wrong_size, "nomic-embed-text returns 1024 numbers per passage and this installation stores 768."),
        (short, "The embedding count did not match the passages"),
        (down, "The local embedding model did not answer. Check that Ollama is running."),
    )
    for handler, message in cases:
        provider = OllamaEmbeddingProvider("http://127.0.0.1:11434", "nomic-embed-text", 30, transport=_server(handler))
        with pytest.raises(EmbeddingError) as raised:
            provider.embed_documents_sync(["passage"])
        assert message in str(raised.value)
    with pytest.raises(EmbeddingError, match="must run on this computer"):
        OllamaEmbeddingProvider("http://embeddings.example.com:11434", "nomic-embed-text", 30)


def test_settings_keep_the_embedding_model_on_this_computer(settings) -> None:
    base = settings.model_dump()
    chosen = Settings.model_validate({**base, "embedding_provider": "ollama"})
    provider = build_embedding_provider(chosen)
    assert isinstance(provider, OllamaEmbeddingProvider)
    assert (provider.provider_name, provider.model, provider.url) == ("ollama", "nomic-embed-text", "http://127.0.0.1:11434/api/embed")
    assert isinstance(build_embedding_provider(settings), HashingEmbeddingProvider)
    assert settings.embedding_dimensions == EMBEDDING_DIMENSIONS == 768
    for change in (
        {"embedding_local_url": "http://10.0.0.5:11434"},
        {"embedding_ollama_model": "bad name; rm"},
        {"embedding_ollama_model": ""},
    ):
        with pytest.raises(ValueError):
            Settings.model_validate({**base, "embedding_provider": "ollama", **change})
    with pytest.raises(ValueError):
        Settings.model_validate({**base, "embedding_provider": "somewhere"})
    with pytest.raises(ValueError):
        Settings.model_validate({**base, "embedding_provider": "remote"})


def test_an_administrator_can_switch_the_provider_and_cannot_point_it_elsewhere(settings, sessions) -> None:
    with sessions.begin() as session:
        sync_public_config(session, settings)
        items = {item["key"]: item for item in list_public_config(session)}
        assert items["embedding_provider"]["editable"] and items["embedding_ollama_model"]["editable"]
        assert not items["embedding_local_url"]["editable"]
        assert not items["embedding_remote_url"]["editable"] and not items["embedding_remote_approved"]["editable"]
        assert update_public_config(session, settings, "embedding_provider", "ollama") == "ollama"
        assert settings.embedding_provider == "ollama"
        with pytest.raises(ConfigUpdateError):
            update_public_config(session, settings, "embedding_provider", "remote")
        with pytest.raises(ConfigUpdateError):
            update_public_config(session, settings, "embedding_local_url", "http://10.0.0.5:11434")


def test_a_second_run_skips_everything_and_reports_each_file(settings, sessions) -> None:
    seen: list[tuple[str, str, int]] = []

    def progress(relative: str, outcome: str, passages: int, seconds: float) -> None:
        assert seconds >= 0
        seen.append((relative, outcome, passages))

    first = KnowledgeIndexer(sessions, settings).scan(progress=progress)
    assert (first.indexed, first.unchanged, first.reembedded, first.errors) == (3, 0, 0, [])
    assert sorted(seen) == [
        ("Policy/archivelog-standard.md", "indexed", 1),
        ("ora-19c-manual/redo.md", "indexed", 1),
        ("ora-19c-manual/undo.md", "indexed", 1),
    ]
    seen.clear()
    second = KnowledgeIndexer(sessions, settings).scan(progress=progress)
    assert (second.indexed, second.updated, second.unchanged, second.reembedded) == (0, 0, 3, 0)
    assert {item[1:] for item in seen} == {("unchanged", 0)}


def test_a_new_model_embeds_the_stored_passages_again_without_parsing_the_files(settings, sessions, monkeypatch) -> None:
    KnowledgeIndexer(sessions, settings).scan()
    before = _documents(sessions)
    assert {row.embedding_model for row in before.values()} == {"local-hashing-v1"}

    def refuse(*_args, **_kwargs):
        raise AssertionError("An unchanged file must not be parsed again")

    monkeypatch.setattr(service, "parse_with_timeout", refuse)
    embedder = _Embedder()
    _use(monkeypatch, embedder)
    report = KnowledgeIndexer(sessions, settings).scan()
    assert (report.reembedded, report.indexed, report.updated, report.unchanged, report.errors) == (3, 0, 0, 0, [])
    after = _documents(sessions)
    assert {(row.embedding_provider, row.embedding_model) for row in after.values()} == {("ollama", "stub-embed")}
    assert {path: row.version for path, row in after.items()} == {path: row.version for path, row in before.items()}
    assert len(embedder.documents) == 3
    with sessions() as session:
        chunk = session.scalars(
            select(DocumentChunk).join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.relative_path == "ora-19c-manual/undo.md"),
        ).one()
        assert chunk.embedding[:4] == [1.0, 0.0, 0.0, 0.0]
    again = KnowledgeIndexer(sessions, settings).scan()
    assert (again.unchanged, again.reembedded) == (3, 0)
    assert len(embedder.documents) == 3


def test_only_a_changed_file_is_indexed_again_and_a_removed_file_leaves_search(settings, sessions, monkeypatch) -> None:
    embedder = _Embedder()
    _use(monkeypatch, embedder)
    KnowledgeIndexer(sessions, settings).scan()
    intake = Path(settings.knowledge_intake_path)
    (intake / "ora-19c-manual" / "undo.md").write_text(
        "# Undo retention\n\nUndo is kept for the undo retention period so a long query stays consistent.\n", encoding="utf-8",
    )
    (intake / "ora-19c-manual" / "redo.md").unlink()
    embedder.documents.clear()
    report = KnowledgeIndexer(sessions, settings).scan()
    assert (report.updated, report.unchanged, report.deleted, report.reembedded) == (1, 1, 1, 0)
    assert len(embedder.documents) == 1
    documents = _documents(sessions)
    assert documents["ora-19c-manual/undo.md"].version == 2
    assert documents["ora-19c-manual/redo.md"].ingestion_status == "deleted"
    with sessions() as session:
        found = [item.citation.relative_path for item in search(session, settings, "redo log crash", ADMIN, limit=5)]
    assert "ora-19c-manual/redo.md" not in found


def test_an_interrupted_run_continues_with_the_unfinished_files(settings, sessions, monkeypatch) -> None:
    KnowledgeIndexer(sessions, settings).scan()

    class _Stops(_Embedder):
        def embed_documents_sync(self, texts: list[str]) -> list[list[float]]:
            if any("Redo" in item or "redo" in item for item in texts):
                raise EmbeddingError("The local embedding model did not answer. Check that Ollama is running.")
            return super().embed_documents_sync(texts)

    stopping = _Stops()
    _use(monkeypatch, stopping)
    first = KnowledgeIndexer(sessions, settings).scan()
    assert first.reembedded == 2
    assert first.errors == ["ora-19c-manual/redo.md: The local embedding model did not answer. Check that Ollama is running."]
    models = {path: row.embedding_model for path, row in _documents(sessions).items()}
    assert models["ora-19c-manual/redo.md"] == "local-hashing-v1"
    assert models["ora-19c-manual/undo.md"] == "stub-embed"
    working = _Embedder()
    _use(monkeypatch, working)
    second = KnowledgeIndexer(sessions, settings).scan()
    assert (second.reembedded, second.unchanged, second.errors) == (1, 2, [])
    assert len(working.documents) == 1


def test_search_compares_only_passages_of_the_current_model(settings, sessions, monkeypatch) -> None:
    KnowledgeIndexer(sessions, settings).scan()

    class _OnlyUndo(_Embedder):
        def embed_documents_sync(self, texts: list[str]) -> list[list[float]]:
            if not any("undo" in item.lower() for item in texts):
                raise EmbeddingError("stopped")
            return super().embed_documents_sync(texts)

    partial = _OnlyUndo()
    _use(monkeypatch, partial)
    KnowledgeIndexer(sessions, settings).scan()
    with sessions() as session:
        by_meaning = search(session, settings, "a long query", ADMIN, limit=3, category="Knowledge")
        assert [item.citation.relative_path for item in by_meaning] == ["ora-19c-manual/undo.md"]
        by_word = search(session, settings, "redo log", ADMIN, limit=3, category="Knowledge")
        assert by_word[0].citation.relative_path == "ora-19c-manual/redo.md"
        # "crash" is in neither file's text. Only a vector could match it, and redo.md still has the old model's vector.
        assert search(session, settings, "crash", ADMIN, limit=3, category="Knowledge") == []
    assert "documents.embedding_model = :model" in retrieval.VECTOR_CANDIDATE_SQL
    assert "document_chunks.embedding IS NOT NULL" in retrieval.VECTOR_CANDIDATE_SQL


def test_search_falls_back_to_words_when_the_embedding_model_is_down(settings, sessions, monkeypatch) -> None:
    embedder = _Embedder()
    _use(monkeypatch, embedder)
    KnowledgeIndexer(sessions, settings).scan()
    embedder.fail = True
    with sessions() as session:
        found = search(session, settings, "redo log", ADMIN, limit=3)
        assert found and found[0].citation.relative_path == "ora-19c-manual/redo.md"
        assert search(session, settings, "crash", ADMIN, limit=3) == []


def test_with_an_embedding_model_a_heading_word_no_longer_outranks_the_meaning() -> None:
    chunk = DocumentChunk(heading="processes (continued)", content="server processes, 12-3")
    assert _rerank_bonus(chunk, "memory shared by all server processes") == 0.05
    assert _rerank_bonus(chunk, "memory shared by all server processes", semantic=True) == 0.0
    exact = DocumentChunk(heading="Undo", content="the undo retention period is tuned automatically")
    assert _rerank_bonus(exact, "undo retention period") == pytest.approx(0.2)
    assert _rerank_bonus(exact, "undo retention period", semantic=True) == pytest.approx(0.02)


def test_the_migration_clears_old_vectors_and_marks_documents_for_embedding(settings, sessions, migration_config) -> None:
    KnowledgeIndexer(sessions, settings).scan()
    with sessions.begin() as session:
        sync_public_config(session, settings)
    command.downgrade(migration_config, "0011")
    with sessions.begin() as session:
        assert session.execute(text("SELECT value FROM app_config WHERE key = 'embedding_dimensions'")).scalar() in ("128", 128)
        session.execute(text("UPDATE documents SET embedding_model = 'local-hashing-v1'"))
        session.execute(text("UPDATE document_chunks SET embedding = '[0.5, 0.5]'"))
    command.upgrade(migration_config, "head")
    with sessions() as session:
        assert session.execute(text("SELECT COUNT(*) FROM document_chunks WHERE embedding IS NOT NULL")).scalar() == 0
        assert session.execute(text("SELECT COUNT(*) FROM document_chunks")).scalar() == 3
        assert {row[0] for row in session.execute(text("SELECT embedding_model FROM documents"))} == {""}
        assert session.execute(text("SELECT value FROM app_config WHERE key = 'embedding_dimensions'")).scalar() in ("768", 768)
    report = KnowledgeIndexer(sessions, settings).scan()
    assert (report.reembedded, report.indexed, report.errors) == (3, 0, [])


def test_a_message_code_finds_the_entry_that_defines_it(settings, sessions, monkeypatch) -> None:
    intake = Path(settings.knowledge_intake_path)
    (intake / "ora-19c-manual" / "messages.md").write_text(
        "# ORA-00253 to ORA-00258\n\nORA-00253: character limit exceeded by archive destination string.\n\n"
        "ORA-00257: Archiver error. Connect AS SYSDBA only until resolved. Cause: The archiver process received an error.\n",
        encoding="utf-8",
    )
    (intake / "ora-19c-manual" / "views.md").write_text(
        "# Undo statistics\n\nSSOLDERRCNT counts the times the error ORA-00257 was mentioned by mistake in this column text.\n",
        encoding="utf-8",
    )
    _use(monkeypatch, _Embedder())
    KnowledgeIndexer(sessions, settings).scan()
    with sessions() as session:
        found = search(session, settings, "what does ora-00257 mean and what should I do", ADMIN, limit=3, category="Knowledge")
        assert found[0].citation.relative_path == "ora-19c-manual/messages.md"
        assert found[0].excerpt.startswith("ORA-00257: Archiver error.")
        assert [item.citation.relative_path for item in found][:2] == ["ora-19c-manual/messages.md", "ora-19c-manual/views.md"]
    assert "phraseto_tsquery('english', :code)" in retrieval.CODE_CANDIDATE_SQL
    assert ":query" not in retrieval.CODE_CANDIDATE_SQL


def test_code_ranking_and_excerpt_rules() -> None:
    entry = DocumentChunk(id="00000000-0000-0000-0000-000000000001", heading="ORA-00253", content="ORA-00253: x. ORA-00257: Archiver error.")
    mention = DocumentChunk(id="00000000-0000-0000-0000-000000000002", heading="Column", content="See ORA-00257 for details.")
    titled = DocumentChunk(id="00000000-0000-0000-0000-000000000003", heading="ORA-00257: Archiver error", content="ORA-00257: Archiver error. Cause.")
    other = DocumentChunk(id="00000000-0000-0000-0000-000000000004", heading="Other", content="Nothing here.")
    assert _rank_code([mention, other, entry, titled], ["ORA-00257"]) == [str(titled.id), str(entry.id), str(mention.id)]
    assert _rank_code([mention], []) == []
    assert _excerpt("ORA-00253: x. ORA-00257: Archiver error.", ["ORA-00257"]) == "ORA-00257: Archiver error."
    assert _excerpt("Plain passage.", ["ORA-00257"]) == "Plain passage."
    assert _excerpt("Plain passage.") == "Plain passage."


def test_text_postgresql_cannot_store_is_removed_before_it_is_saved(settings) -> None:
    parsed = ParsedDocument(
        sections=[Section(heading="Undo\x00 retention", text="Oracle keeps\x00 undo for long queries.", page_start=1, page_end=1)],
        page_count=1, quality_score=1.0, notes=[],
    )
    drafts = build_chunks(parsed, settings)
    assert drafts and all("\x00" not in draft.text and "\x00" not in draft.heading for draft in drafts)
    assert drafts[0].heading == "Undo retention"
    assert "Oracle keeps undo for long queries." in drafts[0].text
