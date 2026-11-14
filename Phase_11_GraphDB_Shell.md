# Phase 11 — GraphDB Shell

**Phase:** 11  
**Status:** Implemented  
**Prerequisites:** Phase 7 sign-in, the database registry, and the read-only Oracle readings  
**Next phase:** [Phase 12 — Memory structures](Phase_12_GraphDB_Memory.md)

GraphDB is the portal name for a graphical view of one registered Oracle database. It is not a separate graph database.

## 1. Overview

Add a **GraphDB** section for every signed-in role. Opening it shows one selected database in four panels: Memory structures, Physical structures, Logical structures, and Management. This phase builds the route, the database choice, and the empty panels. It does not query Oracle and does not draw measurements.

## 2. Rules

- Every signed-in role can open GraphDB; the permission is `dashboard.read`. The picture is read-only for everyone. An administrator (`admin`, `platform_admin`) also sees the file, control-file, redo-member, and segment listings. Every other role sees the disk-storage summary without those listings. This replaced the original administrator-only rule on 14 Nov 2026.
- The selected database is the scope. A panel never mixes rows from another database.
- The picture is drawn from approved readings in later phases. The language model does not invent sizes, file names, or process names.
- There is no Execute, Backup now, or SQL control on this page.
- Monitoring, alerts, and Copilot keep working when GraphDB is closed.

## 3. Portal

- Navigation entry **GraphDB**, shown to every signed-in operator.
- Route `#/graph` asks the operator to choose a registered database.
- Route `#/graph/databases/{id}` shows the display name, TNS alias, and the four panels.
- Each panel is a closed section with its title and the sentence `Not collected in this phase.`
- A missing or disabled database shows the existing not-found state and does not call Oracle.

## 4. API

No new Oracle reading. The page uses the existing database list and database detail endpoints. The shell route needs `dashboard.read` and reports whether the caller sees the storage listings.

## 5. Acceptance

- [x] An administrator sees GraphDB and can open one database.
- [x] An operator, DBA, auditor, or viewer does not see the entry and receives 403 if the route is requested.
- [x] Four panels render with no measurements and no action that changes Oracle.
- [x] Switching the database does not leave the previous database's name on the page.

## 6. Tests

- Portal test: administrator opens `#/graph/databases/{id}` and sees the four panel titles.
- Portal test: a non-administrator does not see the GraphDB link.
- API test, if a dedicated route is added: non-administrator receives 403.
- Live check: `test_real_phase11\Verify_Phase11_Real.ps1`. `-CheckPrerequisites` does not connect to Oracle. The full run is a pass only after it is actually run.

## 7. Out of scope

SGA, PGA, processes, data files, control files, redo logs, tablespaces, segments, and backup status. Those start in Phases 12 through 15.
