# Phase 22 — Agents: Snapshots, Collector, and Investigator

**Phase:** 22
**Status:** Implemented (22A snapshot store and Collector, 22B Supervisor, Investigator, and Verifier)
**Prerequisites:** [Phase 21 — Fleet copilot](Phase_21_Fleet_Copilot.md), [Phase 20 — Security, navigation, and copilot coverage](Phase_20_Security_Navigation_Copilot.md)
**Design:** [Phase_22_Agentic_Design.html](Phase_22_Agentic_Design.html)
**Next phase:** [Phase 23 — Graph history](Phase_23_Graph_History.md)

## 1. Overview

Phase 22 adds agents that work toward a goal over several steps and keep what they read. The Collector reads fixed readings from a registered database and stores the rows in PostgreSQL as a snapshot. The Investigator follows a playbook for one incident or one database: it collects, compares with the previous snapshot and stored metrics, looks up runbooks and policy, and saves a finding with its evidence. The Verifier checks a model-written conclusion against that evidence before an operator sees it.

Agents reach Oracle only through the existing fixed read-only readings. No operator text and no model text is placed in a statement. The model is given no tool to call. No agent changes a database. Agents ship turned off.

Migration `0011` adds five tables. The Fleet analyst and the Advisor are left for a later phase. Database copilot answers from stored snapshots arrived in Phase 24.

## 2. Snapshots and the Collector

| Pack | Readings |
|---|---|
| Core | availability, sessions, connected programs, blocking sessions, memory, tablespaces, datafile sizes, redo logs, running jobs, backups, recovery, alert log errors |
| Performance | performance, SQL workload, top SQL |
| Configuration and security | memory configuration, security, database users, control files, datafiles, largest segments, Data Guard |

- A collection stores one `evidence_snapshots` row per reading, grouped by a batch id, with the rows as JSON, a digest, and the status when a reading could not be read.
- A snapshot costs one readings-server process and one Oracle logon. The server gained a `collect_snapshot` tool that takes the database alias and a list of reading names from its own tool list. It refuses any other argument.
- No model is called during a collection.
- A collection is refused when agents are off for the database, the database is disabled, another collection of it is running, or the last one is more recent than the minimum gap.
- **Restricted readings.** Sessions, blocking sessions, SQL workload, top SQL, security, and database users hold SQL text or user names. They, the existing admin-only listings, and the whole configuration pack are stored with a restricted flag and are readable by an admin operator only. This is a fixed rule, not a setting. A database can stop storing the sensitive readings.
- **What changed.** Two snapshots of a reading are compared in plain code: new rows, missing rows, and values that moved by at least 10 percent, or 2 points for a percentage. Rotating values such as the current redo group are ignored.
- **Retention.** Snapshots older than the database's retention are deleted hourly. Runs and findings are kept with the figures they cite.

## 3. The Investigator

| Step | What happens | Model |
|---|---|---|
| Plan | A fixed rule matches the incident text to a playbook. Only when none matches may the model choose a playbook by name. An unknown name falls back to the general playbook. | Only when no rule matches |
| Collect | The playbook's readings are read from Oracle, or taken from a stored snapshot that is newer than the minimum gap. Readings the caller's role may not read are left out and named. | No |
| Compare | Each reading against its previous snapshot, the playbook's stored metrics now and about a day ago, and other open incidents on the database. | No |
| Look up | Runbook and policy passages from the knowledge search, and the compliance policies that fail on the database. | No |
| Conclude | The model receives the numbered evidence and returns JSON: cause, confidence, the evidence numbers it used, up to three next steps, and up to two more readings it needs. | Yes |
| Collect more | When the model asks, allowlisted readings the role may read are collected once and the model concludes again. | Yes |
| Verify | See section 4. | No |
| Finding | Saved with its evidence, confidence, who wrote it, and whether it passed the Verifier. | No |

- Playbooks: archive destination or recovery area full, tablespace full, blocking sessions, session or process limit, memory pressure, database unavailable, backup failure, scheduler job failure, and a general health check.
- Each step is committed on its own, so the portal shows a run while it works.
- **Budget.** `AGENT_MAX_STEPS` tool steps, `AGENT_MAX_MODEL_CALLS` model calls, and `AGENT_RUN_TIMEOUT_SECONDS`. One investigation per database at a time. A run that reaches the time limit is stopped and keeps its steps.
- **Role.** A run acts with the role of whoever started it. An operator's run never reads restricted readings. An administrator's finding that cites restricted evidence is marked restricted and is hidden from other roles, as are the restricted lines of its steps.
- **Model.** Evidence goes only to the local model. With the model off, or with a hosted provider configured, the finding lists the evidence and the playbook's next steps with no written cause.
- **Automatic runs.** When a database allows it, a timer starts one investigation for each new critical incident from the last 24 hours that has no run yet. These runs act as an operator, so their findings are open to every role.

## 4. The Verifier

A model-written conclusion passes only when all of these hold. A conclusion that fails is not shown; the finding shows the evidence and says why.

- Every ORA code in it is in the evidence or the incident text.
- Every figure of two or more digits is in the evidence, or is one rounded from it.
- It names no other registered database.
- It contains no statement or command.

## 5. Settings

