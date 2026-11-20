# Phase 29 — Agentic AI Evolution: Advisor, Fleet Analyst, and Deep Diagnostics

**Phase:** 29  
**Status:** Planned  
**Prerequisites:** [Phase 28 — SessionDB](Phase_28_SessionDB.md), [Phase 24 — Copilot supervisor](Phase_24_Copilot_Supervisor.md), [Phase 22 — Agents](Phase_22_Agents.md), [Phase 8 — Privileged operations](Phase_8_Privileged_Operations.md), [Phase 7 — Operations, auth, audit](Phase_7_Operations_Auth_Audit.md)  
**Design Reference:** [Phase_22_Agentic_Design.html](Phase_22_Agentic_Design.html)  

---

## 1. Overview

Phase 22 established the core agentic runtime: the **Collector** (storing multi-reading snapshots in PostgreSQL on one Oracle logon), the **Investigator** (executing deterministic incident playbooks), and the **Verifier** (rejecting ungrounded figures, foreign database names, and fabricated ORA errors). Phase 24 introduced the Copilot **Supervisor** to route database questions, while Phase 28 introduced **SessionDB**, giving Sentinel cached execution plans (`V$SQL_PLAN`) and session cursors (`V$SESSION`, `V$SQL`).

However, two planned specialist agents from the architecture roadmap remain unbuilt:
1. **The Advisor:** After an operator accepts an incident finding (e.g., tablespace near capacity, archiver stuck, undo exhaustion), the operator must still manually navigate to Operations and formulate a proposal. The finding is not automatically translated into a safe, pre-validated change draft.
2. **The Fleet Analyst:** Fleet monitoring remains reactive. Cross-database parameter drift, growth outliers, and systemic failures across instances are not synthesized into a daily operational briefing.

Additionally, the **Investigator** is currently limited to flat metric comparisons and cannot inspect root causes within execution plans (`V$SQL_PLAN`) or wait-event trees (`WaitDB`). Raw telemetry handed to local language models (`qwen2.5-coder:14b` via Ollama) can also overwhelm the local context window without deterministic pre-compression.

Phase 29 completes the tier-2 agentic roadmap:
- **29A: The Remediation Advisor.** Translates accepted findings into draft `OperationProposal` records ready for human sign-off.
- **29B: The Fleet Analyst & Automated Brief.** Compares snapshots across registered databases to identify drift, capacity cliffs, and anomalies, storing a daily structured briefing.
- **29C: Deep Telemetry in Investigations.** Connects `SessionDB` cached plans and `WaitDB` enqueue chains into diagnostic playbooks.
- **29D: Local Model Telemetry Pre-Compression.** Implements Python-side tabular summaries and constrained grammar decoding to maximize small local model fidelity.

The core boundary remains untouched: **Agents operate strictly read-only against Oracle through `GSYSTEM`. The language model is never given execution tools, and no change proposal is executed without explicit DBA approval.**

---

## 2. The Remediation Advisor Agent

The Advisor bridges incident investigation and privileged operation drafting. It acts when an operator clicks **"Draft proposal"** on an accepted finding, or when an investigation concludes with high confidence on a known remediation pattern.

```
┌─────────────────┐       ┌──────────────────────┐       ┌────────────────────────┐
│ Verified Finding│ ───>  │   Advisor Agent      │ ───>  │  OperationProposal     │
│ (e.g. USERS 94%)│       │ (Playbook + Policy)  │       │ (status="pending")     │
└─────────────────┘       └──────────────────────┘       └───────────┬────────────┘
                                                                     │
                                                         [DBA Signs Off in Portal]
                                                                     │
                                                                     ▼
                                                         [Manual/External Run]
```

### 2.1 Workflow and Bounds

| Step | Action | Uses Model |
|---|---|---|
| **1. Trigger** | Operator clicks "Draft proposal" on an accepted finding in `AgentFinding`. | No |
| **2. Pattern Match** | Maps finding cause and affected objects to supported proposal actions (`tablespace.add_omf_datafile`, `tablespace.resize_datafile`). | No |
| **3. Policy Evaluation** | Queries `knowledge` for storage naming conventions and maximum file size limits from company policy documents. | No |
| **4. Parameter Synthesis** | Determines the target tablespace, recommended extension size (MB), and storage availability. | No |
| **5. Justification & Risk** | The local model drafts the operational impact statement and risk summary citing the finding's evidence numbers. | Yes (1 model call) |
| **6. Proposal Validation** | `sentinel.operations.validation.preflight` executes static syntax, role, and quota verification on the proposed SQL. | No |
| **7. Store Proposal** | Inserts a pending record in `operation_proposals` linked to `AgentFinding.id`. | No |

