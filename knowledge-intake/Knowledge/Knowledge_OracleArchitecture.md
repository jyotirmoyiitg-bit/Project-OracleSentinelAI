# Oracle Database Architecture Reference

**Document ID:** KNOW-ORA-001  
**Version:** 1.0  
**Date:** 2026-11-10  
**Classification:** Internal — Reference Material  
**Purpose:** RAG knowledge base — Oracle architecture fundamentals for Sentinel AI diagnostics

---

## Table of Contents

1. [Oracle Database Instance Architecture](#1-oracle-database-instance-architecture)
2. [Memory Structures](#2-memory-structures)
3. [Background Processes](#3-background-processes)
4. [Storage Architecture](#4-storage-architecture)
5. [Redo Log and Archive Log](#5-redo-log-and-archive-log)
6. [Control Files](#6-control-files)
7. [Oracle Managed Files (OMF)](#7-oracle-managed-files-omf)
8. [Fast Recovery Area (FRA)](#8-fast-recovery-area-fra)
9. [Flashback Technologies](#9-flashback-technologies)
10. [Tablespace Types](#10-tablespace-types)
11. [Oracle Multitenant Architecture](#11-oracle-multitenant-architecture)
12. [Data Dictionary Views](#12-data-dictionary-views)
13. [Alert Log](#13-alert-log)
14. [Common Architecture Anti-Patterns](#14-common-architecture-anti-patterns)

---

## 1. Oracle Database Instance Architecture

### 1.1 Instance and Database Relationship

An Oracle Database system consists of two components:

- **Instance:** A set of memory structures (SGA) and background processes (PGA)
- **Database:** A set of physical files (datafiles, control files, redo logs, parameter files)

**Key concept:** An instance must be started to access a database. The instance mounts and opens the database.

### 1.2 Instance States

| State | Description |
|---|---|
| `NOMOUNT` | Instance started; SGA allocated; background processes running; no database files accessed |
| `MOUNT` | Control files read; database structure known; datafiles not yet open |
| `OPEN` | Datafiles and redo logs opened; database available for user connections |
| `READ ONLY` | Database open but no write operations permitted |
| `RESTRICTED` | Only users with RESTRICTED SESSION privilege can connect |

### 1.3 Startup Sequence

1. `STARTUP NOMOUNT` — Reads `spfile.ora`/`pfile.ora`, allocates SGA, starts background processes
2. `ALTER DATABASE MOUNT` — Reads control files, locates datafiles and redo logs
3. `ALTER DATABASE OPEN` — Opens datafiles and online redo logs, performs instance recovery if needed

---

## 2. Memory Structures

### 2.1 System Global Area (SGA)

The SGA is a shared memory region containing:

| Component | Purpose |
|---|---|
| **Buffer Cache** | Caches data blocks read from datafiles; most significant SGA component |
| **Shared Pool** | Contains library cache (parsed SQL, execution plans) and dictionary cache (data dictionary metadata) |
| **Large Pool** | Optional; used for RMAN backups, parallel execution, and session memory |
| **Java Pool** | Memory for Java stored procedures |
| **Streams Pool** | Oracle Streams capture and apply processes |

### 2.2 Program Global Area (PGA)

Per-process memory for each server process:

- **Sort areas:** Memory for sort operations
- **Hash areas:** Memory for hash joins
- **Session variables:** User-specific data
- **Cursor state:** Open cursor information

### 2.3 Key Memory Parameters

| Parameter | Description | Monitoring Relevance |
|---|---|---|
| `SGA_TARGET` | Total SGA size (auto-tuned) | Low SGA causes excessive physical I/O |
| `PGA_AGGREGATE_TARGET` | Total PGA size (auto-tuned) | Low PGA causes disk sorts |
| `DB_CACHE_SIZE` | Buffer cache size | Directly affects hit ratio |
| `SHARED_POOL_SIZE` | Shared pool size | Small pool causes library cache contention |

---

## 3. Background Processes

### 3.1 Core Background Processes

| Process | Name | Purpose |
|---|---|---|
| **PMON** | Process Monitor | Failed process cleanup; recovers resources |
| **SMON** | System Monitor | Instance recovery; temporary segment cleanup |
| **DBWn** | Database Writer | Writes dirty blocks from buffer cache to datafiles |
| **LGWR** | Log Writer | Writes redo log buffers to online redo logs |
| **CKPT** | Checkpoint | Updates control files and datafile headers |
| **ARCn** | Archiver | Archives filled redo log files (ARCHIVELOG mode) |
| **MMON** | Manageability Monitor | Automatic Workload Repository (AWR) snapshot collection |
| **MMNL** | Manageability Monitor Light | Real-time statistics gathering |

### 3.2 Monitoring Relevance

- **DBWn activity:** High write activity may indicate buffer cache pressure
- **LGWR activity:** Redo generation rate indicates transaction volume
- **ARCn activity:** Archive log generation rate; critical for FRA capacity planning
- **PMON restarts:** Frequent process failures indicate application or database issues

---

## 4. Storage Architecture

### 4.1 Storage Hierarchy

```
Database
  └── Tablespaces
      └── Datafiles
          └── Extents
              └── Blocks
```

### 4.2 Database Block

- The smallest unit of I/O in Oracle
- Default size: 8 KB (configurable at database creation)
- Block structure: Block header + user data + free space

### 4.3 Extents and Segments

- **Extent:** A contiguous allocation of database blocks
- **Segment:** A collection of extents that stores a specific database object (table, index, etc.)
- **Storage parameters:** Initial, next, minextents, maxextents, pctincrease

### 4.4 Storage Monitoring Key Views

| View | Purpose |
|---|---|
| `DBA_DATA_FILES` | Datafile information including size and autoextend status |
| `DBA_FREE_SPACE` | Free space in tablespaces |
| `DBA_SEGMENTS` | Segment-level storage information |
| `DBA_EXTENTS` | Extent-level allocation details |
| `V$FILESTAT` | I/O statistics per datafile |

---

## 5. Redo Log and Archive Log

### 5.1 Online Redo Logs

- Record all changes to the database
- Written by LGWR in circular fashion
- Minimum two groups required; three or more recommended
- Each group can have multiple members (mirroring for redundancy)

### 5.2 ARCHIVELOG Mode

| Mode | Description | Recovery Capability |
|---|---|---|
| **NOARCHIVELOG** | Redo logs overwritten when full; no archive | Backup-only recovery |
| **ARCHIVELOG** | Filled redo logs archived before reuse | Point-in-time recovery |

### 5.3 Archive Log Monitoring

**Critical metrics for Sentinel AI:**

- Archive log generation rate (MB/hour)
- FRA usage percentage
- Archive log destination status
- Gaps in archive log sequence

**Key views:**

| View | Purpose |
|---|---|
| `V$LOG` | Online redo log groups and status |
| `V$LOGFILE` | Redo log member files |
| `V$ARCHIVED_LOG` | Archived redo log information |
| `V$RECOVERY_FILE_DEST` | FRA usage statistics |

---

## 6. Control Files

### 6.1 Purpose

The control file is a small binary file that records:

- Database name and ID
- Timestamp of database creation
- Tablespaces and datafile names
- Online redo log group information
- Archive log history
- RMAN backup information
- Checkpoint information

### 6.2 Best Practices

- Maintain at least two control files on separate disks
- Use Oracle Managed Files for control file management
- Regularly backup control file to trace

### 6.3 Monitoring

| View | Purpose |
|---|---|
| `V$CONTROLFILE` | Control file names and status |
| `V$PARAMETER` | Check `control_files` parameter |

---

## 7. Oracle Managed Files (OMF)

### 7.1 Overview

OMF automates the creation, naming, and deletion of database files, eliminating manual file management errors.

### 7.2 Configuration

| Parameter | Purpose |
|---|---|
| `DB_CREATE_FILE_DEST` | Default location for datafiles and tempfiles |
| `DB_CREATE_ONLINE_LOG_DEST_n` | Default location for online redo logs |
| `DB_RECOVERY_FILE_DEST` | Fast Recovery Area location |

### 7.3 Benefits

- Consistent file naming convention
- Automatic file placement
- Simplified file creation and deletion
- Reduced human error

### 7.4 Compliance Check

Sentinel AI check: `oracle.omf.configured` — verifies `DB_CREATE_FILE_DEST` is set to a non-null value.

---

## 8. Fast Recovery Area (FRA)

### 8.1 Purpose

The FRA is a centralized location for recovery-related files:

- Archived redo logs
- Control file autobackups
- RMAN backup pieces
- Flashback log files

### 8.2 Configuration

| Parameter | Purpose |
|---|---|
| `DB_RECOVERY_FILE_DEST` | FRA location |
| `DB_RECOVERY_FILE_DEST_SIZE` | Maximum FRA size |

### 8.3 Capacity Management

**Critical:** FRA fill-up causes database hang. Sentinel AI monitors:

- Current FRA usage percentage
- Projected fill-up date based on archive log generation rate
- Alert when usage exceeds 80%

**Key views:**

| View | Purpose |
|---|---|
| `V$RECOVERY_FILE_DEST` | Overall FRA usage |
| `V$RECOVERY_AREA_USAGE` | Breakdown by file type |

### 8.4 Compliance Check

Sentinel AI check: `oracle.fra.configured` — verifies `DB_RECOVERY_FILE_DEST` is set and has sufficient capacity.

---

## 9. Flashback Technologies

### 9.1 Flashback Database

- Uses flashback logs to revert the entire database to a past SCN/timestamp
- Requires FRA and explicit enablement
- Faster than incomplete recovery from backups

### 9.2 Flashback Query

- Queries past versions of data using `AS OF TIMESTAMP` or `AS OF SCN`
- Uses undo tablespace; no special configuration required

### 9.3 Flashback Drop

- Dropped tables move to recycle bin
- Can be recovered with `FLASHBACK TABLE ... TO BEFORE DROP`

### 9.4 Licensing Note

Flashback Database may require Oracle Database Enterprise Edition with specific options. Verify licensing before enabling.

### 9.5 Compliance Check

Sentinel AI check: `oracle.flashback.enabled` — inspects `FLASHBACK_ON` status in `V$DATABASE`.

---

## 10. Tablespace Types

### 10.1 Tablespace Classification

| Type | Purpose | Characteristics |
|---|---|---|
| **SYSTEM** | Data dictionary | Always present; never offline |
| **SYSAUX** | Auxiliary system tablespace | Supports Oracle components; reduces SYSTEM load |
| **UNDO** | Undo/rollback segments | Manages transaction undo; critical for read consistency |
| **TEMPORARY** | Sort operations, global temp tables | Not backed up; recreated at startup |
| **PERMANENT** | User data and indexes | Standard user-created tablespaces |

### 10.2 Locally Managed vs. Dictionary Managed

- **Locally Managed (recommended):** Bitmap-based space tracking in the tablespace itself
- **Dictionary Managed (deprecated):** Space tracking via data dictionary; performance issues

### 10.3 Autoextend

- Datafiles can be configured to autoextend when full
- Recommended for non-critical tablespaces with unpredictable growth
- Always set `MAXSIZE` to prevent unbounded growth

### 10.4 Monitoring

| View | Purpose |
|---|---|
| `DBA_TABLESPACES` | Tablespace status and attributes |
| `DBA_DATA_FILES` | Datafile autoextend status and maxsize |
| `DBA_FREE_SPACE` | Free space per tablespace |

---

## 11. Oracle Multitenant Architecture

### 11.1 Components

| Component | Description |
|---|---|
| **CDB (Container Database)** | The root database that hosts pluggable databases |
| **PDB (Pluggable Database)** | A portable collection of schemas that can be plugged into a CDB |
| **ROOT (CDB$ROOT)** | Contains metadata and common users |
| **SEED (PDB$SEED)** | Template for creating new PDBs |

### 11.2 Monitoring Implications

- Collection must be aware of CDB/PDB context
- Views require `CONTAINER_DATA` or `CDB_` prefix for cross-container queries
- Resource management and limits are per-PDB

### 11.3 Key Views

| View | Purpose |
|---|---|
| `V$CONTAINERS` | List of containers in the CDB |
| `CDB_TABLESPACES` | Tablespaces across all PDBs |
| `CDB_DATA_FILES` | Datafiles across all PDBs |
| `V$PDBS` | PDB status and information |

---

## 12. Data Dictionary Views

### 12.1 View Prefixes

| Prefix | Access | Purpose |
|---|---|---|
| `V$` | Dynamic performance | Real-time instance and database state |
| `DBA_` | DBA privilege | All database objects and metadata |
| `ALL_` | Current user | Objects accessible to current user |
| `USER_` | Current user | Objects owned by current user |
| `CDB_` | CDB/PDB context | Cross-container views in multitenant |

### 12.2 Key Views for Monitoring

| Category | Views |
|---|---|
| **Instance** | `V$INSTANCE`, `V$PARAMETER`, `V$SESSION` |
| **Storage** | `DBA_TABLESPACES`, `DBA_DATA_FILES`, `DBA_FREE_SPACE` |
| **Redo** | `V$LOG`, `V$LOGFILE`, `V$ARCHIVED_LOG` |
| **Performance** | `V$SYSSTAT`, `V$SYSTEM_EVENT`, `V$SESSION_WAIT` |
| **Security** | `DBA_USERS`, `DBA_ROLE_PRIVS`, `DBA_SYS_PRIVS` |
| **Backup** | `V$RECOVERY_FILE_DEST`, `V$BACKUP_PIECE` |

---

## 13. Alert Log

### 13.1 Purpose

The alert log records:

- Database startup and shutdown
- Structural changes (tablespace creation, datafile additions)
- Errors (ORA- messages)
- Checkpoint information
- Archive log status
- Block corruption detection

### 13.2 Location

- **Oracle 11g+:** XML and text format in the ADR (Automatic Diagnostic Repository)
- **Path:** `$ORACLE_BASE/diag/rdbms/<db_name>/<instance_name>/trace/alert_<instance_name>.log`

### 13.3 Sentinel AI Integration

- Alert log is parsed by the dedicated collector
- ORA- error codes are extracted and matched against rules
- Severity classification based on error code patterns

---

## 14. Common Architecture Anti-Patterns

### 14.1 Storage Anti-Patterns

| Anti-Pattern | Risk | Recommendation |
|---|---|---|
| Single datafile per tablespace | No parallel I/O; single point of failure | Multiple datafiles across disks |
| No autoextend on critical tablespaces | ORA-01653: unable to extend | Enable autoextend with MAXSIZE |
| Dictionary-managed tablespaces | Performance degradation | Use locally managed tablespaces |
| FRA on same disk as datafiles | I/O contention; recovery risk | Separate disks for FRA |

### 14.2 Redo Log Anti-Patterns

| Anti-Pattern | Risk | Recommendation |
|---|---|---|
| Two redo log groups | No redundancy during switch | Minimum three groups |
| Single member per group | Loss of member = instance hang | Multiple members on separate disks |
| Small redo log files | Frequent log switches; LGWR overhead | Size based on transaction volume |
| NOARCHIVELOG in production | No point-in-time recovery | Enable ARCHIVELOG mode |

### 14.3 Memory Anti-Patterns

| Anti-Pattern | Risk | Recommendation |
|---|---|---|
| Undersized SGA | Excessive physical I/O; poor performance | Size based on workload |
| Undersized shared pool | Library cache contention; hard parses | Monitor and tune shared pool |
| No PGA management | Disk sorts; temporary tablespace pressure | Set PGA_AGGREGATE_TARGET |

---

## References

- Oracle Database Administration Guide
- Oracle Database SQL Tuning Guide
- Oracle Secure Configuration Guide
- CIS Oracle Database Benchmark
