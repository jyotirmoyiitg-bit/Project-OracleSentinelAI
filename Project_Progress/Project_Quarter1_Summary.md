# Oracle Sentinel AI — Quarter 1 Summary (Phase 0 to Phase 25)

**Period covered:** 8 November 2026 (first commit) to 16 November 2026
**State:** Phases 0 to 25 are implemented. Schema revision is `0012`. The last commit is Phase 21; Phases 22 to 25 are in the working tree and not committed.
**Illustrated version:** [Project_Quarter1_Summary.html](./Project_Quarter1_Summary.html), with the architecture evolution, strengths and weaknesses, and proposed improvements.
**Architecture:** [docs/architecture_v3.html](../docs/architecture_v3.html) (components and switches), [docs/architecture_v2.html](../docs/architecture_v2.html) (data flow and implementation)

## 1. What exists now

Oracle Sentinel AI is a browser portal and one API process that monitor registered Oracle databases through one read-only account, `GSYSTEM`, with no agent on the database hosts. It collects metrics on a schedule, reads the alert log, opens incidents, checks compliance, and forecasts growth. It shows each database live in six views. A copilot answers questions from fixed read-only readings, the Oracle manuals, and company policy, and agents investigate an incident and save a finding with its evidence. No part of it runs SQL from an operator or from a model, and nothing changes a database.

| Measure | Value |
|---|---|
| Phases delivered | 0 to 25. There is no Phase 10 document; the move of settings into the database (revision `0010`) sits between 9 and 11 |
| Backend | About 24,100 lines of Python in 154 files |
| Portal | About 9,800 lines of TypeScript and React, 16 pages |
| Automated tests | Backend 546 passed, 1 skipped (52 test files). Portal 73 passed (14 test files) |
| Schema | 12 migrations, 30 tables |
| Fixed Oracle readings | 66 in the catalog, 28 of them offered to the copilot |
| Settings | 138, most editable in the portal |
| Live launchers | 23 PowerShell launchers, Phases 2 to 9 and 11 to 25 |

Test counts grew with the work: 285 backend and 27 portal at Phase 6, 349 and 37 at Phase 18, 478 and 58 at Phase 22, 546 and 73 at Phase 25.

## 2. Principles set in Phase 0

These were written before any code and are still in force.

1. Monitoring never depends on a language model. Collection, rules, incidents, and alerts work with the model off.
2. No custom agent on the Oracle servers.
3. Read-only by default. `GSYSTEM` has `CREATE SESSION` and `SELECT` on named views.
4. Deny by default: roles, named readings, bounded queries.
5. Evidence over confidence: an answer shows the rows or the cited passage it rests on.
6. No AWR, ASH, or ADDM, because they need a licence.
7. Oracle SQL lives only in the Oracle adapter.

## 3. Phase by phase

