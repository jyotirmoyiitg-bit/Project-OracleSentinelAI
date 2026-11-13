# Oracle Sentinel AI — Architecture

## Overview

Oracle Sentinel AI is a central, browser-based platform for monitoring registered Oracle databases on Windows Server without installing a custom agent on each database server.

## Technology Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.14, FastAPI, Pydantic v2 |
| Oracle Driver | python-oracledb (thin mode) |
| Frontend | TypeScript + React, Vite |
| Application DB | PostgreSQL + pgvector |
| Scheduler | APScheduler (early) → Celery + Redis |
| Local LLM | Ollama (dev), vLLM (production) |
| Auth | OIDC/OAuth2 with enterprise IdP |
| Notifications | SMTP initially |

## Architecture Diagram

```mermaid
flowchart TB
    ORA[(Oracle Instance / PDB)]
    REG[Database Registry / TNS Alias Resolver]
    COL[Python Collection Workers]
    RULE[Rules + Compliance + Forecast Engine]
    INC[Incident + Notification Engine]
    APP[(PostgreSQL + pgvector)]
    DOC[Folder Ingestion: ora-19c-manual / Policy]
    RAG[Hybrid RAG Retrieval]
    TOOLS[Named Read-only Diagnostic Tools]
    AGENT[AI Orchestrator with Strict Tool Policy]
    MODEL[Configurable Model Gateway]
    API[FastAPI + Auth + RBAC]
    UI[React + TypeScript Web Console + Chat]

    REG --> COL
    COL <-->|read-only Oracle SQL| ORA
    COL --> APP
    APP --> RULE --> INC
    DOC --> RAG --> APP
    UI <-->|HTTPS| API
    API --> AGENT
    AGENT --> RAG
    AGENT --> TOOLS --> COL
    AGENT --> MODEL
```

## Knowledge ingestion

Approved files in `knowledge-intake/ora-19c-manual` and `knowledge-intake/Policy` are parsed, chunked, embedded locally, and stored in PostgreSQL. Manual files keep the Knowledge category. Search combines pgvector similarity with full-text ranking and returns a document, version, page, and section citation. Callers only receive passages their ACL allows. Collection, rules, incidents, and alerts do not call this pipeline and do not require a language model. Remote embeddings are used only when explicitly approved with a privacy disclosure.

## Copilot

The copilot answers from collected application data, approved documents, and a fixed set of read-only database readings. A deterministic workflow chooses the tools. The language model does not choose tools, and it never receives a SQL execution tool. Database questions about availability, errors, space, growth, performance, blocking, SQL, memory, backups, Data Guard, accounts, and jobs are sent to the local readings MCP server (`python -m sentinel.mcp`). That server accepts a TNS alias and runs one named reading; it does not accept SQL. The model only explains the rows. Fleet chat does not open that connection. Local Ollama and an approved hosted provider are separate clients. A local failure does not call the hosted provider. Hosted use requires explicit approval, a model name, a `secret://` key reference, and a privacy disclosure. `AI_HOSTED_VENDOR` selects the OpenAI chat API or the Anthropic messages API. An empty base URL uses the vendor's public endpoint. With AI disabled, the same chat still returns guided commands and cited evidence from metrics, incidents, compliance results, and document search. Every allowed or denied tool call is stored in `tool_executions`. In `APP_ENV=dev` or `test`, the local admin principal `sentinel` can choose the local model, Claude, or OpenAI for one answer. Each choice has its own model, base URL, key reference, privacy sentence, timeout, and output limit. Production and every other operator keep the configured provider. A local failure does not call a hosted model. The portal keeps the answer list in a scrolling pane, and New chat opens a fresh thread.

## Proposals and privileged operations

A tablespace extension can be stored and approved. The statement is a fixed template. `POST /api/v1/operations/proposals/{id}/execute` returns `Release 1 does not execute proposals.` Privileged operations are a disabled placeholder at revision `0009`. `GET /api/v1/operations/privileged` and `POST /api/v1/operations/privileged/{id}/execute` return HTTP 501 unless the caller lacks permission, in which case the response is HTTP 403. Each attempt is audited and `privileged_operations` is not written. `OPERATIONS_EXECUTION_ENABLED` defaults to false and cannot be turned on unless `OPERATIONS_EXECUTION_APPROVED` is also set. Both flags still do not send a statement to Oracle. The illustrated page is [docs/architecture.html](architecture.html).

## Non-Negotiable Principles

- **Monitoring never depends on an LLM** — collection, rules, incidents continue if AI is disabled
- **No custom agent on target servers** for baseline Oracle monitoring
- **Read-only by default** — GSYSTEM is a local monitoring user, not a superuser
- **Deny by default** — RBAC, per-database entitlements, named tool allowlists, bounded queries
- **Evidence over confidence** — link explanations to query results, event IDs, and document citations
- **Oracle licensing matters** — no AWR/ASH/ADDM unless explicitly licensed
- **Extensibility** — Oracle-specific SQL stays inside `adapters/oracle`
- **Privileged execution stays off** — proposals are stored, and Release 1 does not run them
