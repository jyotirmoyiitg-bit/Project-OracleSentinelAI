# Phase 24 — Copilot Supervisor, Local Embeddings, and the Database Copilot Page

**Phase:** 24
**Status:** Implemented (24A supervisor, 24B local embeddings and one-time indexing, 24C answers from stored snapshots, 24D database copilot page)
**Next phase:** [Phase 25 — Database sections](Phase_25_Database_Sections.md)
**Prerequisites:** [Phase 23 — Graph history](Phase_23_Graph_History.md), [Phase 22 — Agents](Phase_22_Agents.md), [Phase 20 — Security, navigation, and copilot coverage](Phase_20_Security_Navigation_Copilot.md), [Phase 5 — Knowledge and RAG](Phase_5_Knowledge_RAG.md)

## 1. Overview

The database copilot did not behave as intended. Three causes were found.

- **Routing.** Fixed keyword patterns decided what a question was. A message that said "tablespace" was read as a health question even when it asked for an explanation, and a message with the word "grant" was read as a change request.
- **Manuals.** The passage vectors were a 128-number word hash, which matches shared words and not meaning. The Oracle manuals in `knowledge-intake` had also never been indexed on this installation: knowledge was off, and the page limit of 800 would have rejected most of them.
- **Policy.** A company-policy question accepted any document passage as evidence, so a manual passage could be reported as a company requirement.

Phase 24 changes four things.

- **24A, supervisor.** On a database chat one model call chooses which specialist answers: this database, Oracle knowledge, a SQL draft, company policy, a change request, or a general reply.
- **24B, local embeddings.** Passages are embedded by a model on this computer, served by Ollama. Indexing runs once from the launcher, tracks each file, and embeds only what changed.
- **24C, stored snapshots.** A database question is answered from a recent agent snapshot when there is one, and says so.
- **24D, page.** The database copilot page shows who answers and six sample questions as hints, in place of the shelf of shortcut buttons.

The rules are unchanged: the model is given no tool, a drafted statement is never run, no operator or model text reaches a statement, and privileged execution stays disabled.

## 2. The supervisor

`ai/supervisor.py` holds the routes, the prompt, and the parser. The supervisor does not answer the question.

| Route | Shown as | The answer may use |
|---|---|---|
| `database` | This database | Fixed read-only readings of the selected database, live or from a recent snapshot |
| `oracle_knowledge` | Oracle knowledge | The model's Oracle knowledge and passages from the manuals |
| `sql_help` | SQL draft | A read-only statement written by the model. It is not run |
| `policy` | Company policy | Passages from policy documents and stored compliance results only |
| `change` | Change request | An explanation or a runbook. Nothing is executed |
| `other` | General | A short general reply. No database fact and no company requirement |

The reply is JSON: at most two routes, at most three reading names, and whether the operator asked for a cause. The server keeps a route only when it is in the list and a reading only when it is in the allowlist and the caller's role may read it. Anything else is dropped.

Fixed rules stay in front of and behind the model.

- A pasted statement is refused before the supervisor is asked.
- A catalog question is answered as before and the supervisor is not asked.
- An explicit change phrase is a change request whatever the model chose.
- A question the fixed patterns recognise as being about this database keeps its reading. When the supervisor chose Oracle knowledge alone for a question that names something the patterns can read, and the wording does not ask for an explanation, the reading is added and both specialists answer.
- With the model off, with `ai_supervisor_enabled` off, or when the reply is not usable, the fixed patterns decide as before.

The fleet copilot is not changed.

Two smaller faults were fixed with it. A question that opens like a question and has no statement terminator is no longer refused as pasted SQL only because it names a command ("Explain what an object grant is"). The routing step is not shown to the model as evidence.

## 3. Company policy

A policy question now searches policy documents only. Evidence is a passage marked as policy or a stored compliance result. With neither, the answer is that the requirement could not be established. The model is told to name the policy document and not to present Oracle guidance as a company requirement.

## 4. Local embeddings

| Item | Value |
|---|---|
| Provider | `embedding_provider = ollama`. Word hashing (`local`) stays the default for a new installation and for tests |
| Model | `embedding_ollama_model`, `nomic-embed-text` by default (about 270 MB) |
| Where it runs | `embedding_local_url`, which must be on this computer. It is fixed for the deployment |
| Vector length | 768 for every provider. Migration `0012` widens the column, keeps the approximate index, clears the old vectors, and marks every document for embedding again |
| Batch | `embedding_batch_size`, 32 by default |

A model that returns another length is refused with a message that names the length. When the embedding model does not answer, search falls back to keyword search.

### What is embedded and when

| File state | Action |
|---|---|
| Same content, same embedding model | Skipped |
| Same content, different embedding model | The stored passages are embedded again. The file is not parsed again and its version stays |
| Changed content | Parsed, chunked, and embedded as a new version |
| Removed from the folder | Marked removed, and its passages leave the search |

Each file is committed on its own. A run that stops continues with the next unfinished file. Search compares only passages embedded with the current model, so a half-finished run cannot mix vectors.

