"""Compliance results and the Phase 5 document search. This module does not call a chat model."""

from sqlalchemy import select

from sentinel.ai.safety import sanitize_untrusted
from sentinel.ai.tools.context import ToolContext
from sentinel.ai.tools.health import _target
from sentinel.ai.types import EvidenceCitation, ToolOutcome
from sentinel.knowledge.retrieval import search
from sentinel.persistence.models import PolicyResult


def compliance_status(context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    del arguments
    target = _target(context)
    rows = context.session.scalars(
        select(PolicyResult).where(PolicyResult.target_id == target.id).limit(20),
    ).all()
    items = [
        {
            "policy_id": row.policy_id,
            "version": row.policy_version,
            "result": row.result,
            "severity": row.severity,
        }
        for row in rows
    ]
    summary = f"{len(items)} stored compliance results for {target.display_name}."
    citations = [
        EvidenceCitation(
            "compliance", item["policy_id"], f"{item['policy_id']}@{item['version']}",
            f"{item['result']} ({item['severity']})", str(target.id),
        )
        for item in items[:8]
    ]
    return ToolOutcome("get_compliance", "allowed", summary, citations, {"results": items})


def knowledge_search(context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    query = arguments.get("query")
    text = query.strip() if isinstance(query, str) else ""
    if not text:
        return ToolOutcome("search_knowledge", "allowed", "No document query was provided.", [], {"items": []})
    passages = search(context.session, context.settings, sanitize_untrusted(text, 300), context.caller, limit=5)
    items = [
        {
            "document": passage.citation.relative_path,
            "heading": passage.citation.heading,
            "version": passage.citation.version,
            "excerpt": sanitize_untrusted(passage.excerpt, 400),
            "authoritative_policy": passage.authoritative_policy,
        }
        for passage in passages
    ]
    summary = f"{len(items)} cited document passages."
    citations = [
        EvidenceCitation(
            "document", passage.citation.relative_path, passage.citation.relative_path,
            sanitize_untrusted(passage.excerpt, 400),
            document_path=passage.citation.relative_path, heading=passage.citation.heading,
        )
        for passage in passages
    ]
    return ToolOutcome("search_knowledge", "allowed", summary, citations, {"items": items})