| Phase | Delivered | Key decision and the reason | Schema |
|---|---|---|---|
| 0 Bootstrap | Repository layout, FastAPI backend, React portal, Docker PostgreSQL, the seven principles | Fix the safety rules first, so that later features are built inside them | — |
| 1 Oracle connection | Registry by TNS alias, `GSYSTEM` grant scripts, connection test, Oracle adapter with fixed probes | python-oracledb in thin mode, so no Oracle client is installed. One common read-only account, so every database is reached the same way | `0001` |
| 2 Central collection | Scheduler, six probes, stored metrics, fleet and database pages, multi-database sidebar | APScheduler inside the API process with database leases, instead of Celery and Redis, to keep one process to run. A "current value" table so the dashboard never scans history | `0002` |
| 3 Alert log, rules, incidents | Alert log over SQL, rule engine, incident lifecycle, portal and email notifications | Read the alert log through `V$DIAG_ALERT_EXT`, so no file agent is needed on the host. Incidents carry a fingerprint so a repeat updates the same incident | `0003` |
| 4 Compliance, forecasting | Policy checks per database, growth forecast from retained samples | Forecast by regression on the application's own samples, because AWR is not allowed | `0004` |
| 5 Knowledge and RAG | Folder intake of manuals and policy, chunks, hybrid search with citations, access lists, quarantine | Store vectors in PostgreSQL with pgvector, not a separate vector database. Embeddings computed locally so document text does not leave the machine | `0005` |
| 6 AI copilot | Model gateway (local or approved hosted), chat, readings at ask time, sign-in with Active Directory or a local account | The model gets no tool. A short-lived readings process (MCP over stdin and stdout) runs one named reading and exits, so there is no SQL path and no extra port. Every allowed or denied tool call is stored | `0006`, `0007` |
| 7 Proposals, audit | Operation proposals with approval, roles, immutable audit trail | A proposal is stored and approved and never run in Release 1 | `0008` |
| 8 Privileged operations | A disabled placeholder that answers 501 | Keep execution off until a separate security review. The table is reserved and empty | `0009` |
| 9 Assistant policy | Which source may make which claim: model for general Oracle, readings for a database fact, policy documents for a company requirement | Write the rule down so later routing changes are judged against it | — |
| Configuration | Non-secret settings stored in PostgreSQL and edited in Admin; Oracle manuals placed on disk | An administrator should not edit `.env` and restart for an ordinary setting. The stored value wins | `0010` |
| 11 GraphDB shell | The page, one database, four empty panels, no Oracle read | Build the picture one panel a phase, each small enough to verify | — |
| 12 GraphDB memory | SGA, PGA, background processes | Fixed readings in the API process, one logon per picture | — |
| 13 GraphDB storage | Four-column layout, data-file and redo cylinders, control files | | — |
| 14 Service, host, processes | Service and host cards, server-process ovals, largest segments | | — |
| 15 SGA meters, recovery | Meters, Data Guard, recovery area, archive, latest backup | | — |
| 16 Rate meters, refresh | Four rate meters from two samples, refresh that keeps loaded cards | Rates computed in the browser from two samples and not stored, since AWR-style history was out | — |
| 17 GraphDB for every operator | Open to all roles; file, redo-member, and segment listings for administrators only; performance-home layout | Operators are the audience. The owner asked for the reference layout in the portal's own palette | — |
| 18 Structured answers | Answers as data end to end, Markdown, confidence, reading tables; fleet health rules | Stop re-parsing display text in the portal. A database that cannot be reached is not registered and is disabled after 7 days | — |
| 19 WaitDB | Event waits, blocking locks, lock activity, buffer busy waits | Same pattern as GraphDB: fixed readings, rates on the page | — |
| 20 Security, navigation, copilot coverage | Permission check and audit row on every changing route, revocable sessions, sign-in throttle, production guards, database workspace with tabs, model-assisted routing | The owner asked for a product review to be implemented as one phase. **Rule relaxed:** the model may nominate up to three allowlisted readings when the fixed router has no match | — |
| 21 Fleet copilot | Separate fleet and database copilots; fleet questions on incidents, programs, datafile sizes, tablespaces, policy | **Rule relaxed:** fleet chat may read every database live for two questions. Question text still never reaches SQL | — |
| 22 Agents | Collector, Investigator with playbooks, Verifier, settings per system and per database | The poll the owner proposed became a plain Collector with no model, and the reasoning works on stored snapshots. A Verifier in plain code rejects a cause the evidence does not support. Evidence goes to the local model only | `0011` |
| 23 Graph history | A quick second sample, and counter samples stored by the scheduler | Agent snapshots could not fill the graphs, because agents are off by default and run on request. The always-on scheduler stores the counters instead, with no migration | — |
| 24 Copilot supervisor | Supervisor routing, local embedding model, indexing that runs once and tracks each file, answers from snapshots, new database copilot page | Keyword routing was the main fault: 35 of 53 test questions routed correctly, against 51 with the supervisor. The manuals had never been indexed, and a policy answer could use a manual passage; both fixed | `0012` |
| 25 Database sections | MemoryDB, StorageDB, ParameterDB, AlertDB | Follow the owner's reference screens in the portal palette. No new grant. A run against the real database caught two faults that synthetic tests missed | — |

## 4. The decision trail

### Rules that held through all 25 phases

