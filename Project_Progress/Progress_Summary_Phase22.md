# Phase 22 Implementation Summary — Agents: Snapshots, Collector, and Investigator

**Status:** Agents are implemented and ship turned off. The Collector stores snapshots of the fixed readings in PostgreSQL. The Investigator runs a playbook for an incident or a database and saves a finding with its evidence. A Verifier rejects a model-written conclusion that the evidence does not support. Settings have a system default and a database override.<br>
**Phase specification:** [Phase 22 — Agents](../Phase_22_Agents.md)<br>
**Design:** [Phase_22_Agentic_Design.html](../Phase_22_Agentic_Design.html)<br>
**Previous summary:** [Phase 21 — Fleet copilot](./Progress_Summary_Phase21.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Later work is in [Phase 23 — Graph history](./Progress_Summary_Phase23.md).

Migration head is now `0011`. No Oracle grant was added. Privileged execution stays disabled. No agent changes a database.

## What Was Implemented

### Schema and settings

- `backend/alembic/versions/0011_agents.py` and the models `AgentRun`, `AgentStep`, `EvidenceSnapshot`, `AgentFinding`, `AgentDatabaseSetting`.
- Ten settings in `config/settings.py`, listed in a new Agents section of the Admin configuration. `agents/config.py` resolves the value for one database and validates a database value with the same rules as the system setting.
- Permission `agent.run` for operator, DBA, and administrator.

### Collector

- `agents/packs.py`: three packs over the existing readings, and which readings are sensitive or restricted.
- `agents/collector.py`: `collect_snapshot`, `collect_fleet`, `delete_expired`. The database session is not held while Oracle is read.
- `mcp/server.py` gained the `collect_snapshot` tool, which runs a list of fixed readings on one logon through `shared_reader`. `mcp/client.py` gained `ask_readings`.
- `agents/compare.py`: what changed between two snapshots of a reading.

### Supervisor, Investigator, Verifier

- `agents/playbooks.py`: eight playbooks and a general one.
- `agents/runner.py`: `AgentRunner` starts and runs an investigation, keeps the budget, and commits each step. `record_collection` stores a Collector run.
- `agents/verifier.py`: the checks on a model-written conclusion.
- `agents/scheduler.py`: the timer for automatic investigations and for retention. It starts with the collection scheduler.

### API and portal

- `api/v1/agents.py`: status, snapshots, investigations, runs, findings, and database settings. Stored times are returned with a `Z`.
- `frontend/src/AgentsPage.tsx`, `components/AgentRunView.tsx`, `components/InvestigationPanel.tsx`, `components/SnapshotPanel.tsx`. The Agents link is in the top bar. The panels are on the database Overview and the incident page.
- `frontend/vite.config.ts` reads `SENTINEL_API` for the proxy target, so a preview can run beside a running portal. The default is unchanged.

### Older launchers

The launchers for Phases 8, 9, and 11 to 21 checked for revision `0010` exactly. All 13 now accept `0010` or later and print the revision they found.

## Check with the local model

`qwen2.5-coder:14b` through Ollama, three synthetic databases, temporary SQLite.

| Incident | Playbook | Result |
|---|---|---|
| ORA-00257 archiver error, recovery area 97.4 percent now and 71 a day ago | Archive destination, by rule | Cause written, passed the Verifier, 12 s, one model call |
| ORA-01653 unable to extend, USERS from 71 to 98 percent since the previous snapshot | Tablespace full, by rule | Cause written, passed the Verifier, 12 s, one model call |
| "Reports were slow this morning" | General, chosen by the model | Cause rejected: it used the figure 29, which is not in the evidence. The finding shows the evidence only. 12 s, two model calls |

The first prompt let the model ask for more evidence on every run, which doubled the time. The prompt now says to leave that empty when the evidence already shows a cause.

## Verification

- `backend/tests/agents/test_phase22_agents.py`: **50 passed**. Full backend suite: **478 passed, 1 skipped**. Ruff is clean on the changed files.
- `frontend/src/Phase22.test.tsx`: **6 passed**. Full frontend suite: **58 passed** in 12 files. `tsc -b` is clean.
- Migration `0011` was applied to the PostgreSQL preview database. Against the preview API on its own port, over HTTP: a snapshot stored 15 of 15 readings, an investigation ran in the background to a finding, the finding was dismissed, a fleet snapshot ran, an operator was refused a settings change, and the five audit actions were recorded.
- `Verify_Phase22_Real.ps1` parsed and `-CheckPrerequisites` passed. Its check step passed against a synthetic API with the model off and with `qwen2.5-coder:14b` required, and `--require-model` failed as intended with the model off. The full Oracle launcher was not run.
- The portal pages were not opened in a browser. They are covered by component tests. Signing the browser in to the preview needed a stored credential that was not available to read.

## Live launcher

`test_real_phase22\Verify_Phase22_Real.ps1` follows the Phase 21 launcher through the real Oracle connection and requires revision `0011`. When agents are off it turns them on for the run and off again afterwards. A temporary `phase22-operator` collects the core pack twice from the selected database. The launcher then checks the comparison, the restricted readings for both roles, a refused configuration pack, a refused settings change, a refused out-of-range retention, the minimum gap (429), and an opted-out database (409). It starts an investigation of the database with an archiver goal, waits for it, and checks the steps, the collection, the finding, and that nothing contains SQL. It dismisses the finding and checks the audit trail. The database's own settings and the system switch are put back and checked.

```powershell
.\test_real_phase22\Verify_Phase22_Real.ps1 -CheckPrerequisites
.\test_real_phase22\Verify_Phase22_Real.ps1 -TnsAlias PDBORCL -Interactive
.\test_real_phase22\Verify_Phase22_Real.ps1 -TnsAlias PDBORCL -RequireModel
```

`test_real_phase22\.env` is gitignored and holds no secret. The automatic investigation of a new critical incident is not exercised by the launcher. Snapshots, runs, and the finding from a run stay; the snapshots expire by retention.

## Resume / Next-Agent Pickup

1. Run `alembic upgrade head` (or any phase launcher) before starting the API. It refuses to start without the agent tables.
2. Agents are off. Turn them on in Admin, Agents. Automatic investigation is a separate setting and is also off.
3. The automatic sweep and retention run only when `SCHEDULER_ENABLED=true`.
4. The model writes a cause only when the local provider is selected. A hosted provider gives evidence-only findings.
5. Do not let a reading name, a goal, or any model text reach a statement. A new reading for agents goes in `agents/packs.py` and must already be in `TOOL_NAMES`.
6. A reading that holds user names or SQL text belongs in `SENSITIVE_KINDS`. Reading restricted rows stays admin-only.
7. A later phase: Fleet analyst and daily brief, Advisor drafts from accepted findings. Database copilot answers from stored snapshots arrived in Phase 24.
8. Do not record the full live check as passed until `Verify_Phase22_Real.ps1` is run against Oracle.
9. These edits are uncommitted. Commit only when requested.