### 2.2 Supported Automated Drafts (Release 1 Constraints)
Under Release 1 rules, the Advisor only drafts **pre-approved parameterized actions**:
1. `tablespace.add_omf_datafile`: Adding an Oracle Managed File (OMF) datafile to an autoextending tablespace facing exhaustion.
2. `tablespace.resize_datafile`: Incrementally expanding a non-OMF datafile where filesystem space allows.
3. `session.disconnect_guidance`: For deadlock/blocking findings, drafts an advisory action listing the blocker SID, serial number, and SQL_ID (for manual DBA review).

### 2.3 Strict Safety Invariants
- The Advisor **never executes SQL** and **never changes database state**.
- The output is written to PostgreSQL table `operation_proposals` with `status = "pending"`.
- The existing human-in-the-loop workflow is enforced: a DBA must review, approve, or reject the proposal.
- If the model is disabled or fails to generate valid JSON, the Advisor uses a deterministic fallback template derived from the rule definition.

---

## 3. The Fleet Analyst & Daily Briefing

The Fleet Analyst runs on a scheduled background timer (e.g., daily at 06:00 UTC) or on-demand from the Fleet Workspace. It analyzes stored snapshots across all registered targets to detect fleet-wide risks.

### 3.1 Analysis Dimensions

```
   ┌──────────────────────────────────────────────────────────────┐
   │                    Fleet Snapshot Store                      │
   └───────────────┬──────────────────────────────┬───────────────┘
                   │                              │
         [Configuration Drift]          [Capacity Forecasting]
                   │                              │
                   ▼                              ▼
          Parameter variances             Days-to-exhaustion
          across DEV/UAT/PRD              outliers (< 14 days)
                   │                              │
                   └──────────────┬───────────────┘
                                  ▼
                   ┌──────────────────────────────┐
                   │     Fleet Analyst Engine     │
                   │  - Deterministic Anomaly Agg │
                   │  - 1 Local Model Brief Call  │
                   └──────────────┬───────────────┘
                                  ▼
                   ┌──────────────────────────────┐
                   │    Daily Fleet Brief Record  │
                   │    (Rendered in Portal UI)   │
                   └──────────────────────────────┘
```

1. **Configuration Drift:**
   - Compares static and dynamic initialization parameters (`V$PARAMETER`) across databases sharing the same environment tag or cluster role.
   - Identifies non-standard memory sizing (`sga_target`, `pga_aggregate_target`), optimizer settings (`optimizer_mode`), or audit configurations.
2. **Capacity Exhaustion Cliffs:**
   - Ingests the 30-day linear regression trends from `sentinel.forecasting`.
   - Isolates tablespaces and Fast Recovery Areas (FRA) predicted to reach 100% capacity within 7, 14, or 30 days.
3. **Repeated Error Outliers:**
   - Aggregates ORA codes from alert log snapshots across the fleet over the past 24 hours.
   - Surfaces clusters (e.g., three databases logging `ORA-01555 Snapshot too old` during the same overnight batch window).

### 3.2 Briefing Structure (`fleet_briefs`)
The briefing is saved in PostgreSQL and presented in the portal:
- **Headline Status:** Clean, Attention Required, or Critical.
- **Top 3 Fleet Action Items:** Explicit bulleted priorities for the morning shift.
- **Drift Highlights:** Formatted tables of parameter deviations between peer instances.
- **Capacity Warnings:** Exhaustion timelines with days remaining.
- **Evidence References:** Digest IDs pointing back to underlying `evidence_snapshots`.

---

## 4. Deep Telemetry & Hypothesis-Driven Investigation

Currently, the Investigator gathers standard readings based on a single static playbook. Phase 29 upgrades the Investigator to correlate **SessionDB** and **WaitDB** telemetry for deep root-cause analysis.

### 4.1 Enhanced Playbooks

