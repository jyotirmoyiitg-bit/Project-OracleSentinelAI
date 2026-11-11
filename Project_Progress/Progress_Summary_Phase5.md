# Phase 5 Implementation Summary — Knowledge Ingestion and RAG

**Status:** Implemented. Automated checks pass. Browser verification used a temporary SQLite database. Live PostgreSQL with pgvector was not executed in this session.<br>
**Phase specification:** [Phase 5 — Knowledge and RAG](../Phase_5_Knowledge_RAG.md)<br>
**Previous phase:** [Phase 4 — Compliance and Forecasting](../Phase_4_Compliance_Forecasting.md) · [Phase 4 handoff](./Progress_Summary_Phase4.md)<br>
**Next phase:** [Phase 6 — AI Copilot](../Phase_6_AI_Copilot.md)

This handoff records the Phase 5 implementation and verification state so another coding agent can continue without rediscovering the architecture. It supersedes the Phase 4 handoff's "What's Next" pickup point. The Phase 4 handoff's git-status and live-harness notes remain historical. Phase 5 has its own live harness at `test_real_phase5`; that harness has not been executed against Docker PostgreSQL and a real Oracle instance in this session.

## What Was Implemented

### Ingestion pipeline

- Added the knowledge package under [backend/src/sentinel/knowledge/](../backend/src/sentinel/knowledge/): discovery, parsers, chunking, embeddings, quarantine, storage, hybrid retrieval, evaluation, and a scheduler that is separate from Oracle collection.
- The scanner walks `knowledge-intake/Knowledge` and `knowledge-intake/Policy`. Allowlisted extensions are `.pdf`, `.docx`, `.txt`, and `.md`. Other files, including the existing HTML notes, are skipped and left in place.
- Change detection is SHA-256. An unchanged indexed file is not re-embedded. A changed or reappearing file increments the document version and replaces its chunks. A full scan marks indexed files that disappeared as deleted and removes their chunks. A path-limited reindex does not delete files that were outside the request.
- Parsers read PDF and DOCX from memory so Windows can still move a rejected file into quarantine. PyMuPDF is pinned to `>=1.24,<2`. Encrypted, corrupt, empty/scanned, low-quality, oversized, page-limit, and parser-timeout files are quarantined. OCR is disabled, so a scanned PDF is quarantined as `scanned_pdf`.
- Symlinks and paths that escape the intake root are rejected. An optional malware scanner command treats exit code 1 as malware; a missing scanner does not block ingestion. Quarantine events are appended as JSONL. Document text is stored as data and is never executed.
- Chunks are heading-aware and overlapping. The heading is prepended to the chunk text so a citation can name the section.

### Embeddings and retrieval

- The default provider is local signed feature hashing (`local-hashing-v1`, 128 dimensions, L2-normalized). It does not download a model and does not send document text off the machine. This is a deterministic stand-in, not a neural embedding model.
- A remote HTTP provider exists as a separate class. Settings reject `embedding_provider=remote` unless approval, a URL, and a privacy disclosure are all set. There is no silent fallback from remote to local or from local to remote.
- Hybrid search fuses vector similarity and keyword overlap with reciprocal rank fusion. PostgreSQL uses pgvector cosine distance and `tsvector`/`ts_rank`. SQLite stores the embedding as JSON and a token string, and ranks in Python. Policy passages are labeled authoritative and are not secretly boosted; the category filter is explicit.
- Access control is applied before results leave the store and again before passages are returned. The default grant is `operator`, `dba`, and `admin`. An empty grant denies access. The `admin` role can see and repair every document so an ACL cannot lock out repair. Other roles and named principals must be listed. `X-Sentinel-Role` defaults to `operator`. Reindex and ACL updates require `admin`. That header is a pre-authentication stand-in until Phase 7.

### API, schema, and portal

- Migration [0005_knowledge_rag.py](../backend/alembic/versions/0005_knowledge_rag.py) adds `documents` and `document_chunks`. `CREATE EXTENSION vector`, the HNSW cosine index, and the GIN index run only on PostgreSQL. Downgrade drops the indexes and tables and does not drop the extension.
- Endpoints, documented in [api-contracts.md](../docs/api-contracts.md):
  - `GET /api/v1/knowledge/documents`
  - `POST /api/v1/knowledge/search` (`llm_used` is always false)
  - `POST /api/v1/knowledge/reindex`
  - `POST /api/v1/knowledge/documents/{id}/acl`
- `KNOWLEDGE_ENABLED` only starts `KnowledgeScheduler`. Search and manual reindex work when it is false. A knowledge scheduler failure is logged and does not stop Oracle collection. A scan already in progress returns HTTP 409.
- The portal route `#/knowledge` lists document name, category, version, status, and index time, and shows search passages with document, version, page, and section citations. The page states that it does not call a language model. The reindex button sends `X-Sentinel-Role: admin`. Below 450px the shared top bar wraps so the new Knowledge link does not push the header actions off a phone-width screen.

