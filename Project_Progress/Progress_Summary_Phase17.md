# Phase 17 Implementation Summary — GraphDB for Every Operator

**Status:** GraphDB is open to every signed-in role. A non-administrator receives the disk-storage summary without listings. Each picture request opens one Oracle session. The page follows the performance-home arrangement in five columns with the portal palette, and links to the database-scoped copilot. The live launcher was added. `-CheckPrerequisites` passed. The full Oracle run was not performed.<br>
**Phase specification:** [Phase 17 — GraphDB for every operator](../Phase_17_GraphDB_Access.md)<br>
**Previous summary:** [Phase 16 — Rate meters and refresh](./Progress_Summary_Phase16.md)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head remains `0010`. Privileged execution stays disabled. No new grant was added. No reading SQL changed. No table or view was created in `GSYSTEM`.

## What Was Implemented

### Access

- `GET /api/v1/graph` and the memory, storage, and activity endpoints in `backend/src/sentinel/api/v1/graph.py` require `dashboard.read` instead of `platform.read`. Every canonical role has it. An unknown role is still HTTP 400 and a signed-out request is still HTTP 401.
- `platform.read` now decides detail only. `summarize` in `backend/src/sentinel/graph/storage.py` empties `data_files.files`, `temp_files.files`, `control_files.files`, `redo_logs.members`, and `segments.rows`, sets `detail: false`, and adds `detail_note`: `File, control-file, redo-member, and segment listings are shown to an administrator.` Counts, used percent, recovery, archive, and backup are untouched. `control_files.count` was added so the summary still shows the number of control files. The shell response gains `detail`.
- The portal shows the **GraphDB** entry to every signed-in user. The storage card renders the note for a non-administrator and closed listing sections for an administrator.

### One Oracle session per request

`shared_reader` in `backend/src/sentinel/adapters/oracle/activity.py` logs on once with the configured monitoring account, yields a reader bound to that session, and closes it when the request finishes. `load_memory`, `load_storage`, and `load_activity` receive that reader. A failed logon is turned into the same unavailable limitation on every card and the endpoint still returns HTTP 200. Synthetic mode keeps the synthetic readers and does not open a session. Before this change each card logged on separately, so one refresh of the page cost around twenty Oracle logons.

### Layout

`frontend/src/GraphPage.tsx` was rebuilt after the performance-home reference while keeping the portal's cards, type, and colours:

| Column | Contents |
|---|---|
| Service and Host | Uptime, container and pluggable database, response, users, average active; CPU donut since instance startup, run-queue meter, free memory |
| Server processes | PGA target and used; ovals for dedicated, shared, dispatchers, parallel query, job queue; total at the foot |
| Rates and SGA | Four flow meters from two page samples (`blks/s`, `redo ents/s`, `parse reqs/s`, `SQL exec/s`); pool bars; hit-ratio, in-memory, and shared-pool meters; result cache; ASMM footer |
| Background processes | Database writer, recovery writer, redo log writer, and archiver pills named from the session program, grey when absent; Data Guard; full scrolling list |
| Disk storage | Database files, fast recovery area, redo logs, and archive log cylinders with counts; backup; administrator-only closed sections for data files, temp files, control files, redo members, and the 20 largest segments |

The heading carries an **Active database** block (cylinder mark, display name, environment, monitoring status, and the health badge), the database switch with the active entry marked, **Database detail**, and **Ask copilot** (opens `#/copilot/databases/{id}`). The fleet sidebar highlights the same database while GraphDB is open.

The storage cylinders are inline SVG drawn after the banded database symbol: a navy outline, three bands, a light top, and a blue part that rises from the bottom with the used percent. Database files use the used percent, the fast recovery area uses its used percent, redo uses the share of current or active groups, and a cylinder with no percent (archive log, or a missing recovery area) stays empty and grey rather than drawn as zero. The `aria-label` states the percent. A footer shows the engine, alias, snapshot sentence, and collection time; the UTC stamp from the API is converted in the browser. The board drops to three, two, and one column at narrower widths. The styles are in `frontend/src/styles.css` under the `graph-` and `storage-` classes.

### Copilot

