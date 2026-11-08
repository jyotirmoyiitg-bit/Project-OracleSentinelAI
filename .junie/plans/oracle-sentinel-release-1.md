---
sessionId: session-261108-133019-1kab
---

# Oracle Sentinel AI — Phase-by-Phase Documentation Split

### Overview & Goals

The user has requested that the monolithic `Oracle_Sentinel_AI_Implementation_Plan.md` be broken down into **separate Markdown files, one for each phase** (Phase 0 through Phase 8). Each file should be self-contained, buildable, and testable, so that a coding agent can work on a single phase in isolation.

### Approach

I will create the following Markdown files in the project root:

| File | Phase | Focus |
|---|---|---|
| `Phase_0_Bootstrap.md` | Phase 0 | Monorepo scaffold, Python 3.14 env, React app, Docker Compose, CI, health endpoints |
| `Phase_1_Oracle_Connection.md` | Phase 1 | TNS registry, GSYSTEM scripts, read-only diagnostic probes, Oracle adapter |
| `Phase_2_Central_Collection.md` | Phase 2 | Scheduler, storage, fleet dashboard, polling jobs, checkpoints |
| `Phase_3_AlertLog_Rules_Incidents.md` | Phase 3 | Alert-log ingestion, rules engine, incident lifecycle, SMTP notifications |
| `Phase_4_Compliance_Forecasting.md` | Phase 4 | Compliance policies, capacity forecasting, proactive alerts |
| `Phase_5_Knowledge_RAG.md` | Phase 5 | Knowledge/Policy folder ingestion, PDF/DOCX parsing, chunking, embeddings, RAG retrieval |
| `Phase_6_AI_Copilot.md` | Phase 6 | LLM gateway, named read-only tools, AI orchestrator, chat interface with citations |
| `Phase_7_Operations_Auth_Audit.md` | Phase 7 | Operation proposals (no auto-DDL), OIDC/RBAC, audit trails, production hardening |
| `Phase_8_Privileged_Operations.md` | Phase 8 | Privileged operations interface placeholder (disabled by default) |

Each phase file will include:
1. **Phase Overview** — purpose and goals
2. **Prerequisites** — which prior phases must be complete
3. **Deliverables** — what to build (backend, frontend, infrastructure)
4. **Acceptance Criteria** — how to verify the phase is done
5. **Tests** — unit, integration, and security tests for this phase
6. **Dependencies** — Python/npm packages required
7. **Key Files** — files to create or modify
8. **Architecture Context** — a brief excerpt of the shared architecture (principles, tech stack, diagram) so each file is self-contained

The original `Oracle_Sentinel_AI_Implementation_Plan.md` will remain as the master specification, with cross-references to the phase files.

### Key Decisions

1. **Shared context in each file** — Each phase file will include a condensed version of the project's non-negotiable principles, tech stack table, and architecture diagram so that a coding agent can work from any phase file in isolation.
2. **No duplication of SQL scripts** — The GSYSTEM provisioning SQL and TNS examples remain in their original files; phase files reference them.
3. **Phase files are standalone** — A coding agent can pick up any phase file and understand what to build without reading the master plan.
4. **Original plan preserved** — The master `Oracle_Sentinel_AI_Implementation_Plan.md` is not modified; it serves as the source of truth with links to phase files.

### Proposed Changes

#### New Files to Create
- `Phase_0_Bootstrap.md` — Bootstrap phase documentation
- `Phase_1_Oracle_Connection.md` — Oracle connection phase documentation
- `Phase_2_Central_Collection.md` — Central collection phase documentation
- `Phase_3_AlertLog_Rules_Incidents.md` — Alert-log, rules, and incidents phase documentation
- `Phase_4_Compliance_Forecasting.md` — Compliance and forecasting phase documentation
- `Phase_5_Knowledge_RAG.md` — Knowledge ingestion and RAG phase documentation
- `Phase_6_AI_Copilot.md` — AI copilot phase documentation
- `Phase_7_Operations_Auth_Audit.md` — Operations, auth, and audit phase documentation
- `Phase_8_Privileged_Operations.md` — Privileged operations phase documentation

#### Modified Files
- `Oracle_Sentinel_AI_Implementation_Plan.md` — Add a table of contents at the top linking to each phase file (no other changes to the original content)

### File Structure (After Changes)

```
oracle-sentinel/
├── Oracle_Sentinel_AI_Implementation_Plan.md  (master spec, updated with phase file links)
├── Phase_0_Bootstrap.md
├── Phase_1_Oracle_Connection.md
├── Phase_2_Central_Collection.md
├── Phase_3_AlertLog_Rules_Incidents.md
├── Phase_4_Compliance_Forecasting.md
├── Phase_5_Knowledge_RAG.md
├── Phase_6_AI_Copilot.md
├── Phase_7_Operations_Auth_Audit.md
├── Phase_8_Privileged_Operations.md
├── Oracle_Sentinel_GSYSTEM_ReadOnly_Provisioning.sql
├── Oracle_Sentinel_Optional_Privileged_Operations.sql
├── Oracle_Sentinel_tnsnames_example.ora
└── main.py
```