## Verification

Run from the repository root in PowerShell:

```powershell
# Backend tests, including migration/autogenerate drift checks
Set-Location backend
..\.venv\Scripts\python.exe -m pytest tests -q

# Backend lint for Phase 5 code
Set-Location ..
..\.venv\Scripts\ruff.exe check backend\src\sentinel\knowledge backend\src\sentinel\api\v1\knowledge.py backend\src\sentinel\main.py backend\src\sentinel\config\settings.py backend\src\sentinel\persistence\models.py backend\src\sentinel\api\v1\schemas.py backend\alembic\versions\0005_knowledge_rag.py backend\tests\knowledge backend\tests\api\test_knowledge.py backend\tests\test_migrations.py

# Type check (the suppression is the pre-existing fleet-summary dict inference)
Set-Location backend
..\.venv\Scripts\python.exe -m mypy --disable-error-code var-annotated src\sentinel\knowledge src\sentinel\api\v1\knowledge.py src\sentinel\main.py src\sentinel\config\settings.py src\sentinel\persistence\models.py src\sentinel\api\v1\schemas.py

# Frontend build and tests
Set-Location ..\frontend
npm run build
npx vitest run
```

Latest results:

- Backend: **175 passed, 1 skipped**. The skip is the symlink rejection test; creating the link raised `OSError` on this Windows session, and the test treats that as an environment limitation.
- Ruff: passed for the Phase 5 files listed above.
- Mypy: passed for the listed modules with the targeted `var-annotated` suppression. Missing stubs for PyMuPDF, python-docx, pgvector, and APScheduler are ignored in `backend/pyproject.toml`.
- Frontend: TypeScript/Vite production build succeeded. Vite still reports the existing large-chunk advisory. Vitest: **24 passed** (23 existing tests plus the Knowledge page test).
- Browser, against a temporary SQLite file (`%TEMP%\sentinel-phase5.db`), with both schedulers off: `#/knowledge` loaded, an empty search showed "Enter a search query.", reindex reported 5 indexed documents, search for `ORA-00257` returned cited passages including `Knowledge/internal-runbooks/ora-00257.md`, the Policy filter reduced the table to the policy document, a citation click filled the document name, and Fleet overview still loaded with no databases. At 390px the page did not grow the document scroll width; the document table scrolls inside its own container. The temporary API and Vite processes were stopped afterward.
- **Not run:** `test_real_phase5\Verify_Phase5_Real.ps1` against Docker PostgreSQL and a real Oracle instance. It was parse-checked, and `real_runtime.py` was byte-compiled. Automated tests still cover the PostgreSQL SQL as text on SQLite. Live Oracle validation from earlier phases remains a separate step.

## Resume / Next-Agent Pickup

1. Apply the migration before using Phase 5 endpoints against the application database:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
   ```

   On PostgreSQL this migration creates the `vector` extension and the HNSW/GIN indexes. The compose image is already `pgvector/pgvector:pg16`.
2. Keep `EMBEDDING_PROVIDER=local` unless a reviewer explicitly approves a remote endpoint, URL, and privacy disclosure together. Do not add a fallback between the two providers.
3. `KNOWLEDGE_ENABLED=false` is the safe default. Operators can reindex from the portal or `POST /api/v1/knowledge/reindex` with `X-Sentinel-Role: admin`. Replace that header with the Phase 7 authenticated principal before production use.
4. Phase 6 should call `POST /api/v1/knowledge/search` and keep the returned citations. Do not re-parse the intake folders inside the copilot, and do not let a disabled language model block search.
5. Run `test_real_phase5\Verify_Phase5_Real.ps1` against Docker PostgreSQL and a real Oracle instance (`-CheckPrerequisites` first, then `-TnsAlias <alias> -InstallDependencies -Interactive`). It reindexes the intake folders, checks citations, quarantine, pgvector distance, full-text search, and ACL, and leaves the verification note and quarantined PDF in place. Phase 4 live verification (`test_real_phase4\Verify_Phase4_Real.ps1`) remains a separate unrun step.

## Known Limitations

- Local embeddings are hashed token features. They support hybrid retrieval and the evaluation fixture (Recall@5 = 1, nDCG at least 0.99 on three labeled documents), but they are not a semantic model. A related ORA section can rank above the exact runbook.
- pgvector and PostgreSQL full-text SQL were verified by inspection and string assertions. `test_real_phase5\Verify_Phase5_Real.ps1` is the live check and has not been executed in this session.
- The scan lock is process-local. Two API processes can scan at the same time.
- The portal reindex control is authorized only by the `X-Sentinel-Role: admin` header. There is no login yet.
- OCR is off. Scanned PDFs are quarantined.
- PyMuPDF 2.x is intentionally excluded because it pulled extra native packages on Python 3.14.
- The knowledge scan is not fenced with the collector's database lease.
- Changes are uncommitted. Preserve the existing working tree and commit only when requested.
