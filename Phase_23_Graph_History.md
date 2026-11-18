# Phase 23 — Graph History: GraphDB and WaitDB Open Filled

**Phase:** 23
**Status:** Implemented (23A quick second sample, 23B stored counter samples)
**Next phase:** [Phase 24 — Copilot supervisor](Phase_24_Copilot_Supervisor.md)
**Prerequisites:** [Phase 22 — Agents](Phase_22_Agents.md), [Phase 19 — GraphDB wait stats](Phase_19_GraphDB_Waits.md), [Phase 16 — Rate meters and refresh](Phase_16_GraphDB_Live.md)

## 1. Overview

A rate is the change of an Oracle counter between two readings. Before this phase a page kept the earlier reading only in the browser, so GraphDB showed "Rate needs a second sample" and every WaitDB chart was empty until the page's refresh interval had passed once. An operator who opened a database for a quick look saw no rates.

Phase 23 fixes this in two parts.

- **23A, quick second sample.** A page takes its second reading 3 seconds after it opens instead of waiting for the refresh interval. Nothing is stored.
- **23B, stored counter samples.** While the scheduler runs, the API stores the counters of every enabled database every 5 minutes. A page that opens reads the last hour of stored samples from PostgreSQL and replays them through the same rate code it uses for live readings. The WaitDB charts are drawn at once, and the GraphDB meters show an average since the latest stored sample.

No migration, no new Oracle grant, and no new statement were added. The sampler runs five readings that GraphDB and WaitDB already run. Agent snapshots are not used for this: they are taken on request, are off by default, and are at least 300 seconds apart.

## 2. What is stored

One sample is one row in `observations` for one database.

| Column | Value |
|---|---|
| `metric_name` | `activity.sample` |
| `timestamp` | When the counters were read (UTC) |
| `value` | Empty |
| `value_str` | The sample as JSON |
| `unit`, `source` | `json`, `graph_history` |

The JSON holds only numbers and Oracle's own event names.

| Key | Reading | Content |
|---|---|---|
| `counters` | `get_rate_counters` | physical reads, redo entries, parse count (total), execute count |
| `events` | `get_wait_events` | The 15 non-idle wait events with the most wait time: event, wait class, total waits, time waited |
| `cpu_micro` | `get_wait_cpu` | DB CPU |
| `io` | `get_io_counters` | db block changes, physical reads, physical reads direct, consistent gets, physical writes, physical writes direct |
| `files` | `get_file_io` | Data file reads, writes, read time, write time |

A reading that is not granted or fails is left out of the sample and the rest is stored. A database that cannot be reached stores nothing for that sweep. No row is written to `current_observations`, so health, staleness, rules, and compliance do not see these rows. The general observation list leaves them out; they are returned only when asked for by name or through the history route.

Lock activity, parsing, and buffer busy waits are not stored. Those sections fill from the quick second sample.

## 3. The sampler

`GraphHistorySampler` is a timer in the API process. It starts with the collection scheduler, so it runs only when `SCHEDULER_ENABLED=true`.

- It checks every 30 seconds whether a sweep is due, so a changed interval applies without a restart. The first sweep runs about 5 seconds after start.
- A sweep reads every enabled database, four at a time, each on one Oracle logon with a 45 second limit. One slow or failed database does not stop the others.
- Retention runs every hour and deletes samples older than the retention setting. Other observations are not touched by it.
- In synthetic mode the sampler uses the same synthetic readings as the pictures.

## 4. Settings

The settings are in Admin, in a new **Graph history** section. They apply to the whole system.

| Setting | Default | Range | Meaning |
|---|---|---|---|
| `graph_history_enabled` | on | | Store samples while the scheduler runs |
| `graph_history_interval_seconds` | 300 | 60 to 3600 | Seconds between samples of one database |
| `graph_history_retention_hours` | 24 | 1 to 168 | Hours a sample is kept |

At the defaults one database holds about 288 rows. There is no value for one database in this phase.