| Playbook | Existing Readings | Phase 29 Deep Telemetry Additions |
|---|---|---|
| `blocking_sessions` | `blocking`, `sessions`, `session_programs` | Cached statements for blocker and blocked (`get_session_statement`), cached explain plans (`get_statement_plan`), enqueue wait chains from `WaitDB` |
| `memory_pressure` | `memory`, `performance`, `top_sql`, `ora_errors` | PGA usage per session from `SessionDB`, shared pool SQL child cursor counts from `V$SQL` |
| `tablespace_full` | `tablespace`, `datafile_sizes`, `growth` | Largest segment owners, uncommitted transactions holding undo segments |

### 4.2 Causal Hypothesis Validation Loop
When an incident is investigated:
1. **Initial Assessment:** The Investigator matches the trigger and collects initial snapshots.
2. **Hypothesis Formulation:** The local model formulates at most two hypotheses (e.g., *"Locking caused by unindexed foreign key execution plan"* vs. *"Application thread connection stall"*).
3. **Plan Inspection:** If execution plans are implicated and the caller holds administrator credentials, the agent queries the cached plan of the blocking session from `SessionDB`.
4. **Factual Verification:** The Verifier ensures the identified table, index, or wait object matches the exact text in `V$SQL_PLAN` and `V$SESSION`.

---

## 5. Local Model Telemetry Pre-Compression

Local models (`qwen2.5-coder:14b`, `llama3.1:8b`) have limited effective attention spans compared to frontier cloud models. Dumping raw JSON rows of 50 sessions and full SQL text causes context pollution and hallucinated figures.

Phase 29 introduces deterministic Python pre-compressors before prompt construction:

### 5.1 Pre-Compression Strategies

| Telemetry Type | Raw MCP / Snapshot Output | Pre-Compressed Model Representation | Token Reduction |
|---|---|---|---|
| **User Sessions** | 100+ raw `V$SESSION` rows with 15 columns | Grouped by `PROGRAM` and `STATUS`: counts, active count, top 3 resource consumers by CPU/elapsed time. | ~80% |
| **Execution Plans** | Raw `V$SQL_PLAN` rows (Id, Op, Options, Cost, Bytes, CPU) | Tree representation highlighting only high-cost operations: `TABLE ACCESS FULL`, large Cartesian joins, index range scans with high cost. | ~75% |
| **Alert Log Errors** | Full timestamped alert log excerpts | Grouped by ORA code with first seen, last seen, count, and clean snippet without repeated stack dumps. | ~70% |
| **Wait Events** | All wait events from `V$SYSTEM_EVENT` | Top 5 non-idle wait classes and events exceeding 5% of total database time. | ~85% |

