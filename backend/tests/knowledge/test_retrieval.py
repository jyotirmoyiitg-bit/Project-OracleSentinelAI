"""Hybrid retrieval, ACL filtering, citations, and ranking quality."""

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller
from sentinel.knowledge.constants import PRIORITIZATION
from sentinel.knowledge.evaluation import citation_grounded, ndcg_at_k, recall_at_k
from sentinel.knowledge.retrieval import KEYWORD_CANDIDATE_SQL, VECTOR_CANDIDATE_SQL, search
from sentinel.knowledge.service import KnowledgeIndexer
from sentinel.knowledge.types import ChunkDraft
from sentinel.persistence.models import DocumentChunk


@pytest.fixture
def settings(tmp_path: Path):
    intake = tmp_path / "intake"
    for name in ("Knowledge", "Policy", "Quarantine"):
        (intake / name).mkdir(parents=True)
    (intake / "Policy" / "archivelog-standard.md").write_text(
        "# ARCHIVELOG standard\n\nEvery production Oracle database must run in ARCHIVELOG mode. "
        "NOARCHIVELOG is non-compliant for production.\n",
        encoding="utf-8",
    )
    (intake / "Knowledge").joinpath("internal-runbooks").mkdir()
    (intake / "Knowledge" / "internal-runbooks" / "ora-00257.md").write_text(
        "# ORA-00257\n\nORA-00257 means the archiver cannot archive a redo log because the fast "
        "recovery area is full.\n",
        encoding="utf-8",
    )
    (intake / "Knowledge" / "omf-guide.md").write_text(
        "# Oracle Managed Files\n\nOracle Managed Files use DB_CREATE_FILE_DEST to place datafiles.\n",
        encoding="utf-8",
    )
    (intake / "Policy" / "restricted.md").write_text(
        "# Restricted payroll policy\n\nOnly database administrators may read the payroll retention "
        "exception for production schemas.\n",
        encoding="utf-8",
    )
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'sentinel.db'}",
        synthetic_mode=True,
        scheduler_enabled=False,
        knowledge_enabled=False,
        ai_enabled=False,
        ai_provider="disabled",
        knowledge_intake_path=str(intake),
    )


def test_retrieval_meets_recall_and_citation_thresholds(settings, sessions) -> None:
    KnowledgeIndexer(sessions, settings).scan()
    cases = (
        ("What is the ARCHIVELOG standard for production databases?", "Policy/archivelog-standard.md"),
        ("ORA-00257 fast recovery area is full", "Knowledge/internal-runbooks/ora-00257.md"),
        ("DB_CREATE_FILE_DEST Oracle Managed Files", "Knowledge/omf-guide.md"),
    )
    recalls: list[float] = []
    rankings: list[float] = []
    operator = Caller("operator", "")
    with sessions() as session:
        for query, relevant in cases:
            passages = search(session, settings, query, operator, limit=5)
            ranked = [item.citation.relative_path for item in passages]
            recalls.append(recall_at_k(ranked, {relevant}, 5))
            rankings.append(ndcg_at_k(ranked, {relevant}, 5))
            assert ranked[0] == relevant
            chunk = session.get(DocumentChunk, UUID(passages[0].chunk_id))
            assert chunk is not None
            assert citation_grounded(passages[0].excerpt, chunk.content)
            assert passages[0].citation.heading
            assert passages[0].citation.version == 1
    assert min(recalls) >= 1
    assert min(rankings) >= 0.99


def test_acl_hides_unauthorized_documents_from_search(settings, sessions) -> None:
    from sentinel.knowledge.store import update_acl
    from sentinel.persistence.models import Document

    KnowledgeIndexer(sessions, settings).scan()
    with sessions.begin() as session:
        restricted = session.scalar(select(Document).where(Document.relative_path == "Policy/restricted.md"))
        assert restricted is not None
        update_acl(session, restricted, {"roles": ["dba"], "principals": ["payroll-owner"]})
    with sessions() as session:
        operator = search(session, settings, "payroll retention exception", Caller("operator", ""))
        auditor = search(session, settings, "payroll retention exception", Caller("auditor", ""))
        owner = search(session, settings, "payroll retention exception", Caller("operator", "payroll-owner"))
        dba = search(session, settings, "payroll retention exception", Caller("dba", ""))
    assert all(item.citation.relative_path != "Policy/restricted.md" for item in operator)
    assert all(item.citation.relative_path != "Policy/restricted.md" for item in auditor)
    assert owner[0].citation.relative_path == "Policy/restricted.md"
    assert dba[0].citation.relative_path == "Policy/restricted.md"
    assert dba[0].authoritative_policy is True


def test_rerank_bonus_and_postgres_sql_include_acl_and_hybrid_clauses() -> None:
    from sentinel.knowledge.retrieval import _rerank_bonus

    phrase = ChunkDraft("ARCHIVELOG", "the archivelog standard for production", 1, 1)
    heading = ChunkDraft("ARCHIVELOG", "other text", None, None)
    plain = ChunkDraft("", "other text", None, None)

    class _Chunk:
        def __init__(self, draft: ChunkDraft) -> None:
            self.heading = draft.heading
            self.content = draft.text

    assert _rerank_bonus(_Chunk(phrase), "archivelog standard") > _rerank_bonus(_Chunk(heading), "archivelog standard")
    assert _rerank_bonus(_Chunk(heading), "archivelog standard") > _rerank_bonus(_Chunk(plain), "archivelog standard")
    assert "<=>" in VECTOR_CANDIDATE_SQL
    assert "jsonb_build_array" in VECTOR_CANDIDATE_SQL
    assert "ingestion_status = 'indexed'" in VECTOR_CANDIDATE_SQL
    assert "plainto_tsquery" in KEYWORD_CANDIDATE_SQL
    assert "ts_rank" in KEYWORD_CANDIDATE_SQL
    assert PRIORITIZATION.startswith("Policy passages are labeled")