- No SQL from an operator or a model. Every statement is a fixed entry in the catalog.
- Read-only account. No table or view is created in Oracle.
- Privileged execution is off. Proposals are stored and not run.
- No AWR, ASH, or ADDM.
- A company requirement comes only from a policy document.
- Evidence to the local model only for agents.

### Rules that were relaxed, on purpose

| Earlier rule | Changed in | New rule | Why |
|---|---|---|---|
| The model never chooses a reading | Phase 20, then 24 | The model nominates names from an allowlist, and since Phase 24 a supervisor picks the specialist. The server checks every name | Keyword routing sent too many questions to the wrong path |
| Fleet chat does not read a database | Phase 21 | Two fleet questions read every enabled database live, a few at a time with a deadline | Operators needed "which database runs program X" without opening each one |
| GraphDB is for administrators | Phase 17 | Every role, with file listings for administrators | Operators are the people who watch the databases |
| Rates are never stored | Phase 23 | Counter samples are stored for 24 hours | Pages opened empty until the second refresh |
| Passage vectors are a local word hash | Phase 24 | A local embedding model, 768 numbers | Word hashing matched words, not meaning |

### Course corrections

- **The poll is not an agent (Phase 22).** The first idea was an agent that pulls database information into PostgreSQL. A fixed Collector with no model was recommended and accepted; the model reasons only over what was stored.
- **Agent snapshots are not graph history (Phase 23).** The question "does the agent data fill the graphs" exposed that it did not. A scheduler sampler was added instead.
- **No migration unless it earns its place.** Phases 11 to 21 and 23 and 25 added none, to keep older launchers working. Phases 22 and 24 added one each because the feature needed a table or a wider column.
- **Check against the real thing.** Real-model runs found a policy answer built on manual passages (Phase 24). A real-database run found a rejected statement and a wrong alert severity rule (Phase 25).

## 5. Verification status

| Layer | Status |
|---|---|
| Unit and integration tests | Backend 546 passed, 1 skipped. Portal 73 passed. Type-check and lint clean on changed files |
| Real local models | Phase 21 fleet answers, Phase 22 investigations, Phase 24 supervisor (51 of 53) and eleven end-to-end answers, with `qwen2.5-coder:14b` and `nomic-embed-text` |
| Real Oracle | Phase 25: the four new readings were run once on `PDBORCL`. Commit messages record the owner's testing through Phase 9 and at Phase 21 |
| Full live launchers | Written for 23 phases. The summaries record that most full runs were not performed in the sessions that wrote them. Phases 22 to 25 are not yet run end to end |
| Browser | Phases 23, 24, and 25 were opened against a running API with synthetic data |

## 6. Open risks

1. **Uncommitted work.** Phases 22 to 25 are 72 changed files and 42 new entries on top of the Phase 21 commit.
2. **Live proof.** The full launchers for Phases 22 to 25 have not been run against Oracle.
3. **Single process.** The scheduler and all timers live in the API process. There is no second node and no shared throttle.
4. **Manuals not indexed in the project database.** The one-time indexing step has been run only on a preview copy.
5. **Evaluation set.** The 53 routing questions were written by the assistant, not taken from operators.
6. **Defaults are off.** The scheduler, agents, language model, and knowledge scan are off on a fresh installation, so a new user sees little until they are turned on.

## 7. What would make it stand out

In order of value for the effort:

1. Close the proof gap: commit, run every launcher on a real database, and add one regression launcher and a CI run.
2. A daily fleet brief: what changed overnight on every database, from the snapshots and history already stored.
3. Drift detection: compare parameters, users, and files against yesterday and against a baseline, and say what changed.
4. An Advisor that turns an accepted finding into a draft proposal for a human to approve.
5. Longer trends with a time axis, and "unusual compared with last week".
6. Operator feedback on every answer, kept as the evaluation set.
7. Better manual citations: structure-aware chunking and a reranker.
8. Alerts to Teams, Slack, or a webhook, with maintenance windows.
9. A worker process for the timers and a second node.
10. A second database engine through the adapter contract that already exists.
11. A first-run setup page that shows every switch with its state and checks Ollama, the grants, and the index.
12. Guarded execution of a small set of fixed changes, only after a security review.

The HTML page explains each of these and the reasons.