### 5.2 Structured Grammars & Constrained Decoding
All LLM invocations for the Advisor, Fleet Analyst, and Investigator use constrained JSON decoding (`format="json"` via Ollama's schema grammar engine). Unstructured prose output is prohibited in agent steps.

---

## 6. Data Model & Migrations

Phase 29 requires Alembic migration `0013_agent_evolution.py`.

### 6.1 New Table: `fleet_briefs`

```sql
CREATE TABLE fleet_briefs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT clock_timestamp(),
    status VARCHAR(20) NOT NULL DEFAULT 'healthy', -- healthy, warning, critical
    headline VARCHAR(255) NOT NULL,
    summary TEXT NOT NULL,
    action_items JSONB NOT NULL DEFAULT '[]'::jsonb,
    drift_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    capacity_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_digests JSONB NOT NULL DEFAULT '[]'::jsonb,
    written_by VARCHAR(50) NOT NULL DEFAULT 'fleet_analyst',
    model VARCHAR(100) NOT NULL,
    verified BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX ix_fleet_briefs_created ON fleet_briefs(created_at DESC);
```

### 6.2 Schema Update: Link `agent_findings` to `operation_proposals`
Add a nullable foreign key column `proposal_id` to `agent_findings`:

```sql
ALTER TABLE agent_findings ADD COLUMN proposal_id UUID REFERENCES operation_proposals(id) ON DELETE SET NULL;
CREATE INDEX ix_agent_findings_proposal ON agent_findings(proposal_id);
```

---

## 7. API Contracts

### 7.1 Advisor Endpoint
- **`POST /api/v1/agents/findings/{id}/propose`**
  - **Permission:** `proposal.create` (DBA or Admin Operator)
  - **Input:** Optional override parameter dict (e.g., custom size in MB).
  - **Response:**
    ```json
    {
      "finding_id": "c71e21b0-9cf6-4444-9f22-123456789abc",
      "proposal_id": "e82f32c1-8df7-5555-af33-987654321def",
      "action": "tablespace.add_omf_datafile",
      "target_id": "a11d11a0-1bb2-3333-8c44-555555555555",
      "preview_sql": "ALTER TABLESPACE USERS ADD DATAFILE SIZE 1024M AUTOEXTEND ON NEXT 512M MAXSIZE 32767M;",
      "impact": "Expands tablespace USERS by 1024 MB to relieve 94.4% capacity pressure.",
      "risk": "low",
      "status": "pending"
    }
    ```

### 7.2 Fleet Analyst Endpoints
- **`GET /api/v1/agents/fleet/briefs`**
  - **Permission:** Signed-in operator, DBA, or administrator (`read` permission).
  - **Query Params:** `limit=10`
  - **Response:** List of historic fleet briefs.
- **`GET /api/v1/agents/fleet/briefs/latest`**
  - **Response:** Most recent completed fleet brief.
- **`POST /api/v1/agents/fleet/briefs/run`**
  - **Permission:** `agent.run`
  - **Response:** Trigger an on-demand fleet brief generation task.

---

## 8. Portal UI Enhancements

1. **Agent Findings Card (`FindingCard.tsx`):**
   - When a finding is marked `decision = "accepted"`, display an active **"Draft Proposal"** action button.
   - Once drafted, the card shows a persistent badge linking directly to the pending proposal: `Proposal #104 (Pending Review)`.
2. **Fleet Workspace (`FleetWorkspace.tsx`):**
   - Add a dedicated **Daily Fleet Brief** widget at the top of the fleet overview.
   - Displays status pill, 3 morning action items, and expandable accordions for configuration drift and tablespace exhaustion horizons.
3. **SessionDB & Investigation Linkage:**
   - On the Incident page, when an investigation cites a SQL statement or lock chain, provide a direct navigation link into `#/sessions/databases/{id}?sid={sid}&serial={serial}`.

---

## 9. Settings, Permissions, and Audit

### 9.1 New Configuration Settings
Editable in **Admin > Agents** or overridden per database target:

| Setting | Default | Per Target | Meaning |
|---|---|---|---|
| `AGENT_ADVISOR_ENABLED` | `true` | Yes | Enable drafting operation proposals from findings. |
| `AGENT_FLEET_BRIEF_ENABLED` | `false` | No | Automatically generate daily fleet briefing. |
| `AGENT_FLEET_BRIEF_HOUR_UTC` | `6` | No | UTC hour to trigger the fleet analysis run. |
| `AGENT_DEEP_TELEMETRY` | `true` | Yes | Allow Investigator to inspect session cursors and explain plans. |

### 9.2 Permissions
- Drafting a proposal requires `proposal.create` permission (DBA or Administrator).
- Running the fleet briefing on-demand requires `agent.run`.
- Viewing fleet briefs is open to all authenticated roles.

### 9.3 Audit Trail
All agent actions record immutable rows in `audit_logs`:
- `agent.advisor.draft`: Records finding ID, proposed action, target database, and generated proposal ID.
- `agent.fleet_analyst.run`: Records run duration, databases scanned, anomalies detected, and brief ID.

---

## 10. Acceptance & Guardrails

- [ ] **No Model Execution:** The model cannot execute SQL, shell commands, or network calls. It only drafts text and parameters checked by code.
- [ ] **Deterministic Pre-Validation:** Every Advisor proposal passes `sentinel.operations.validation.preflight` before being saved to PostgreSQL.
- [ ] **Strict Verifier Enforcement:** The Fleet Brief and Advisor impact text must pass the factual Verifier (no ungrounded figures or hallucinated databases).
- [ ] **RBAC Safeguards:** An investigation started by a standard operator cannot inspect `V$SQL_PLAN` or restricted session SQL text.
- [ ] **Fallback Resiliency:** If the local LLM is offline or times out, the Advisor generates a deterministic proposal draft using static templates, and the Fleet Analyst generates a metrics-only tabular summary.
- [ ] **Database Safety:** All telemetry is read through `GSYSTEM` with `SELECT`-only privileges. No target database objects or schemas are modified.

---

## 11. Out of Scope for Phase 29

- Fully autonomous execution of remediation proposals without human sign-off.
- Autonomous index creation or schema modifications.
- Fine-tuning custom local weights (standard inference via Ollama is retained).
- Cross-database multi-instance distributed deadlock resolution.
- Live modification of Oracle initialization parameters (`ALTER SYSTEM`).
