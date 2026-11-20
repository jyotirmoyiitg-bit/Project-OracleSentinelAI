# Phase 27 — Tablespace timeline

**Phase:** 27
**Status:** Implemented
**Prerequisites:** [Phase 26 — Copilot fixes](Phase_26_Copilot_Fixes.md)

## 1. Overview

On the Overview page of MY19C the "Tablespace Used Pct" timeline went up and down between about 0 and 95 percent every few minutes, and the "Tablespace usage" card showed 0.5 percent. Used space in a tablespace does not fall unless a DBA shrinks or drops something, so the chart looked wrong.

The stored rows were correct. The storage probe writes one `storage.tablespace.used_pct` row per tablespace at every collection, labelled with the tablespace name. MY19C has five:

| Tablespace | Used % of allocated |
|---|---|
| USERS | 94.41 |
| SYSTEM | 94.19 |
| SYSAUX | 94.06 |
| UNDOTBS1 | 10.58 |
| INDX | 0.47 |

| Where | What went wrong |
|---|---|
| Timeline | It asked for the metric by name only and drew every row as one line, so the line jumped from tablespace to tablespace |
| Card | It showed whichever row came first. All five share a timestamp, so it showed INDX at 0.47 percent |
| Percentage | It is used space against the space allocated now. When a datafile autoextends, the percentage falls while the used bytes rise |

No migration, no new Oracle grant, no new setting, no new view queried.

## 2. One series per tablespace

`GET /api/v1/databases/{id}/metrics` accepts `label=key=value`, up to four times, such as `label=tablespace_name=USERS`. Only rows whose labels match are returned. The key must be a lower-case label name and the value is compared, never run. A malformed filter is refused with HTTP 422.

The Overview timeline:

- Shows a series picker when the metric has more than one series. Each option shows the latest value, highest first, followed by "All tablespaces".
- Opens on the fullest tablespace and asks the API for that series only.
- With "All tablespaces", draws one line per tablespace, each in its own colour, with a legend.
- Never joins rows of different series into one line, whatever the API returns.
- Asks for up to 5,000 rows, the API's maximum, so a seven-day range is not cut short as early.

The same applies to any labelled metric, such as temporary tablespace use. The `source` label says where a row came from and does not make a series.

## 3. The card

"Tablespace usage" shows the fullest tablespace by name, for example "94.4 Percent — USERS, the fullest of 5". With one tablespace it shows that tablespace's name only.

## 4. Used space against the autoextend maximum

The storage probe also reads `MAXBYTES` from `DBA_DATA_FILES`, a column the monitoring account could already read. Two metrics are stored per tablespace:

| Metric | Meaning |
|---|---|
| `storage.tablespace.bytes_max` | The most the datafiles may grow to: `MAXBYTES` for an autoextend file (at least its current size), the current size otherwise |
| `storage.tablespace.used_pct_of_max` | Used bytes against that maximum |

The Tablespaces table on the Overview page has an "Of maximum" column, and the metric can be picked on the timeline. The existing `storage.tablespace.used_pct` is unchanged, so rules, health, forecasts, and the copilot read the same values as before.

The statement was run once against both local databases:

| Database | Tablespace | Used % of allocated | Used % of maximum |
|---|---|---|---|
| MY19C | USERS | 94.41 | 14.25 |
| MY19C | SYSTEM | 94.19 | 2.90 |
| MY19C | SYSAUX | 94.06 | 1.44 |
| PDBORCL | SYSTEM | 98.98 | 0.82 |
| PDBORCL | INDX | 1.00 | 1.00 (no autoextend) |

## 5. Not changed

Health and the default rules still warn above 85 percent and turn critical above 95 percent of the space allocated now. With autoextend on, that is why MY19C shows Warning although its largest tablespace is 14 percent of its maximum. Moving health and the rules to `used_pct_of_max` changes when people are alerted, so it is left for a decision.

## 6. Verification

| Check | Result |
|---|---|
| Backend tests | 593 passed, 1 skipped. Three collection tests now expect five synthetic storage rows instead of three |
| New backend tests | `backend/tests/api/test_phase27_tablespace_series.py`: label filter, malformed and excess filters, detail summary, synthetic rows, probe values, read-only statement |
| Frontend tests | 80 passed, including `frontend/src/Phase27.test.tsx` |
| Live Oracle | The changed statement ran against PDBORCL and MY19C (section 4) |
| PostgreSQL | The label filter ran against the development database's stored rows |
| Browser | A seeded copy of the MY19C mix: the card names USERS, the chart opens on USERS as a flat line, "All tablespaces" draws five flat lines, the table shows "Of maximum", no console errors |