- `frontend/src/CopilotPage.tsx` shows the **Active database** block (shared `components/ActiveDatabase.tsx`, which also serves GraphDB) when the chat is scoped to a database, plus a **Database scope** select that switches to `#/copilot/databases/{id}` or back to fleet chat. Fleet and incident scope show a note instead. The composer placeholder names the database.
- Duplicate questions were removed. `GUIDED_COMMANDS` in `backend/src/sentinel/ai/orchestrator.py` keeps eleven Common questions with no catalog equivalent; tablespace pressure, top SQL, running jobs, health, "is there a problem", blocking, and compliance are now asked from the catalog groups. `public_guided_commands` drops a repeated prompt, and `uniqueCommands` on the page drops a repeated prompt or a repeated label within one group as a safety net. Typing any removed prompt still works.
- `present_catalog` in `backend/src/sentinel/ai/catalog.py` no longer prefixes the answer with the question id (`Q02`). The first line is `Label. Status X. Health Y.`; the page parses it into a verdict row with status and health chips, tolerating an older stored answer that still carries the id. The id remains in the envelope stored on the tool execution.
- The copilot styles moved to the portal's sans-serif type (no serif prose), a bordered disclosure line, question chips, a "You asked" block with an accent bar, a shadowed answer card, monospace reading lines, and a rounded composer with a focus ring.
- `frontend/src/CopilotPage.test.tsx` gained a database-scope test: active block, scope select, one offer per prompt, verdict chips without the id, and the scope switch back to fleet. **4 passed**. Backend copilot tests: **105 passed**.

### Housekeeping

- The merge-conflict markers left in `docs/oracle-permissions.md` and `Oracle_Sentinel_GSYSTEM_ReadOnly_Provisioning.sql` were removed, keeping the ORA-02030 explanation.
- `backend/tests/graph/__init__.py` was added. Without it `tests/graph/test_activity.py` collided with `tests/ai/test_activity.py` and the full backend suite stopped at collection.
- `.claude/launch.json` holds `backend-preview` and `frontend` for a synthetic preview against a separate `sentinel_graph_preview` database. The preview sign-in secret lives in the gitignored `.claude/preview_admin_password.txt`.

## Live launcher

`test_real_phase17\Verify_Phase17_Real.ps1` follows the Phase 16 launcher. Steps 1 to 6 are the same: tools, live configuration, Docker PostgreSQL, Alembic head `0010`, local administrator sign-in, and the real target. Step 7 reads the three pictures as the administrator, checks `detail: true` with listings, then creates a temporary `phase17-operator`, confirms the shell, memory, and activity open for it, confirms the storage response is the summary with no listing and the note, and removes that account. Step 8 launches the portal.

```powershell
.\test_real_phase17\Verify_Phase17_Real.ps1 -CheckPrerequisites
.\test_real_phase17\Verify_Phase17_Real.ps1 -TnsAlias PDBORCL -Interactive
```

A full run stops whatever is already listening on port 8000 and the portal port (5173 by default). The language model stays off. Counts and states are printed. File paths, cookies, passwords, and SQL are not printed.

## Verification

- `backend/tests/api/test_graph.py`: operator, DBA, auditor, and viewer receive 200 on the shell and the three pictures; the operator storage response has empty listings, `detail: false`, and the note; the administrator keeps the listings; one Oracle session is opened and closed per storage request with all eight readings on it; a failed logon returns 200 with a limitation that names the Oracle code and not the driver text.
- `backend/tests/graph/test_storage.py`: `summarize` keeps counts, used percent, recovery, archive, and backup and empties every listing.
- Full backend suite: **346 passed, 1 skipped**.
- `frontend/src/GraphPage.test.tsx`: the five columns render for an administrator with pills, ovals, cylinders, and listings; the operator sees the summary note, no path, no segment, and opens the copilot; rates still need a second sample; a failed storage refresh keeps the memory card. **6 passed**. `App.test.tsx` **7 passed**. `tsc -b` clean.
- Both views were opened in the synthetic preview (`previewadmin` and `previewoperator`) and matched the tests.
- `Verify_Phase17_Real.ps1` parsed, and `-CheckPrerequisites` passed. The full Oracle launcher was not run.

## Resume / Next-Agent Pickup

1. Restart the API before expecting `detail` on the shell and storage responses from a process that was already running.
2. Do not add a grant. No reading SQL changed in this phase.
3. Keep new GraphDB readings behind `dashboard.read`; gate only paths and object names behind `platform.read`.
4. Do not record the full live check as passed until `Verify_Phase17_Real.ps1` is run against Oracle.
5. Archive destination names are still shown to a non-administrator because they carry the VALID or ERROR status. Hide them in `summarize` if that is wanted.
6. These edits are uncommitted. Commit only when requested.
