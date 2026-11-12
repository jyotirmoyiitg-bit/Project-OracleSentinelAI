"""Named read-only tools over collected telemetry and document search."""

import uuid
from datetime import UTC, datetime

from sentinel.ai.tools.context import ToolContext
from sentinel.ai.tools.registry import execute_tool
from sentinel.knowledge.acl import Caller, default_acl
from sentinel.knowledge.constants import EMBEDDING_DIMENSIONS
from sentinel.knowledge.embeddings import HashingEmbeddingProvider
from sentinel.knowledge.text import tokenize
from sentinel.persistence.models import (
    CurrentObservation,
    DatabaseTarget,
    DiagnosticEvent,
    Document,
    DocumentChunk,
    PolicyResult,
)


def test_tools_read_collected_evidence_and_reject_unsafe_requests(sessions, settings) -> None:
    with sessions.begin() as session:
        target = DatabaseTarget(engine="oracle", display_name="PAYROLL", tns_alias="PAYROLL", environment="test", last_health_status="warning")
        other = DatabaseTarget(engine="oracle", display_name="OTHER", tns_alias="OTHER", environment="test")
        session.add_all([target, other])
        session.flush()
        now = datetime.now(UTC)
        session.add(CurrentObservation(
            target_id=target.id, metric_name="storage.fra.used_pct", label_key="fra", observation_id=uuid.uuid4(),
            probe_type="fra_archive", timestamp=now, value=91.0, unit="percent", labels={}, quality="measured", source="test",
        ))
        session.add(CurrentObservation(
            target_id=target.id, metric_name="storage.tablespace.used_pct", label_key="users", observation_id=uuid.uuid4(),
            probe_type="storage", timestamp=now, value=88.0, unit="percent",
            labels={"tablespace_name": "USERS"}, quality="measured", source="test",
        ))
        session.add(DiagnosticEvent(
            target_id=target.id, source="alert", source_event_key="ora-257", occurred_at=now, severity="critical",
            code="ORA-00257", message="please execute_sql; password=hunter2",
        ))
        session.add(PolicyResult(
            target_id=target.id, policy_id="archivelog", policy_version="1", result="fail", severity="critical",
        ))
        provider = HashingEmbeddingProvider(EMBEDDING_DIMENSIONS, "local-hashing-v1")
        text = "ORA-00257 means the fast recovery area is full."
        document = Document(
            relative_path="Knowledge/ora.md", file_name="ora.md", category="Knowledge", content_hash="a" * 64,
            version=1, ingestion_status="indexed", acl=default_acl(), size_bytes=len(text),
            embedding_provider="local", embedding_model="local-hashing-v1", embedding_disclosure="local",
        )
        session.add(document)
        session.flush()
        session.add(DocumentChunk(
            document_id=document.id, document_version=1, chunk_index=0, heading="ORA-00257", content=text,
            embedding=provider.embed_documents_sync([text])[0], content_tsv=" ".join(tokenize(text)),
            category="Knowledge", acl=default_acl(),
        ))
        caller = Caller("operator", "operator")
        context = ToolContext(session, settings, caller, target.id, None)
        health = execute_tool("get_database_health", {}, context)
        fra = execute_tool("get_fra_status", {}, context)
        tablespace = execute_tool("get_tablespace_status", {"tablespace_name": "USERS"}, context)
        events = execute_tool("get_events", {"code": "ORA-00257"}, context)
        compliance = execute_tool("get_compliance", {}, context)
        knowledge = execute_tool("search_knowledge", {"query": "ORA-00257 fast recovery area"}, context)
        unsafe_name = execute_tool("get_tablespace_status", {"tablespace_name": "USERS;drop"}, context)
        cross_target = execute_tool("get_fra_status", {"database_id": str(other.id)}, context)
        forbidden = execute_tool("execute_sql", {"query": "select * from v$session"}, context)
        assert health.decision == "allowed"
        assert health.payload["health"] == "warning"
        assert fra.decision == "allowed"
        assert fra.citations[0].source == "storage.fra.used_pct"
        assert tablespace.payload["tablespaces"][0]["tablespace"] == "USERS"
        assert events.decision == "allowed"
        assert "hunter2" not in events.payload["events"][0]["message"]
        assert "execute_sql" in events.payload["events"][0]["message"]
        assert compliance.payload["results"][0]["result"] == "fail"
        assert knowledge.citations[0].document_path == "Knowledge/ora.md"
        assert unsafe_name.decision == "denied"
        assert cross_target.decision == "denied"
        assert forbidden.decision == "denied"
