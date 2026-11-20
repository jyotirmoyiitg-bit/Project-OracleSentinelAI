# Incident Response Playbook

**Document ID:** KNOW-INC-003  
**Version:** 1.0  
**Date:** 2026-11-10  
**Classification:** Internal — Reference Material  
**Purpose:** RAG knowledge base — structured incident response procedures for Oracle Sentinel AI

---

## Table of Contents

1. [Incident Response Framework](#1-incident-response-framework)
2. [Incident Severity Classification](#2-incident-severity-classification)
3. [General Incident Response Flow](#3-general-incident-response-flow)
4. [Storage Incidents](#4-storage-incidents)
5. [Availability Incidents](#5-availability-incidents)
6. [Performance Incidents](#6-performance-incidents)
7. [Security Incidents](#7-security-incidents)
8. [Backup and Recovery Incidents](#8-backup-and-recovery-incidents)
9. [Archive Log and FRA Incidents](#9-archive-log-and-fra-incidents)
10. [Connection and Authentication Incidents](#10-connection-and-authentication-incidents)
11. [Communication Templates](#11-communication-templates)
12. [Post-Incident Review](#12-post-incident-review)
13. [Sentinel AI Incident Integration](#13-sentinel-ai-incident-integration)

---

## 1. Incident Response Framework

### 1.1 Objectives

- Minimize impact on database availability and performance
- Restore normal operations within defined recovery time objectives (RTO)
- Preserve evidence for post-incident analysis
- Communicate effectively with stakeholders
- Prevent recurrence through root cause analysis

### 1.2 Roles and Responsibilities

| Role | Responsibilities |
|---|---|
| **On-Call DBA** | First responder; initial triage and containment |
| **DBA Team Lead** | Escalation point; coordinates complex incidents |
| **Platform Administrator** | Sentinel AI platform issues; collector and alerting problems |
| **Security Team** | Security-related incidents; access violations |
| **Management** | Business impact assessment; stakeholder communication |

### 1.3 Tools

- **Oracle Sentinel AI:** Incident detection, metrics collection, compliance tracking
- **SQL*Plus / SQLcl:** Direct database access for diagnosis and remediation
- **RMAN:** Backup and recovery operations
- **Enterprise Manager (if available):** Additional monitoring and management
- **Communication channels:** Email, Slack/Teams, phone for escalation

---

## 2. Incident Severity Classification

### 2.1 Severity Levels

| Severity | Response Time | Escalation Time | Examples |
|---|---|---|---|
| **Critical (P1)** | 15 minutes | 2 hours | Database down, data corruption, security breach |
| **High (P2)** | 1 hour | 4 hours | Tablespace full, ARCHIVELOG failure, authentication failure |
| **Medium (P3)** | 4 hours | 8 hours | High wait events, slow-growing tablespaces, compliance failures |
| **Low (P4)** | 24 hours | Next business day | Informational alerts, forecast warnings, non-critical compliance gaps |

### 2.2 Severity Determination Factors

1. **Business impact:** Number of affected users and systems
2. **Data risk:** Potential for data loss or corruption
3. **Duration:** How long the issue has persisted
4. **Trend:** Whether the situation is deteriorating
5. **Environment:** Production incidents are higher severity than non-production

---

## 3. General Incident Response Flow

### 3.1 Detection and Triage

```
1. Alert received (automated or manual)
2. Verify the alert is genuine (not a false positive)
3. Assess severity based on impact and urgency
4. Assign incident owner
5. Begin investigation timer
```

### 3.2 Investigation

```
1. Gather current metrics from Sentinel AI
2. Check alert log for related errors
3. Review recent changes (deployments, configuration changes)
4. Correlate with historical data and baselines
5. Identify root cause or contributing factors
```

### 3.3 Containment and Resolution

```
1. Apply immediate containment measures
2. Implement fix or workaround
3. Verify resolution through metrics
4. Monitor for recurrence
5. Document resolution steps
```

### 3.4 Closure and Review

```
1. Close incident in Sentinel AI
2. Add resolution notes and evidence
3. Schedule post-incident review (for P1/P2)
4. Update runbooks based on lessons learned
5. Implement preventive measures
```

---

## 4. Storage Incidents

### 4.1 Tablespace Full (ORA-01653)

**Severity:** High (P2) — Critical (P1) if production and actively failing transactions

**Detection:** Sentinel AI rule `tablespace_usage_percent > 95`

**Diagnosis:**

```sql
-- Check tablespace usage
SELECT tablespace_name,
       SUM(bytes) / 1024 / 1024 AS total_mb,
       SUM(MAXBYTES) / 1024 / 1024 AS max_mb
FROM dba_data_files
GROUP BY tablespace_name;

-- Identify largest segments in the tablespace
SELECT segment_name, segment_type,
       SUM(bytes) / 1024 / 1024 AS size_mb
FROM dba_segments
WHERE tablespace_name = '<TABLESPACE>'
GROUP BY segment_name, segment_type
ORDER BY size_mb DESC
FETCH FIRST 10 ROWS ONLY;

-- Check autoextend status
SELECT file_name, autoextensible, maxbytes,
       bytes / 1024 / 1024 AS current_mb
FROM dba_data_files
WHERE tablespace_name = '<TABLESPACE>';
```

**Resolution Options:**

| Option | Command | Risk |
|---|---|---|
| Enable autoextend | `ALTER DATABASE DATAFILE '<file>' AUTOEXTEND ON MAXSIZE 32G;` | Low — monitor disk space |
| Add datafile | `ALTER TABLESPACE <name> ADD DATAFILE '<path>' SIZE 10G AUTOEXTEND ON;` | Low |
| Resize datafile | `ALTER DATABASE DATAFILE '<file>' RESIZE 20G;` | Low — requires free disk |
| Drop unnecessary objects | `DROP TABLE <table> PURGE;` | Medium — verify with application team |
| Move segments | Export and recreate in different tablespace | High — requires maintenance window |

**Prevention:**

- Enable autoextend with reasonable MAXSIZE
- Set Sentinel AI warning threshold at 75%
- Review growth forecasts weekly

### 4.2 Temporary Tablespace Full (ORA-01652)

**Severity:** High (P2)

**Diagnosis:**

```sql
-- Check temp space usage
SELECT tablespace_name,
       SUM(bytes_used) / 1024 / 1024 AS used_mb,
       SUM(bytes_free) / 1024 / 1024 AS free_mb
FROM v$temp_space_header
GROUP BY tablespace_name;

-- Identify sessions using temp space
SELECT s.sid, s.serial#, s.username, s.sql_id,
       t.blocks * 8 / 1024 AS temp_mb
FROM v$sort_usage t
JOIN v$session s ON t.session_addr = s.saddr
ORDER BY temp_mb DESC;
```

**Resolution:**

1. Identify and potentially terminate the session causing excessive temp usage
2. Add temp file: `ALTER TABLESPACE TEMP ADD TEMPFILE '<path>' SIZE 10G AUTOEXTEND ON;`
3. Analyze and optimize the SQL query causing excessive sorting

### 4.3 Undo Tablespace Issues (ORA-01555)

**Severity:** High (P2)

**Diagnosis:**

```sql
-- Check undo retention
SHOW PARAMETER undo_retention;

-- Check undo tablespace usage
SELECT tablespace_name, status,
       SUM(bytes) / 1024 / 1024 AS size_mb
FROM dba_undo_extents
GROUP BY tablespace_name, status;

-- Check for snapshot too old errors
SELECT * FROM v$undostat
WHERE ssolderrcnt > 0
ORDER BY snap_time DESC;
```

**Resolution:**

1. Increase UNDO_RETENTION: `ALTER SYSTEM SET undo_retention = 3600;`
2. Add undo datafile with autoextend
3. Optimize long-running queries that exceed undo retention

---

## 5. Availability Incidents

### 5.1 Database Instance Down

**Severity:** Critical (P1)

**Detection:** Sentinel AI connectivity check failure

**Diagnosis:**

```bash
# Check alert log for shutdown reason
tail -100 $ORACLE_BASE/diag/rdbms/<db>/<inst>/trace/alert_<inst>.log

# Check OS processes
ps -ef | grep pmon

# Check listener status
lsnrctl status
```

**Resolution Flow:**

```
1. Check alert log for cause of downtime
2. If clean shutdown: STARTUP
3. If crash: STARTUP (instance recovery automatic)
4. If media failure: RMAN recovery required
5. If corruption: Assess extent of damage; restore from backup if needed
6. Verify all services and connections restored
```

### 5.2 Hung Database (No Response)

**Severity:** Critical (P1)

**Diagnosis:**

```sql
-- From another session if possible
SELECT sid, serial#, event, seconds_in_wait, blocking_session
FROM v$session
WHERE status = 'ACTIVE'
ORDER BY seconds_in_wait DESC;

-- Check for latch contention
SELECT name, gets, misses, immediate_misses
FROM v$latch
WHERE misses > 0
ORDER BY misses DESC;
```

**Resolution:**

1. Identify blocking sessions and terminate if appropriate
2. Check for I/O saturation on the host
3. If completely hung, may require SRIMM (Systemstate dump) and instance restart
4. Contact Oracle Support if latch issues persist

### 5.3 Listener Issues

**Severity:** High (P2) — Critical (P1) if no connections possible

**Diagnosis:**

```bash
lsnrctl status
lsnrctl services
```

**Common Issues:**

| Issue | Symptom | Fix |
|---|---|---|
| Listener down | TNS-12541 | `lsnrctl start` |
| Service not registered | TNS-12514 | Check `LOCAL_LISTENER`; wait for PMON registration |
| Password protection | TNS-01190 | Set `LSNRCTL` password or adjust `REMOTE_LOGIN_PASSWORDFILE` |
| Port conflict | TNS-12542 | Change listener port in `listener.ora` |

---

## 6. Performance Incidents

### 6.1 High CPU Usage

**Severity:** Medium (P3) — High (P2) if sustained

**Diagnosis:**

```sql
-- Check CPU-related wait events
SELECT event, total_waits, time_waited,
       time_waited / total_waits AS avg_wait_ms
FROM v$system_event
WHERE event LIKE '%CPU%'
ORDER BY time_waited DESC;

-- Identify top SQL by CPU
SELECT sql_id, elapsed_time, cpu_time, executions,
       cpu_time / executions AS cpu_per_exec
FROM v$sql
ORDER BY cpu_time DESC
FETCH FIRST 10 ROWS ONLY;
```

**Resolution:**

1. Identify top CPU-consuming SQL
2. Check for recent execution plan changes
3. Consider SQL plan baselines or hints
4. Evaluate whether workload is expected (batch windows, reports)

### 6.2 I/O Bottleneck

**Severity:** High (P2)

**Diagnosis:**

```sql
-- Check I/O wait events
SELECT event, total_waits, time_waited
FROM v$system_event
WHERE event LIKE 'db file%'
ORDER BY time_waited DESC;

-- Check file-level I/O stats
SELECT name, phyrd, phywr, readtim, writetim
FROM v$filestat f
JOIN v$datafile d ON f.file# = d.file#
ORDER BY readtim + writetim DESC;
```

**Resolution:**

1. Identify files with highest I/O
2. Check for full table scans on large tables
3. Consider adding indexes or materialized views
4. Evaluate storage subsystem capacity

### 6.3 Lock Contention

**Severity:** High (P2)

**Diagnosis:**

```sql
-- Check blocking sessions
SELECT blocking_session, sid, serial#, event, seconds_in_wait, sql_id
FROM v$session
WHERE blocking_session IS NOT NULL;

-- Check lock types
SELECT type, lmode, request, lmode, sid
FROM v$lock
WHERE request > 0;
```

**Resolution:**

1. Identify the blocking session
2. Determine if the blocking transaction is legitimate
3. If appropriate, terminate the blocking session: `ALTER SYSTEM KILL SESSION '<sid>,<serial#>';`
4. Review application transaction design to reduce lock duration

---

## 7. Security Incidents

### 7.1 Unauthorized Access Attempt

**Severity:** Critical (P1)

**Response:**

```
1. Identify the source IP and attempted credentials
2. Check if access was successful
3. If successful: immediately revoke access and assess data exposure
4. Lock the compromised account
5. Review audit trail for post-compromise activity
6. Notify security team and management
7. Preserve evidence for forensic analysis
```

### 7.2 Suspicious SQL Activity

**Severity:** High (P2) — Critical (P1) if data exfiltration suspected

**Diagnosis:**

```sql
-- Check for unusual SQL patterns
SELECT sql_id, sql_text, executions, elapsed_time,
       parsing_schema_name, last_active_time
FROM v$sql
WHERE last_active_time > SYSDATE - 1/24
ORDER BY last_active_time DESC;
```

**Response:**

1. Identify the session and user executing suspicious SQL
2. If active threat, terminate the session
3. Review the SQL for data exfiltration patterns (large exports, bulk selects)
4. Check for newly created objects or privileges
5. Escalate to security team

### 7.3 Privilege Escalation Detection

**Severity:** Critical (P1)

**Response:**

```
1. Identify the privilege change and who made it
2. Revoke the unauthorized privilege
3. Audit all actions taken with the escalated privilege
4. Review account security (password, MFA status)
5. Notify security team and management
```

---

## 8. Backup and Recovery Incidents

### 8.1 Backup Failure

**Severity:** High (P2)

**Diagnosis:**

```sql
-- Check recent backup status
SELECT session_key, status,
       TO_CHAR(start_time, 'YYYY-MM-DD HH24:MI') AS start_time,
       TO_CHAR(end_time, 'YYYY-MM-DD HH24:MI') AS end_time,
       output_bytes / 1024 / 1024 / 1024 AS output_gb
FROM v$rman_backup_job_details
ORDER BY start_time DESC
FETCH FIRST 10 ROWS ONLY;
```

**Response:**

1. Identify the failure reason from RMAN logs
2. Common causes: disk space, network issues, corruption
3. Fix the underlying cause
4. Run backup immediately
5. Verify backup completeness

### 8.2 No Recent Backup

**Severity:** High (P2) — Critical (P1) if backup window missed for > 48 hours

**Detection:** Sentinel AI check `oracle.backup.freshness`

**Response:**

1. Verify backup infrastructure is operational
2. Run immediate backup
3. Investigate why scheduled backup failed
4. Fix scheduling or infrastructure issue
5. Verify backup success

---

## 9. Archive Log and FRA Incidents

### 9.1 FRA Full (ORA-00257)

**Severity:** Critical (P1) — database may hang

**Detection:** Sentinel AI rule `fra_usage_percent > 90`

**Diagnosis:**

```sql
-- Check FRA usage
SELECT name, space_limit / 1024 / 1024 AS limit_gb,
       space_used / 1024 / 1024 AS used_gb,
       space_reclaimable / 1024 / 1024 AS reclaimable_gb,
       number_of_files AS files
FROM v$recovery_file_dest;

-- Check file type breakdown
SELECT file_type, percent_space_used, percent_space_reclaimable,
       number_of_files
FROM v$recovery_area_usage;
```

**Immediate Resolution:**

```sql
-- Option 1: Increase FRA size
ALTER SYSTEM SET db_recovery_file_dest_size = 100G;

-- Option 2: Delete obsolete backups via RMAN
RMAN> DELETE NOPROMPT OBSOLETE;

-- Option 3: Delete specific archived logs (if backed up)
RMAN> DELETE ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-7';
```

**Prevention:**

- Set Sentinel AI warning at 80% FRA usage
- Configure automated RMAN cleanup
- Monitor archive log generation rate

### 9.2 Archive Log Gap

**Severity:** High (P2)

**Diagnosis:**

```sql
-- Check for gaps
SELECT * FROM v$archive_gap;

-- Verify archive log sequence
SELECT thread#, sequence#, first_time, next_time, applied
FROM v$archived_log
ORDER BY thread#, sequence#;
```

**Response:**

1. Identify the gap range
2. Copy missing archive logs from primary or backup
3. Register missing logs if needed
4. Verify Data Guard sync (if applicable)

---

## 10. Connection and Authentication Incidents

### 10.1 Maximum Processes Reached (ORA-00020)

**Severity:** High (P2)

**Diagnosis:**

```sql
-- Check current vs. max processes
SELECT value FROM v$parameter WHERE name = 'processes';
SELECT COUNT(*) FROM v$session;
```

**Resolution:**

```sql
-- Increase processes (requires restart)
ALTER SYSTEM SET processes = 500 SCOPE=SPFILE;
-- Restart database
SHUTDOWN IMMEDIATE;
STARTUP;
```

**Immediate workaround:** Identify and terminate idle sessions

### 10.2 Authentication Failures

**Severity:** Medium (P3) — High (P2) if widespread

**Diagnosis:**

```sql
-- Check for locked accounts
SELECT username, account_status, lock_date, expiry_date
FROM dba_users
WHERE account_status != 'OPEN'
ORDER BY lock_date DESC;
```

**Response:**

1. Identify the cause (password expiry, too many failed attempts)
2. Unlock accounts if appropriate: `ALTER USER <user> ACCOUNT UNLOCK;`
3. Reset passwords if needed
4. Review password policy and application credential management

---

## 11. Communication Templates

### 11.1 Initial Notification

```
Subject: [INCIDENT][<Severity>] <Brief Description> - <Database/Environment>

Incident ID: <ID>
Severity: <P1/P2/P3/P4>
Database: <Name>
Environment: <Production/Staging/Development>
Detected: <Timestamp>
Status: Investigating

Description:
<Brief description of the issue and impact>

Current Action:
<What is being done to address the issue>

Next Update: <Time>
```

### 11.2 Status Update

```
Subject: UPDATE [INCIDENT][<Severity>] <Brief Description>

Status: <Investigating/Identified/Remediating/Monitoring>
Impact: <Current impact assessment>
ETA: <Estimated time to resolution>

Update:
<What has changed since last update>
```

### 11.3 Resolution Notification

```
Subject: RESOLVED [INCIDENT][<Severity>] <Brief Description>

Status: Resolved
Resolution Time: <Duration>
Root Cause: <Brief explanation>

Resolution:
<What was done to resolve the issue>

Preventive Measures:
<Steps to prevent recurrence>
```

---

## 12. Post-Incident Review

### 12.1 When to Conduct

- All Critical (P1) incidents
- All High (P2) incidents
- Recurring Medium (P3) incidents
- Any incident with significant business impact

### 12.2 Review Timeline

| Activity | Timeline |
|---|---|
| Schedule review | Within 24 hours of resolution |
| Conduct review | Within 5 business days |
| Document findings | Within 2 business days of review |
| Implement preventive measures | Per agreed action plan |

### 12.3 Review Agenda

1. **Timeline reconstruction:** What happened and when
2. **Detection analysis:** How was the issue detected; could it have been earlier
3. **Response analysis:** What was done well; what could have been faster
4. **Root cause identification:** The underlying cause of the incident
5. **Preventive measures:** Actions to prevent recurrence
6. **Process improvements:** Changes to runbooks, thresholds, or procedures

### 12.4 Blameless Culture

Post-incident reviews focus on process and system improvements, not individual blame. The goal is to learn and improve, not to assign fault.

---

## 13. Sentinel AI Incident Integration

### 13.1 Automated Incident Creation

Sentinel AI automatically creates incidents when rules are violated:

- Rule evaluation triggers incident generation
- Severity is derived from rule configuration
- Initial diagnosis data is attached to the incident
- Notifications are sent based on severity and routing rules

### 13.2 Incident Lifecycle in Sentinel AI

| State | Description |
|---|---|
| `new` | Automatically created by rule evaluation |
| `acknowledged` | Operator has acknowledged and begun investigation |
| `investigating` | Active investigation in progress |
| `resolving` | Fix is being applied |
| `resolved` | Issue is fixed and verified |
| `closed` | Incident is formally closed with resolution notes |

### 13.3 AI-Assisted Incident Response

When AI components are available:

- **Root cause suggestions:** AI analyzes metrics and suggests likely causes
- **Runbook retrieval:** RAG retrieves relevant procedures from knowledge base
- **Impact assessment:** AI correlates the incident with dependent systems
- **Resolution guidance:** Step-by-step remediation suggestions with source citations

**Important:** AI suggestions are advisory only. All actions require human review and approval.

---

## References

- Oracle Database Administration Guide
- Oracle Database Backup and Recovery User's Guide
- Oracle Database Security Guide
- ITIL Incident Management Best Practices
- NIST SP 800-61: Computer Security Incident Handling Guide