### Risks
- **Information duplication** — Shared context (principles, tech stack, architecture) will be repeated in each phase file. This is intentional for standalone readability but increases maintenance burden if the master plan changes.
- **Inconsistency** — If the master plan is updated later, phase files may drift. Mitigation: phase files reference the master plan as the source of truth.
- **Large files** — Some phases (e.g., Phase 6) have extensive content. Mitigation: keep shared context concise in phase files; refer to the master plan for full details.

# Delivery Steps

### ✓ Step 1: Create Phase 0-3 Markdown files
Create the first four phase documentation files (Phase 0 through Phase 3) by extracting and organizing the relevant content from the master implementation plan.

- Create `Phase_0_Bootstrap.md` with:
  - Phase overview, prerequisites (none), deliverables, acceptance criteria
  - Backend: monorepo scaffold, Python 3.14 env, FastAPI app, pyproject.toml, package structure
  - Frontend: Vite + React + TypeScript app, basic layout
  - Infrastructure: Docker Compose (PostgreSQL + pgvector, Redis), .env.example
  - CI: format/lint/test pipeline
  - Tests: health endpoints, build/typecheck verification
  - Dependencies: FastAPI, Pydantic v2, python-oracledb, SQLAlchemy, Alembic, APScheduler, pytest
  - Key files to create: `backend/pyproject.toml`, `backend/src/sentinel/main.py`, `backend/src/sentinel/config/`, `frontend/package.json`, `docker-compose.yml`, `.env.example`
  - Shared context: principles, tech stack, architecture diagram

- Create `Phase_1_Oracle_Connection.md` with:
  - Phase overview, prerequisites (Phase 0), deliverables, acceptance criteria
  - Backend: TNS-based registry, GSYSTEM DBA scripts, secret integration, connection test, Oracle adapter, fixed SQL probes, capability checks
  - Frontend: database registry page (list, register, test connection)
  - Persistence: SQLAlchemy models for `database_targets`, `observations`, `diagnostic_events`, Alembic migrations
  - API: database registry endpoints (register, list, test connection)
  - Tests: Oracle adapter with mock, connection test, capability detection
  - Dependencies: python-oracledb
  - Key files: `backend/src/sentinel/adapters/oracle/`, `backend/src/sentinel/domain/`, `backend/src/sentinel/persistence/`, `scripts/oracle/`
  - Shared context: principles, tech stack, architecture diagram

- Create `Phase_2_Central_Collection.md` with:
  - Phase overview, prerequisites (Phase 1), deliverables, acceptance criteria
  - Backend: APScheduler-based scheduler, polling jobs, checkpoints, normalization, fleet and target detail endpoints
  - Frontend: Fleet Dashboard with database cards/table, Database Detail page with version, role/PDB, tablespaces, FRA, archive state, timeline charts
  - Persistence: models for `poll_runs`, `collector_checkpoints`, observations storage
  - API: metrics endpoints, time-range chart series
  - Tests: scheduler jobs with test doubles, stale telemetry handling, disconnected targets
  - Dependencies: APScheduler
  - Key files: `backend/src/sentinel/collectors/`, `backend/src/sentinel/api/v1/`
  - Shared context: principles, tech stack, architecture diagram

- Create `Phase_3_AlertLog_Rules_Incidents.md` with:
  - Phase overview, prerequisites (Phase 2), deliverables, acceptance criteria
  - Backend: `V$DIAG_ALERT_EXT` collector, source dedup, rules engine with YAML definitions, incident state machine, SMTP notifier
  - Frontend: Incident Center, Logs/Events page with ORA-code filters
  - Persistence: models for `rule_definitions`, `incidents`, `incident_events`, `notifications`
  - API: incidents, events, notifications endpoints
  - Tests: synthetic ORA-00257 scenario, deduplication, notification retry, no-LLM resilience
  - Dependencies: none new
  - Key files: `backend/src/sentinel/rules/`, `backend/src/sentinel/incidents/`, `backend/src/sentinel/notifications/`
  - Shared context: principles, tech stack, architecture diagram

### ✓ Step 2: Create Phase 4-8 Markdown files
Create the remaining five phase documentation files (Phase 4 through Phase 8) by extracting and organizing the relevant content from the master implementation plan.

- Create `Phase_4_Compliance_Forecasting.md` with:
  - Phase overview, prerequisites (Phase 3), deliverables, acceptance criteria
  - Backend: versioned OMF/ARCHIVELOG/FRA/Flashback policies, pass/fail/unknown, trend-based capacity forecast
  - Frontend: Compliance page, forecast charts on Database Detail
  - Persistence: models for `policy_definitions`, `policy_results`
  - API: compliance results, forecast data endpoints
  - Tests: policy overrides, insufficient history yields `unknown`, predicted exhaustion warning
  - Key files: `backend/src/sentinel/rules/` (compliance), `backend/src/sentinel/forecasting/`
  - Shared context: principles, tech stack, architecture diagram