Each of the first six has a system default in Admin, in the new Agents section, and can be set for one database. The database value wins. Changing a database value needs `config.write` and is audited.

| Setting | Default | Per database |
|---|---|---|
| `AGENTS_ENABLED` | off | Yes. The system switch is the master: a database can opt out, and cannot opt in while the system is off. |
| `AGENT_SNAPSHOT_PACKS` | `core,performance` | Yes |
| `AGENT_COLLECT_SENSITIVE` | on | Yes |
| `AGENT_SNAPSHOT_RETENTION_DAYS` | 14 (1 to 30) | Yes |
| `AGENT_SNAPSHOT_MIN_GAP_SECONDS` | 300 | Yes |
| `AGENT_AUTO_INVESTIGATE` | off | Yes |
| `AGENT_MAX_STEPS` | 6 | No |
| `AGENT_MAX_MODEL_CALLS` | 4 | No |
| `AGENT_RUN_TIMEOUT_SECONDS` | 180 | No |
| `AGENT_SWEEP_SECONDS` | 60 | No |

## 6. Permissions and audit

- New permission `agent.run` for operator, DBA, and administrator: collect a snapshot, start an investigation, accept or dismiss a finding. Every signed-in role reads runs, findings, and snapshot summaries.
- The configuration pack can be requested only by an admin operator.
- Audit actions: `snapshot.collect`, `agent.run`, `agent.finding.accept`, `agent.finding.dismiss`, `agent.settings`.

## 7. API

- `GET /api/v1/agents/status`
- `POST /api/v1/databases/{id}/snapshots`, `GET /api/v1/databases/{id}/snapshots`
- `POST /api/v1/agents/snapshots/fleet`
- `POST /api/v1/agents/investigations`, `GET /api/v1/agents/runs`, `GET /api/v1/agents/runs/{id}`
- `GET /api/v1/agents/findings`, `POST /api/v1/agents/findings/{id}/decision`
- `GET /api/v1/databases/{id}/agent-settings`, `PUT /api/v1/databases/{id}/agent-settings`

Details are in [docs/api-contracts.md](docs/api-contracts.md).

## 8. Portal

- **Agents** in the top bar: open findings with Accept and Dismiss, recent runs, the steps of a chosen run, and Collect fleet snapshot.
- **Database Overview:** a Snapshot panel with the last collection, Collect snapshot, what changed since the previous snapshot, and this database's agent settings; and an Investigation panel.
- **Incident page:** an Investigation panel with Investigate, the run's steps as they happen, and the finding.
- **Admin:** an Agents section in the configuration.
- The panels do not appear when the agent routes cannot be reached.

## 9. Data

| Table | One row is |
|---|---|
| `agent_runs` | One run of one agent |
| `agent_steps` | One step of a run |
| `evidence_snapshots` | One reading from one database at one time |
| `agent_findings` | One conclusion shown to an operator |
| `agent_database_settings` | One setting overridden for one database |

The live launchers for Phases 8, 9, and 11 to 21 checked for revision `0010` exactly. They now accept `0010` or later.

## 10. Acceptance

- [x] A snapshot stores one row per reading on one Oracle logon, flags restricted readings, and is refused when agents are off, the database is disabled, or the minimum gap has not passed.
- [x] A database that does not store sensitive readings never reads them.
- [x] What changed names the reading and the values, and restricted changes are hidden from non-administrators.
- [x] An investigation saves every step and a finding, with and without a model.
- [x] A conclusion with an invented figure, an unknown ORA code, another database, or a statement is rejected and only the evidence is shown.
- [x] The model may choose a playbook only when no rule matches, and may ask once for allowlisted readings.
- [x] A database value overrides the system default, an invalid value is refused, and the system switch is the master.
- [x] A new critical incident is investigated once when the database allows it.
- [x] With `qwen2.5-coder:14b`, three investigations on synthetic databases completed in about 12 seconds each with one model call; two causes passed the Verifier and one was rejected for a figure that was not in the evidence.
- [ ] The full live check is recorded only after `Verify_Phase22_Real.ps1` is run against Oracle. A written spec is not a pass.

## 11. Tests

- `backend/tests/agents/test_phase22_agents.py`: packs and restricted readings; database settings; the Collector and its refusals; retention; the readings server snapshot tool; comparison; the Verifier; playbook matching; the Investigator with and without a model, extra evidence, playbook choice, role limits, the model budget, the time limit; automatic investigation; and the API routes with their permissions and audit rows.
- `backend/tests/test_migrations.py`: revision `0011` and the five tables. `backend/tests/ai/test_phase20_routing.py`: the readings server lists the snapshot tool.
- `frontend/src/Phase22.test.tsx`: the Investigation panel, the Snapshot panel and database settings, the Agents page, and the top-bar link.
- Live check: `test_real_phase22\Verify_Phase22_Real.ps1`. `-CheckPrerequisites` does not connect to Oracle. The full run is a pass only after it is actually run.

## 12. Out of scope

The Fleet analyst and the daily fleet brief, the Advisor that drafts an operation proposal from an accepted finding, copilot and fleet answers from stored snapshots, a goal text box in the portal for a database investigation, trends drawn from snapshots, and any agent that changes a database.
