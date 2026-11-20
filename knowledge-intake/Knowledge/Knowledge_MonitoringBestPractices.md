# Database Monitoring Best Practices

**Document ID:** KNOW-MON-002  
**Version:** 1.0  
**Date:** 2026-11-10  
**Classification:** Internal — Reference Material  
**Purpose:** RAG knowledge base — monitoring best practices for Oracle Sentinel AI operations

---

## Table of Contents

1. [Monitoring Philosophy](#1-monitoring-philosophy)
2. [Collection Strategy](#2-collection-strategy)
3. [Key Performance Indicators](#3-key-performance-indicators)
4. [Threshold Design Principles](#4-threshold-design-principles)
5. [Storage Monitoring](#5-storage-monitoring)
6. [Performance Monitoring](#6-performance-monitoring)
7. [Availability Monitoring](#7-availability-monitoring)
8. [Security Monitoring](#8-security-monitoring)
9. [Backup and Recovery Monitoring](#9-backup-and-recovery-monitoring)
10. [Alert Log Analysis](#10-alert-log-analysis)
11. [Wait Event Analysis](#11-wait-event-analysis)
12. [Monitoring Query Optimization](#12-monitoring-query-optimization)
13. [Sentinel AI-Specific Practices](#13-sentinel-ai-specific-practices)
14. [Common Monitoring Mistakes](#14-common-monitoring-mistakes)

---

## 1. Monitoring Philosophy

### 1.1 Core Principles

1. **Observe without interfering:** Monitoring queries must have minimal impact on production workloads. Read-only access, bounded queries, and off-peak scheduling are mandatory.
2. **Measure what matters:** Focus on metrics that directly impact availability, performance, and compliance. Avoid collecting data that is never acted upon.
3. **Baseline before alerting:** Establish normal behavior patterns before setting thresholds. Static thresholds without baselines generate noise.
4. **Context over numbers:** A metric value alone is meaningless without context (time of day, recent changes, related metrics).
5. **Actionable alerts:** Every alert should trigger a defined action. If no action exists, the alert is noise.

### 1.2 Monitoring Layers

| Layer | What It Covers | Tools |
|---|---|---|
| **Infrastructure** | OS, disk, network, memory | OS metrics, SNMP |
| **Database Instance** | Instance health, memory, processes | V$ views, data dictionary |
| **Workload** | Sessions, SQL performance, waits | AWR, ASH, V$SESSION |
| **Application** | Business logic, response times | Application logs, tracing |
| **Compliance** | Policy adherence, security posture | Custom checks, audit trails |

---

## 2. Collection Strategy

### 2.1 Collection Frequency

| Metric Category | Recommended Frequency | Rationale |
|---|---|---|
| Instance availability | Every 1–5 minutes | Fast detection of outages |
| Tablespace usage | Every 15–30 minutes | Storage changes slowly |
| Session counts | Every 5–15 minutes | Balance between granularity and overhead |
| Wait events | Every 15–30 minutes | Trends matter more than instant values |
| Alert log parsing | Every 5–15 minutes | Timely error detection |
| Backup status | Every 1–4 hours | Backup jobs run periodically |
| Compliance checks | Every collection cycle | Consistent evaluation |

### 2.2 Collection Overhead Management

**Rules for Sentinel AI collectors:**

1. **Read-only queries only:** No DML, no package execution that modifies state
2. **Bounded execution time:** Each collector query has a timeout (default: 30 seconds)
3. **Result row limits:** Cap result sets to prevent memory exhaustion
4. **Off-peak heavy queries:** AWR-like queries run during low-activity windows
5. **Licensing awareness:** No AWR/ASH/ADDM queries unless explicitly licensed

### 2.3 Data Dictionary vs. Dynamic Performance Views

| Source | Characteristics | Use Case |
|---|---|---|
| **Data Dictionary (DBA_*)** | Static metadata; low overhead | Configuration, structure, security |
| **Dynamic Performance (V$)** | Real-time state; moderate overhead | Current performance, sessions, waits |
| **AWR/ASH** | Historical performance; requires license | Deep performance analysis |

---

## 3. Key Performance Indicators

### 3.1 Availability KPIs

| KPI | Target | Measurement |
|---|---|---|
| **Instance uptime** | 99.9%+ | Time between startup and shutdown events |
| **Connection success rate** | 99.5%+ | Successful connections / total connection attempts |
| **Mean Time to Detect (MTTD)** | < 5 minutes | Time from failure to alert generation |
| **Mean Time to Respond (MTTR)** | < 30 minutes | Time from alert to operator acknowledgment |

### 3.2 Performance KPIs

| KPI | Target | Measurement |
|---|---|---|
| **Buffer cache hit ratio** | > 95% | Logical reads vs. physical reads |
| **Library cache hit ratio** | > 99% | Soft parses vs. total parses |
| **Average wait time** | < 10ms | Total wait time / total waits |
| **SQL response time (p95)** | Defined per workload | 95th percentile SQL execution time |

### 3.3 Storage KPIs

| KPI | Target | Measurement |
|---|---|---|
| **Tablespace utilization** | < 85% | Used space / total space |
| **FRA utilization** | < 80% | Used FRA / total FRA |
| **Growth rate** | Within forecast | Daily/weekly space growth trend |
| **Autoextend events** | Monitored | Frequency of autoextend triggers |

---

## 4. Threshold Design Principles

### 4.1 Threshold Tiers

| Tier | Purpose | Example |
|---|---|---|
| **Warning** | Early indication of potential issue | Tablespace at 75% |
| **Critical** | Immediate action required | Tablespace at 90% |
| **Emergency** | Imminent failure | Tablespace at 95% |

### 4.2 Dynamic vs. Static Thresholds

| Approach | Pros | Cons |
|---|---|---|
| **Static** | Simple; predictable | Doesn't adapt to growth patterns |
| **Dynamic** | Adapts to trends; fewer false positives | Requires baseline data; complex |

### 4.3 Threshold Design Rules

1. **Start with percentiles, not averages:** Averages hide spikes; p95/p99 reveal real issues
2. **Use rate-of-change thresholds:** A tablespace growing 10%/day is more urgent than one at 80% growing 0.1%/day
3. **Account for business cycles:** Batch windows, month-end processing, and backups create expected patterns
4. **Set hysteresis:** Avoid flapping by requiring a different threshold to clear an alert than to trigger it
5. **Review and adjust:** Thresholds should be reviewed quarterly and adjusted based on false positive/negative rates

### 4.4 Sentinel AI Threshold Configuration

Thresholds are defined in YAML rule files under `rules/`. Each rule specifies:

```yaml
metric: tablespace_usage_percent
condition: "> 85"
severity: warning
cooldown_minutes: 60
```

---

## 5. Storage Monitoring

### 5.1 Tablespace Monitoring

**Key queries:**

| Metric | View | Query Pattern |
|---|---|---|
| Tablespace usage | `DBA_TABLESPACES`, `DBA_DATA_FILES`, `DBA_FREE_SPACE` | Sum of used vs. total space |
| Autoextend status | `DBA_DATA_FILES` | `AUTOEXTENSIBLE`, `MAXBYTES` |
| Growth trend | Historical collection data | Linear regression on space usage |

**Critical alerts:**

- Tablespace usage > 85% (warning)
- Tablespace usage > 95% (critical)
- Autoextend disabled on growing tablespace
- Datafile reached MAXSIZE

### 5.2 FRA Monitoring

**Key queries:**

| Metric | View | Query Pattern |
|---|---|---|
| FRA usage | `V$RECOVERY_FILE_DEST` | `SPACE_USED` / `SPACE_LIMIT` |
| File type breakdown | `V$RECOVERY_AREA_USAGE` | Usage by `FILE_TYPE` |
| Archive log rate | `V$ARCHIVED_LOG` | Count and size over time |

**Critical alerts:**

- FRA usage > 80% (warning)
- FRA usage > 90% (critical)
- Archive log destination unavailable
- Projected FRA fill-up within 24 hours

### 5.3 Undo Tablespace Monitoring

**Key queries:**

| Metric | View | Query Pattern |
|---|---|---|
| Undo usage | `V$UNDOSTAT` | Undo bytes used over time |
| Undo retention | `V$PARAMETER` | `UNDO_RETENTION` setting |
| Undo pressure | `V$UNDOSTAT` | `SSOLDERRCNT` (snapshot too old errors) |

**Critical alerts:**

- Snapshot too old errors detected
- Undo tablespace autoextending frequently
- Undo retention goal not met

---

## 6. Performance Monitoring

### 6.1 Wait Event Categories

| Category | Examples | Indicates |
|---|---|---|
| **CPU** | `ON CPU`, `wait for unread message on broadcast channel` | CPU contention |
| **I/O** | `db file scattered read`, `db file sequential read` | Disk I/O bottleneck |
| **Concurrency** | `enq: TX - row lock contention`, `library cache lock` | Lock contention |
| **Configuration** | `buffer busy waits`, `free buffer waits` | Memory configuration |
| **Network** | `SQL*Net message from client`, `SQL*Net more data from client` | Network latency |

### 6.2 Top Wait Event Analysis

**Methodology:**

1. Identify top 5 wait events by total wait time
2. Classify each wait event by category
3. Determine if wait is normal for the workload type
4. Identify specific sessions or SQL associated with waits
5. Recommend tuning actions based on wait category

### 6.3 SQL Performance Monitoring

**Without AWR/ASH (unlicensed):**

| Metric | View | Query Pattern |
|---|---|---|
| Active SQL | `V$SQL` | SQL with high executions or elapsed time |
| Parsing stats | `V$SYSSTAT` | Parse calls, hard parses |
| Buffer gets | `V$SQL` | Logical I/O per SQL |

**With AWR/ASH (licensed):**

- Top SQL by elapsed time, CPU, and I/O
- SQL execution plan changes
- ASH-based wait event analysis per SQL

---

## 7. Availability Monitoring

### 7.1 Instance Health Checks

| Check | Method | Frequency |
|---|---|---|
| Instance status | `V$INSTANCE` | Every 1–5 minutes |
| Database open mode | `V$DATABASE` | Every 5 minutes |
| Listener status | TNSPING or listener query | Every 5 minutes |
| Session connectivity | Test connection | Every 1–5 minutes |

### 7.2 Background Process Monitoring

| Check | View | Alert Condition |
|---|---|---|
| Process count | `V$BGPROCESS` | Missing expected processes |
| PMON activity | Alert log | Frequent process cleanup |
| SMON activity | Alert log | Frequent instance recovery |

### 7.3 Connection Monitoring

| Metric | View | Alert Condition |
|---|---|---|
| Active sessions | `V$SESSION` | Unusual spike or drop |
| Session wait states | `V$SESSION_WAIT` | Sessions stuck in same wait |
| Connection errors | Alert log | ORA-12514, ORA-12516 |

---

## 8. Security Monitoring

### 8.1 Authentication Monitoring

| Check | Method | Alert Condition |
|---|---|---|
| Failed logins | `V$SESSION`, audit trail | Spike in failed attempts |
| Expired accounts | `DBA_USERS` | Accounts past expiry |
| Locked accounts | `DBA_USERS` | Unexpected account locks |

### 8.2 Privilege Monitoring

| Check | Method | Alert Condition |
|---|---|---|
| DBA role grants | `DBA_ROLE_PRIVS` | New DBA grants |
| SYSDBA access | `V$PWFILE_USERS` | Unauthorized SYSDBA users |
| Privilege changes | Audit trail | Privilege escalation events |

### 8.3 Object Access Monitoring

| Check | Method | Alert Condition |
|---|---|---|
| DDL changes | Audit trail, `DBA_OBJECTS` | Unexpected object creation/drop |
| Schema changes | `DBA_TAB_MODIFICATIONS` | Unapproved schema modifications |
| Data pump activity | Alert log, OS processes | Unauthorized export/import |

---

## 9. Backup and Recovery Monitoring

### 9.1 Backup Evidence Tracking

| Check | Method | Alert Condition |
|---|---|---|
| Last backup time | RMAN repository, alert log | No backup within threshold |
| Backup success | RMAN logs, alert log | Failed backup jobs |
| Backup size trend | Historical data | Anomalous backup sizes |

### 9.2 Recovery Readiness

| Check | Method | Alert Condition |
|---|---|---|
| Archive log continuity | `V$ARCHIVED_LOG` | Gaps in sequence |
| Control file backup | RMAN repository | Stale control file backup |
| Spfile backup | File system check | Missing spfile backup |

### 9.3 Sentinel AI Compliance Check

Sentinel AI check: `oracle.backup.freshness` — evaluates backup evidence when a supported source is configured. Thresholds:

- Warning: No backup in 48 hours
- Critical: No backup in 7 days

---

## 10. Alert Log Analysis

### 10.1 Error Code Classification

| Severity | ORA- Code Patterns | Examples |
|---|---|---|
| **Critical** | Instance crash, corruption | ORA-00600, ORA-00601, ORA-01578 |
| **High** | Structural failures | ORA-01113, ORA-01115, ORA-01122 |
| **Medium** | Configuration issues | ORA-01653, ORA-01654, ORA-01652 |
| **Low** | Informational | ORA-01505, ORA-16032 |

### 10.2 Alert Log Parsing Strategy

1. **Incremental parsing:** Track last-read position; parse only new entries
2. **Pattern matching:** Use regex to extract ORA- codes and context
3. **Deduplication:** Group identical errors within a time window
4. **Correlation:** Link alert log entries to collection metrics
5. **Escalation:** Route critical errors immediately; batch informational messages

### 10.3 Common Alert Log Patterns

| Pattern | Meaning | Action |
|---|---|---|
| `ORA-00020: maximum number of processes` | Process limit reached | Increase PROCESSES parameter |
| `ORA-01555: snapshot too old` | Undo retention insufficient | Increase UNDO_RETENTION |
| `ORA-01653: unable to extend table` | Tablespace full | Add datafile or enable autoextend |
| `ORA-01652: unable to extend temp segment` | Temporary tablespace full | Increase temp tablespace |
| `ORA-00257: archiver error` | FRA full or archive dest unavailable | Free FRA space or fix destination |

---

## 11. Wait Event Analysis

### 11.1 Wait Event Diagnosis Flow

```
Identify top wait event
    ↓
Classify wait category (CPU, I/O, Concurrency, Configuration, Network)
    ↓
Determine scope (instance-wide vs. specific session/SQL)
    ↓
Check related metrics (I/O stats, lock waits, memory usage)
    ↓
Identify root cause
    ↓
Recommend action
```

### 11.2 Common Wait Event Remediation

| Wait Event | Likely Cause | Remediation |
|---|---|---|
| `db file scattered read` | Full table scans; insufficient indexes | Add indexes; review SQL |
| `db file sequential read` | Index scans; undo reads | Check index efficiency |
| `library cache lock` | Concurrent parsing; invalidations | Pin frequently used objects |
| `enq: TX - row lock contention` | Application locking issues | Review transaction design |
| `buffer busy waits` | Buffer cache too small | Increase DB_CACHE_SIZE |
| `gc buffer busy` | RAC inter-instance contention | Review data affinity |

---

## 12. Monitoring Query Optimization

### 12.1 Query Design Rules

1. **Select only needed columns:** Avoid `SELECT *`
2. **Use appropriate views:** `V$` for real-time, `DBA_` for metadata
3. **Limit result sets:** Use `ROWNUM` or `FETCH FIRST` for sampling
4. **Avoid joins across large V$ views:** Query and correlate in application code
5. **Use materialized views for aggregations:** Pre-compute expensive aggregations

### 12.2 Query Timeout Handling

- Each collector query has a configurable timeout (default: 30 seconds)
- On timeout, the collector logs the event and skips that metric
- Repeated timeouts trigger a collector health alert
- Query plans are cached to avoid repeated hard parses

### 12.3 Licensing-Aware Queries

Sentinel AI maintains a licensing flag per database. Queries requiring licensed features are gated:

```python
if db_config.get("licensed_awr"):
    results = await collect_awr_metrics(db)
else:
    results = await collect_unlicensed_alternatives(db)
```

---

## 13. Sentinel AI-Specific Practices

### 13.1 Read-Only Collection

All Sentinel AI collectors execute as the `GSYSTEM` read-only user. No DML, DDL, or package execution that modifies database state is permitted during collection.

### 13.2 Adapter Pattern

Oracle-specific SQL is isolated in `adapters/oracle/`. This enables:

- Easy substitution of alternative data sources
- Testing with mock adapters
- Future support for non-Oracle databases

### 13.3 Rule-Based Evaluation

Rules are defined in YAML files and evaluated against collected metrics. This separation enables:

- Rule changes without code deployment
- Version-controlled policy definitions
- Per-database rule customization

### 13.4 Forecasting

Sentinel AI uses linear regression to forecast:

- Tablespace fill-up dates
- FRA capacity exhaustion
- Growth trend anomalies

Forecasts require at least 7 days of historical data for reliable predictions.

### 13.5 AI-Assisted Diagnostics

When available, AI components provide:

- Natural language explanations of wait events
- Suggested remediation steps based on knowledge base
- Root cause analysis using RAG-retrieved documentation

AI is advisory only and never replaces automated rule evaluation.

---

## 14. Common Monitoring Mistakes

### 14.1 Alert Fatigue

| Mistake | Consequence | Fix |
|---|---|---|
| Too many low-severity alerts | Operators ignore all alerts | Raise severity thresholds; use warning tier for informational |
| No cooldown period | Same alert fires repeatedly | Implement cooldown timers |
| No actionable guidance | Operators don't know what to do | Include remediation steps in alerts |

### 14.2 Collection Overhead

| Mistake | Consequence | Fix |
|---|---|---|
| Frequent heavy queries | Impact on production workload | Reduce frequency; optimize queries |
| No result limits | Memory exhaustion in collector | Cap result sets |
| AWR queries without license | Licensing violation | Check licensing flag before execution |

### 14.3 Threshold Misconfiguration

| Mistake | Consequence | Fix |
|---|---|---|
| Static thresholds without baselines | False positives and negatives | Establish baselines first |
| No hysteresis | Alert flapping | Use different trigger/clear thresholds |
| One-size-fits-all | Doesn't account for database differences | Per-database threshold overrides |

---

## References

- Oracle Database Performance Tuning Guide
- Oracle Database SQL Tuning Guide
- Oracle Automatic Workload Repository User's Guide
- Oracle Secure Configuration Guide
- CIS Oracle Database Benchmark
