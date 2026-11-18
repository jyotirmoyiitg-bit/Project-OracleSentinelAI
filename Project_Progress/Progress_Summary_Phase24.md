# Phase 24 Implementation Summary — Copilot Supervisor, Local Embeddings, and the Database Copilot Page

**Status:** The database copilot is routed by a supervisor, the Oracle manuals are searched by meaning with a local embedding model, a database answer can come from a recent snapshot, and the page shows sample questions in place of the shortcut shelf. Automated checks pass. The supervisor, the embedding model, and eleven end-to-end answers were checked with the real local models on synthetic database readings, and the page was opened in a browser. The live launcher was added and `-CheckPrerequisites` passed. The full Oracle run was not performed, and the manuals are not yet indexed in the project database.<br>
**Phase specification:** [Phase 24 — Copilot supervisor](../Phase_24_Copilot_Supervisor.md)<br>
**Previous summary:** [Phase 23 — Graph history](./Progress_Summary_Phase23.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)<br>
**Architecture:** [docs/architecture_v2.html](../docs/architecture_v2.html)

Later work is in [Phase 25 — Database sections](./Progress_Summary_Phase25.md).

Migration head is now `0012`. No Oracle grant was added. No reading SQL changed. Privileged execution stays disabled.

## What was wrong

- The fixed keyword patterns sent questions to the wrong path. On 53 labelled questions they chose the expected specialist for 35.
- The manuals had never been indexed on this installation. `KNOWLEDGE_ENABLED` is false in `.env` and the page limit of 800 would have moved most manuals to Quarantine on the first scan.
- The passage vectors were a 128-number word hash.
- A policy question accepted a manual passage as company policy. This was found in the end-to-end run and fixed.
- A question that only named a command, such as "Explain what an object grant is", was refused as pasted SQL.

## What Was Implemented

### 24A Supervisor

- `backend/src/sentinel/ai/supervisor.py`: routes, prompt, schema, `parse_decision`, `route_labels`.
- `ai/orchestrator.py`: `_supervise`, `merge_decision`, the general route, the policy prompt, and `routes`, `routed_by`, `source_note` on the answer. The fixed patterns remain the fallback and two fixed rules sit behind the model.
- `ai/safety.py`: a question about a command is not a pasted statement.
- `ai/tools/knowledge.py`: the search tool takes a category. `_policy_evidence` needs a policy passage or a compliance result.

### 24B Local embeddings

- `knowledge/embeddings.py`: `OllamaEmbeddingProvider`, on this computer only, in batches, with a task prefix for known models.
- `knowledge/service.py`: `_state` (unchanged, re-embed, ingest), `_reembed`, a progress callback, and `reembedded` in the report.
- `knowledge/store.py`: `chunk_texts`, `reembed_document`. `knowledge/chunker.py`: NUL removed.
- `knowledge/retrieval.py`: only the current model's passages are compared; keyword fallback; the message-code list; ranking without the heading bonus for an embedding model.
- `alembic/versions/0012_embedding_dimensions.py`: vectors of 768, the approximate index kept, old vectors cleared, documents marked for embedding again, the stored `embedding_dimensions` updated.
- `config/settings.py`, `config/store.py`: new settings; `embedding_provider` and `embedding_ollama_model` editable in Admin; larger knowledge limits.

### 24C Stored snapshots

- `ai/stored.py`: `stored_reading`, `wants_live`, `age_text`.

### 24D Page

- `frontend/src/CopilotPage.tsx`: `DatabaseWelcome`, `SampleQuestions`, specialist badges, the snapshot note, and the hints toggle. `styles.css`: a Phase 24 block. `types.ts`: three optional answer fields.

## Checks with the real local models