## 5. API

`GET /api/v1/databases/{id}/graph/history?minutes=60` returns the stored samples, oldest first. It needs `dashboard.read`, which every signed-in role has. It reads PostgreSQL only. `minutes` is 5 to 1440 and at most 120 samples are returned. A disabled or unknown database returns 404.

| Field | Meaning |
|---|---|
| `samples[]` | `taken_at`, `age_seconds`, `counters`, `events`, `cpu_micro`, `io`, `files`, in the shapes of the live pictures |
| `enabled` | The scheduler is on and the setting is on |
| `interval_seconds` | The stored interval |
| `caption` | One sentence for the page |

`age_seconds` is measured by the server, so the page does not depend on the browser clock matching the server clock.

## 6. Portal

**GraphDB.** The first reading of a database also asks for the history. When a stored sample is at least 10 seconds old, the four rate meters show the average since that sample, with the caption "Average since the stored sample at 10:42. A live rate follows." Three seconds later the page reads the activity picture again, on its own, and shows the live rate with the usual caption. The memory and storage pictures are not read again for the quick sample.

**WaitDB.** The first reading of a database replays the stored samples. Block activity, Event waits, Top waits, Data file requests, and Data file latency are drawn from them. The first live reading is compared with the latest stored sample, so the event table has rates at once. Three seconds later the page reads again; parsing, lock activity, and buffer busy rates appear then. The caption under the tabs says which points are stored and which are live.

A counter that is lower than in the earlier sample, which happens after an instance restart, shows "Not collected" as before. When nothing is stored, or the history cannot be read, the pages behave as before except for the quick second sample.

## 7. Rules kept

- Only fixed readings from the catalog are run. No operator text and no model text reaches a statement.
- No language model is used.
- Nothing is written to Oracle. No table or view is created in `GSYSTEM`.
- No AWR, ASH, or ADDM view is read.
- Privileged execution stays disabled.

## 8. Acceptance

- [x] A sample holds the counters the pages use, on one reader, from five fixed readings.
- [x] A denied or failed reading is left out and the rest of the sample is stored. An unreachable database stores nothing.
- [x] A sweep stores one row per enabled database, writes no current value, and one slow database does not stop the others.
- [x] The history route returns samples oldest first with their age, is open to every role, refuses a window outside 5 to 1440 minutes, and does not read Oracle.
- [x] Retention removes old samples only. The general observation list leaves samples out.
- [x] The timer samples when due and follows a changed interval. The three settings are in Admin and are checked.
- [x] GraphDB shows a rate from the stored sample at once and a live rate after the quick second sample, without reading memory or storage again.
- [x] WaitDB draws its five charts from stored samples at once and samples again after 3 seconds.
- [x] Against the real API and portal with synthetic databases, the sampler stored rows, WaitDB opened with five charts drawn, and GraphDB opened with its meters filled.
- [ ] The full live check is recorded only after `Verify_Phase23_Real.ps1` is run against Oracle. A written spec is not a pass.

## 9. Tests

- `backend/tests/graph/test_phase23_history.py`: the sample and its five readings; a denied reading; an unreachable database; the sweep; a slow database; history order and age; the route and its refusals; the caption; retention; the observation list; the timer; the settings.
- `frontend/src/Phase23.test.tsx`: a missing or malformed history; GraphDB with a stored sample; GraphDB with nothing stored; WaitDB with stored samples; WaitDB with nothing stored.
- Live check: `test_real_phase23\Verify_Phase23_Real.ps1`. `-CheckPrerequisites` does not connect to Oracle. The full run is a pass only after it is actually run.

## 10. Out of scope

A value for one database; a chart of more than the last hour; a time axis on the charts; stored lock, parsing, and buffer busy counters; rates on the Overview page; copilot and agent use of the stored samples. The Fleet analyst, the Advisor, and copilot answers from agent snapshots, which the Phase 22 documents called Phase 23, are left for a later phase.