- Create `Phase_5_Knowledge_RAG.md` with:
  - Phase overview, prerequisites (Phase 4), deliverables, acceptance criteria
  - Backend: folder watcher, PDF/DOCX parsers (PyMuPDF, python-docx), chunking, local embedding adapter, pgvector storage, PostgreSQL full-text search, hybrid retrieval
  - Frontend: Knowledge page with approved docs, search with citations
  - Persistence: models for `documents`, `document_chunks`
  - API: knowledge documents, search, reindex endpoints
  - Tests: new/changed/deleted docs, unauthorized docs never appear, corrupted docs quarantine, no-LLM search
  - Dependencies: PyMuPDF, python-docx, pgvector
  - Key files: `backend/src/sentinel/knowledge/`, `knowledge-intake/`
  - Shared context: principles, tech stack, architecture diagram

- Create `Phase_6_AI_Copilot.md` with:
  - Phase overview, prerequisites (Phase 5), deliverables, acceptance criteria
  - Backend: provider gateway (local/hosted/disabled), named read-only tools, AI orchestrator, safety policy, deterministic workflow
  - Frontend: DBA Copilot Chat page with source citations, tool execution transcript, no-LLM fallback
  - Persistence: models for `chat_threads`, `chat_messages`, `tool_executions`
  - API: chat threads, messages with SSE streaming
  - Tests: local and hosted provider, injection tests, no-LLM fallback, citation verification
  - Dependencies: Ollama client (optional)
  - Key files: `backend/src/sentinel/ai/`
  - Shared context: principles, tech stack, architecture diagram

- Create `Phase_7_Operations_Auth_Audit.md` with:
  - Phase overview, prerequisites (Phase 6), deliverables, acceptance criteria
  - Backend: operation proposals (proposal-only), OIDC/OAuth2 auth, RBAC roles, audit trails, observability
  - Frontend: Administration page, operation proposal UI, authentication flow
  - Persistence: models for `operation_proposals`, `audit_events`, `target_credentials`
  - API: operation proposals, admin AI provider config, audit events
  - Tests: proposal-only workflow, RBAC enforcement, audit trail immutability, load tests
  - Dependencies: OIDC library (e.g., authlib)
  - Key files: `backend/src/sentinel/operations/`, `backend/src/sentinel/auth/`, `backend/src/sentinel/observability/`
  - Shared context: principles, tech stack, architecture diagram

- Create `Phase_8_Privileged_Operations.md` with:
  - Phase overview, prerequisites (Phase 7), deliverables, acceptance criteria
  - Backend: privileged operations interface placeholder, disabled by default, approval workflow skeleton
  - Frontend: privileged operations UI placeholder (disabled)
  - Persistence: models for privileged operation state
  - API: privileged operations endpoints (disabled)
  - Tests: disabled-by-default verification, no execution possible
  - Key files: `backend/src/sentinel/operations/` (privileged sub-module)
  - Shared context: principles, tech stack, architecture diagram

### ✓ Step 3: Update master plan with cross-references
Update the master `Oracle_Sentinel_AI_Implementation_Plan.md` to add a table of contents at the top that links to each phase file, so users can navigate to the detailed phase documentation.

- Add a new section at the top of `Oracle_Sentinel_AI_Implementation_Plan.md` (after the document header) with a table linking to each phase file:
  ```markdown
  ## Phase Documentation

  | Phase | Documentation File |
  |---|---|
  | Phase 0 | [Phase_0_Bootstrap.md](Phase_0_Bootstrap.md) |
  | Phase 1 | [Phase_1_Oracle_Connection.md](Phase_1_Oracle_Connection.md) |
  | Phase 2 | [Phase_2_Central_Collection.md](Phase_2_Central_Collection.md) |
  | Phase 3 | [Phase_3_AlertLog_Rules_Incidents.md](Phase_3_AlertLog_Rules_Incidents.md) |
  | Phase 4 | [Phase_4_Compliance_Forecasting.md](Phase_4_Compliance_Forecasting.md) |
  | Phase 5 | [Phase_5_Knowledge_RAG.md](Phase_5_Knowledge_RAG.md) |
  | Phase 6 | [Phase_6_AI_Copilot.md](Phase_6_AI_Copilot.md) |
  | Phase 7 | [Phase_7_Operations_Auth_Audit.md](Phase_7_Operations_Auth_Audit.md) |
  | Phase 8 | [Phase_8_Privileged_Operations.md](Phase_8_Privileged_Operations.md) |
  ```
- No other changes to the original plan content — it remains the source of truth.
- Verify that all phase files are created and the links work.