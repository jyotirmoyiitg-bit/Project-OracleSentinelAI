# Phase 17 — GraphDB for Every Operator

**Phase:** 17  
**Status:** Implemented  
**Prerequisites:** [Phase 16 — Rate meters, refresh, and live check](Phase_16_GraphDB_Live.md)

## 1. Overview

GraphDB is a read-only picture, so every signed-in role may open it. An administrator keeps the file, control-file, redo-member, and segment listings. Every other role sees the disk-storage summary. The page is rearranged after the performance-home reference: five columns, count ovals, flow meters, pills, and cylinders, drawn in the portal palette. Each picture request opens one Oracle session. The page links to the database-scoped copilot.

## 2. Rules

- The shell and the three picture endpoints need `dashboard.read`. `viewer`, `operator`, `dba`, `security_auditor`, `admin`, and `platform_admin` all receive HTTP 200. An unknown role still receives HTTP 400 and a signed-out request still receives HTTP 401.
- `platform.read` (the administrator) decides detail. The storage response carries `detail` and `detail_note`. For a non-administrator `data_files.files`, `temp_files.files`, `control_files.files`, `redo_logs.members`, and `segments.rows` are empty, `detail` is `false`, and `detail_note` says the listings are shown to an administrator. Counts, used percent, the cylinders, recovery, archive, and backup stay. `control_files.count` is returned so the summary can still say how many control files exist.
- The SQL for every reading is unchanged. No new grant. `V$BGPROCESS`, `V$PROCESS`, ASH, AWR, ADDM, and `V$SYSMETRIC` stay unqueried.
- One Oracle session per picture request. `shared_reader` in the Oracle activity adapter logs on once and runs every fixed reading on it. A failed logon is reported as the same limitation on every card and the response is still HTTP 200. Synthetic mode keeps the synthetic readers and opens nothing.
- There is still no Execute, Backup, kill, or SQL control. **Ask copilot** opens the existing database-scoped chat; it does not add a tool.

## 3. Portal

- Navigation entry **GraphDB** for every signed-in operator. `#/graph` is the chooser. `#/graph/databases/{id}` is the picture.
- The heading carries an **Active database** block (cylinder mark, name, environment, monitoring status, health badge), the database switch with the active entry marked, **Database detail**, and **Ask copilot**. The fleet sidebar highlights the same database.
- Storage cylinders are inline SVG after the banded database symbol: navy outline, three bands, light top, blue part rising from the bottom with the used percent. A cylinder with no percent stays empty and grey. It is never drawn as zero.
- Five columns, left to right:
  1. **Service** (uptime, container database, pluggable database, response, total users, active users, average active) and **Host** (CPU donut since instance startup, run queue meter, free memory).
  2. **Server processes**: PGA target and used, one oval per count (dedicated, shared, dispatchers, parallel query, job queue), total at the foot.
  3. Four **rate flow meters** (physical reads, redo entries, parse count, execute count) from two page samples, then the **SGA** card: pool bars, buffer-cache hit ratio, in-memory usage, current SGA caption, shared-pool used, result cache, and the Automatic Shared Memory Management footer.
  4. **Background processes**: database writer, recovery writer, redo log writer, and archiver pills named from the session program (grey when no such session was returned), Data Guard, and the full scrolling list.
  5. **Disk storage**: database files, fast recovery area, redo logs, and archive log as cylinders with their counts, then backup. An administrator also sees closed sections for data files, temp files, control files, redo members, and the 20 largest segments. A non-administrator sees the note instead.
- A footer shows the engine, the alias, the snapshot sentence, and the collection time in the browser's clock. The API stamps UTC.
- The board drops to three, two, and one column at narrower widths. The palette is the portal's: white cards, blue accent, green figures and fills. The black-and-green reference palette is not used.

## 3a. Copilot

- The Copilot page shows the same **Active database** block when the chat is scoped to a database, and a **Database scope** switch that opens `#/copilot/databases/{id}` or returns to fleet chat. Fleet and incident scope show a short note instead.
- Guided questions are offered once. The Common list keeps only questions with no catalog equivalent (FRA status, datafiles, memory, sessions, backups, errors, alert log, Data Guard, open incidents, search runbooks, database users). Tablespace pressure, top SQL, running jobs, health, "is there a problem", blocking, and compliance are asked from their catalog groups. The API drops a repeated prompt, and the page drops a repeated prompt or a repeated label inside one group.
- A catalog answer no longer starts with the question id. The first line is `Label. Status X. Health Y.` and the page draws it as a verdict row with status and health chips. The id stays in the stored envelope.
- The page uses the portal's sans-serif type throughout (no serif prose), a bordered disclosure line, question chips, a highlighted "You asked" block, a shadowed answer card, monospace reading lines, and a rounded composer with a focus ring.

## 4. API

- `GET /api/v1/graph` gains `detail`.
- `GET /api/v1/databases/{id}/graph/storage` gains `detail`, `detail_note`, and `control_files.count`, and returns the summary for a non-administrator.
- `GET /api/v1/databases/{id}/graph/memory` and `.../graph/activity` are unchanged in shape and open to every role.

## 5. Acceptance

- [x] An operator, DBA, auditor, and viewer open GraphDB and receive memory, storage, and activity.
- [x] The operator storage response has no file path, redo member, or segment name, and says the listings are shown to an administrator.
- [x] The administrator storage response keeps the listings.
- [x] One Oracle logon per picture request; a failed logon returns limitations, not an HTTP error.
- [x] The page renders the five columns with the portal palette and has no run, kill, backup, or SQL control.
- [x] **Ask copilot** opens `#/copilot/databases/{id}`.
- [x] The merge-conflict markers in `docs/oracle-permissions.md` and `Oracle_Sentinel_GSYSTEM_ReadOnly_Provisioning.sql` are gone.
- [ ] The full live check is recorded only after `Verify_Phase17_Real.ps1` is run against Oracle. A written spec is not a pass.

## 6. Tests

- API test: operator, DBA, auditor, and viewer receive 200 on the shell and the three pictures; the operator storage response has empty listings, `detail: false`, and the note; the administrator response has `detail: true`.
- API test: one request to the storage endpoint opens one Oracle session, closes it, and runs all eight readings on it; a failed logon returns 200 with a limitation on every card.
- Unit test: `summarize` keeps counts, used percent, recovery, archive, and backup and empties every listing.
- Portal test: the five columns render for an administrator with pills, ovals, cylinders, and listings; the operator sees the summary note, no path, and can open the copilot; rates still need a second sample.
- Live check: `test_real_phase17\Verify_Phase17_Real.ps1`. `-CheckPrerequisites` does not connect to Oracle. The full run signs in as the administrator, creates a temporary `phase17-operator`, confirms the detail and the summary, removes that account, and launches the portal. The full run is a pass only after it is actually run.

## 7. Out of scope

A per-database entitlement for GraphDB, `V$SYSMETRIC` for response time and average active sessions, a server-side snapshot cache, a shared connection pool across requests, and any control that changes the database.