### Search

- With an embedding model the heading-word bonus is removed. It pushed contents and index pages above the passage that answers the question.
- A message code such as ORA-00257 is looked up by the code itself. The entry that defines it comes first and its excerpt starts at the code.
- The NUL character is removed from extracted text. One manual carried it and could not be stored.
- The page, size, and parse limits were raised for new installations (10,000 pages, 64 MB, 300 seconds). The launcher raises a lower stored value.

## 5. Answers from stored snapshots

When a database reading is needed, the newest agent snapshot of that reading is used if it is no older than `ai_snapshot_max_age_seconds` (900 by default, 0 turns this off). The answer says "From the snapshot stored 5 minutes ago. Ask for the value now to read the database again." A live read is used when the question asks for the value now, when no snapshot is recent, when the snapshot did not succeed, for a catalog question, and for a restricted reading asked by a non-administrator.

## 6. The database copilot page

- The shelf of shortcut buttons is gone from a database chat. The API still returns the list, and typing any of those questions still works.
- The opening panel names the four specialists and what each may use, shows six sample questions, and links to GraphDB, WaitDB, and the Overview for the pictures.
- A sample question is a hint. Choosing one puts it in the box, where it can be edited before it is sent. After the first answer the samples wait behind a toggle.
- Each answer shows which specialist answered, with a note when a reading came from a stored snapshot.
- The fleet and incident chats keep their question shelf.

## 7. Settings

| Setting | Default | Meaning |
|---|---|---|
| `ai_supervisor_enabled` | on | Let the model choose the specialist on a database chat |
| `ai_snapshot_max_age_seconds` | 900 | Oldest agent snapshot used for a database answer. 0 always reads live |
| `embedding_provider` | local | `local`, `ollama`, or an approved `remote`. Now editable in Admin |
| `embedding_ollama_model` | nomic-embed-text | Embedding model served by Ollama |
| `embedding_batch_size` | 32 | Passages sent to the embedding model at once |

## 8. API

- The answer object gains `routes`, `routed_by` (`supervisor`, `patterns`, or `catalog`), and `source_note`.
- The tool list of an answer may hold `route_question` with the supervisor's choice. It is stored in `tool_executions` like every other step.
- `POST /api/v1/knowledge/reindex` also returns `reembedded`.
- The `search_knowledge` tool accepts a category, which the policy route sets to `Policy`.

## 9. Acceptance

- [x] Only listed routes and readings the caller may read are kept. An unusable reply leaves the fixed patterns in charge.
- [x] An explanation is not answered with a database reading, a SQL request reads nothing, and a greeting gets a general reply.
- [x] An explicit change is a change whatever the supervisor says. A pasted statement is refused before the supervisor.
- [x] A known database question keeps its reading when the supervisor misses it, and an explanation is never turned into a reading.
- [x] A manual passage is not company policy. A policy question searches policy documents only.
- [x] A recent snapshot answers without reading Oracle, and a live read is used when asked for or when the snapshot cannot be used.
- [x] The local model is asked in batches with its task prefix, and its failures are plain messages.
- [x] A second run skips everything. A new model embeds stored passages again without parsing. Only a changed file is indexed again. An interrupted run continues.
- [x] Search compares only passages of the current model and falls back to words when the embedding model is down.
- [x] A message code finds the entry that defines it.
- [x] With `qwen2.5-coder:14b`, the supervisor routed 51 of 53 labelled questions as expected in about 0.8 seconds each. The fixed patterns alone routed 35 of 53.
- [x] The 12 Oracle manuals and the policy document were indexed with `nomic-embed-text` into PostgreSQL: 63,302 passages in about 15 minutes. A second run took 0.1 seconds.
- [ ] The full live check is recorded only after `Verify_Phase24_Real.ps1` is run against Oracle. A written spec is not a pass.

## 10. Tests

- `backend/tests/ai/test_phase24_supervisor.py`: the parser and prompt; each route; role rules; the fixed rules; unusable replies; when the supervisor is not asked; pasted statements; stored snapshots; policy evidence; the routing step; settings.
- `backend/tests/knowledge/test_phase24_embeddings.py`: the Ollama provider and its failures; settings; skipping, re-embedding, changed and removed files, an interrupted run; search by current model; keyword fallback; ranking; message codes; NUL text; the migration.
- `frontend/src/CopilotPage.test.tsx`: the opening panel, sample questions as hints, the specialist badges, the snapshot note, and the toggle.
- Live check: `test_real_phase24\Verify_Phase24_Real.ps1`. `-CheckPrerequisites` does not connect to Oracle. `-ReindexOnly` only indexes. The full run is a pass only after it is actually run.

## 11. Out of scope

The fleet copilot (unchanged); the Fleet analyst and the Advisor; a reranking model; better heading detection in the PDF chunker, which still labels many passages "Page N"; an embedding model with a vector length other than 768; a per-database setting for the supervisor; streaming the answer text as it is written.