| Check | Result |
|---|---|
| Supervisor, `qwen2.5-coder:14b`, 53 labelled questions | 51 routed as expected, 0.8 seconds on average. Fixed patterns alone: 35 |
| Index of 12 manuals and 1 policy file, `nomic-embed-text`, PostgreSQL preview database | 63,302 passages. 12 files in 827 seconds and the last manual in 85 seconds |
| Second index run | 13 unchanged, 0.1 seconds |
| Interrupted run | One manual failed on a NUL character. After the fix the next run indexed that manual only |
| Search on the full index | 270 to 480 milliseconds a question |
| Eleven end-to-end questions | 3 to 7 seconds an answer. Files, tablespaces, memory, errors, and the alert log each read the database; explanations cited the manuals; the SQL request read nothing |

Two of the 53 questions were still routed differently from the label: a request for "something I can run" was answered with the live blocking reading, and "Which users have the DBA role here?" was treated as Oracle knowledge.

The database readings in these checks were synthetic. No Oracle database was read.

## Verification

- `backend/tests/ai/test_phase24_supervisor.py` and `backend/tests/knowledge/test_phase24_embeddings.py`: **43 passed**. Full backend suite: **534 passed, 1 skipped**. Ruff is clean on the changed files; findings remain in 13 files this phase did not touch.
- Full frontend suite: **63 passed** in 13 files. `tsc -b` is clean. One existing copilot test was rewritten for the new page.
- Two older routing tests now pin the fixed-pattern path with the supervisor off, because the supervisor is asked first by design.
- The launcher's check step passed against a synthetic API with the model off (fixed patterns, 4 of 5 database topics by the expected reading) and with the local model on (supervisor, 5 of 5). `--require-model` failed as intended with the model off.
- In a browser against the running API with the real models: the opening panel, six samples, and three links were shown and the shelf was gone; a sample filled the box; the answer showed "This database", chosen by the supervisor, with the snapshot note and a six-row reading.
- `Verify_Phase24_Real.ps1` parsed and `-CheckPrerequisites` passed. The full launcher was not run.

## Live launcher

`test_real_phase24\Verify_Phase24_Real.ps1` starts PostgreSQL, upgrades to revision `0012`, pulls `nomic-embed-text` when it is missing, and indexes the knowledge folders once before the API starts. It then signs in, confirms the Oracle connection, and checks the index, a second run, a search by meaning, a pasted statement, a question about a command, an explanation, the five database topics, a snapshot answer, and the access rules. Every changed setting is put back.

```powershell
.\test_real_phase24\Verify_Phase24_Real.ps1 -CheckPrerequisites
.\test_real_phase24\Verify_Phase24_Real.ps1 -ReindexOnly
.\test_real_phase24\Verify_Phase24_Real.ps1 -TnsAlias PDBORCL -Interactive
.\test_real_phase24\Verify_Phase24_Real.ps1 -TnsAlias PDBORCL -RequireModel
```

`-ReindexOnly` is the one-time indexing on its own. Run it again after adding or replacing a manual.

## Resume / Next-Agent Pickup

1. Run `alembic upgrade head` or the launcher before starting the API. Revision `0012` clears the stored vectors, so search is by keyword until the indexing step has run.
2. The indexing step stores `embedding_provider = ollama` and raises the stored knowledge limits. Ollama must be running whenever a search is made; without it search is by keyword.
3. The supervisor needs the local language model stored in Admin. With the model off the copilot behaves as before this phase, apart from the page.
4. Answers from snapshots need agents on and a snapshot no older than 15 minutes. Agents are off by default.
5. A new route goes in `ai/supervisor.py` and needs a branch in `Orchestrator._answer` or `_route_claim`. Keep the two fixed rules in `merge_decision`.
6. Do not let a route, a reading name, or any model text reach a statement. A company requirement comes only from a policy document.
7. A different embedding model must return 768 numbers, or a migration is needed.
8. The PDF chunker still labels many passages "Page N". Better headings would improve citations.
9. Later phase, not built: Fleet analyst and daily brief, Advisor drafts from accepted findings, the same supervisor for the fleet copilot.
10. Do not record the full live check as passed until `Verify_Phase24_Real.ps1` is run against Oracle.
11. These edits are uncommitted. Commit only when requested.
