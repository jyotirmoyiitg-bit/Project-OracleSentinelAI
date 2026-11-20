# Internal Policy 101 — Oracle Sentinel AI

**Document ID:** POL-INT-101  
**Version:** 1.0  
**Effective Date:** 2026-11-10  
**Classification:** Internal — Confidential  
**Owner:** Platform Engineering / Database Operations  
**Review Cycle:** Annual or upon significant regulatory change

---

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Database Security Policy](#2-database-security-policy)
3. [Access Control and Authentication Policy](#3-access-control-and-authentication-policy)
4. [Data Classification and Handling Policy](#4-data-classification-and-handling-policy)
5. [Data Retention and Disposal Policy](#5-data-retention-and-disposal-policy)
6. [Change Management Policy](#6-change-management-policy)
7. [Incident Response and Escalation Policy](#7-incident-response-and-escalation-policy)
8. [Backup and Disaster Recovery Policy](#8-backup-and-disaster-recovery-policy)
9. [Audit and Compliance Monitoring Policy](#9-audit-and-compliance-monitoring-policy)
10. [AI and Machine Learning Usage Policy](#10-ai-and-machine-learning-usage-policy)
11. [Network and Connectivity Policy](#11-network-and-connectivity-policy)
12. [Vulnerability Management Policy](#12-vulnerability-management-policy)
13. [Third-Party and Supply Chain Policy](#13-third-party-and-supply-chain-policy)
14. [Privileged Operations Policy](#14-privileged-operations-policy)

---

## 1. Purpose and Scope

### 1.1 Purpose

This document establishes the internal policies governing the operation, security, and compliance of the Oracle Sentinel AI platform. It aligns with industry frameworks including **NIST SP 800-53**, **ISO/IEC 27001:2022**, **CIS Controls v8**, and **Oracle Secure Configuration Guides**.

### 1.2 Scope

These policies apply to:

- All Oracle database instances monitored by Oracle Sentinel AI
- The Oracle Sentinel AI platform infrastructure (backend, frontend, application database, cache, and vector store)
- All personnel with access to the platform, including DBAs, operators, platform administrators, and security auditors
- All data collected, processed, stored, or transmitted by the platform
- AI/ML components, including RAG pipelines, embedding models, and LLM integrations

### 1.3 Regulatory Alignment

| Framework | Relevance |
|---|---|
| NIST SP 800-53 Rev. 5 | Security and privacy controls for federal information systems |
| ISO/IEC 27001:2022 | Information security management system requirements |
| CIS Controls v8 | Best practices for cybersecurity defense |
| SOC 2 Type II | Trust service criteria for security and availability |
| GDPR / CCPA | Data privacy and subject rights (where applicable) |
| PCI DSS v4.0 | Payment card data protection (if monitoring PCI-scoped databases) |
| HIPAA Security Rule | Protected health information (if monitoring healthcare databases) |

---

## 2. Database Security Policy

### 2.1 Read-Only Monitoring Principle

**Policy:** All baseline monitoring connections to Oracle databases must use read-only credentials. The `GSYSTEM` monitoring user is provisioned with the minimum privileges required for collection and must never be granted DDL, DML, or administrative privileges.

**Rationale:** CIS Control 4 (Secure Configuration), NIST AC-6 (Least Privilege)

**Implementation:**
- `GSYSTEM` is created with `CONNECT` and `RESOURCE` roles only
- Additional `SELECT` grants are scoped to specific data dictionary views required for collection
- No `SYSDBA`, `SYSOPER`, or `SYSKM` privileges are granted to monitoring accounts
- Privilege escalation attempts are logged and trigger immediate alerts

### 2.2 Oracle Managed Files (OMF)

**Policy:** Production databases must use Oracle Managed Files (OMF) for datafile, tempfile, and control file management unless a documented exception is approved.

**Rationale:** Oracle best practice; reduces human error in file management

**Compliance Check:** `oracle.omf.configured` — verifies `DB_CREATE_FILE_DEST` is set

### 2.3 ARCHIVELOG Mode

**Policy:** Production databases must operate in ARCHIVELOG mode to support point-in-time recovery.

**Rationale:** NIST CP-9 (System Recovery), Oracle best practice for data protection

**Compliance Check:** `oracle.archivelog.enabled` — inspects database log mode

### 2.4 Fast Recovery Area (FRA)

**Policy:** Production databases must configure a Fast Recovery Area with sufficient capacity for at least 7 days of archived redo logs.

**Rationale:** Ensures recovery point objective (RPO) compliance

**Compliance Check:** `oracle.fra.configured` — verifies `DB_RECOVERY_FILE_DEST` and capacity

### 2.5 Flashback

**Policy:** Flashback Database may be enabled on production systems after DBA review of recovery-area capacity and licensing requirements.

**Rationale:** Provides additional recovery granularity; licensing implications must be assessed

**Compliance Check:** `oracle.flashback.enabled` — inspects `FLASHBACK_ON` status

### 2.6 Oracle Licensing Compliance

**Policy:** No AWR, ASH, or ADDM queries are executed unless the target database has an explicit license entitlement documented and approved.

**Rationale:** Oracle Diagnostic Pack and Tuning Pack require separate licensing

**Implementation:**
- Licensing status is tracked per database in the platform configuration
- Queries requiring licensed features are gated by the licensing flag
- Attempting to use unlicensed features generates an audit event

---

## 3. Access Control and Authentication Policy

### 3.1 Authentication

**Policy:** All access to the Oracle Sentinel AI platform must authenticate via enterprise OIDC/OAuth2. Local authentication is permitted only in development environments and must be disabled in production.

**Rationale:** NIST IA-2 (Identification and Authentication), CIS Control 6 (Access Control Management)

**Implementation:**
- Enterprise identity provider (IdP) integration via standard OIDC flow
- Multi-factor authentication (MFA) required for all non-viewer roles
- Session timeout: 30 minutes of inactivity for operator and above roles
- Session timeout: 4 hours for viewer role

### 3.2 Role-Based Access Control (RBAC)

**Policy:** Access is granted based on the principle of least privilege through predefined roles.

**Rationale:** NIST AC-2 (Access Control), CIS Control 5 (Account Management)

| Role | Permissions | MFA Required |
|---|---|---|
| `viewer` | Read-only dashboards and reports | No |
| `operator` | Acknowledge incidents, view proposals | Yes |
| `dba` | Create proposals, manage database configurations | Yes |
| `platform_admin` | Full platform administration | Yes |
| `security_auditor` | Read-only access to audit trails and compliance reports | Yes |

### 3.3 Per-Database Entitlements

**Policy:** Users are entitled to specific databases and environments. Access to a database in one environment (e.g., development) does not grant access to the same database in another environment (e.g., production).

**Rationale:** NIST AC-3 (Access Enforcement), segregation of duties

### 3.4 Credential Management

**Policy:** All database credentials stored by the platform must be encrypted at rest using AES-256. Credentials must be rotated at least every 90 days or immediately upon suspected compromise.

**Rationale:** NIST IA-5 (Authenticator Management), CIS Control 5

**Implementation:**
- Credentials are stored encrypted in the application database
- No plaintext credentials in configuration files or environment variables
- Automated rotation reminders are generated 14 days before expiry

---

## 4. Data Classification and Handling Policy

### 4.1 Classification Levels

**Policy:** All data handled by the platform is classified into one of four levels:

| Level | Description | Examples |
|---|---|---|
| **Public** | Information approved for unrestricted disclosure | Platform documentation, public status pages |
| **Internal** | Information for internal use only | Monitoring metrics, performance data, compliance reports |
| **Confidential** | Sensitive information requiring access control | Database credentials, connection strings, audit logs |
| **Restricted** | Highly sensitive data with strict handling requirements | PII in monitored databases, financial data, health records |

### 4.2 Data in Transit

**Policy:** All data transmitted between components must be encrypted using TLS 1.2 or higher.

**Rationale:** NIST SC-8 (Transmission Confidentiality), CIS Control 13 (Network Monitoring)

**Implementation:**
- HTTPS for all API endpoints
- TLS for database connections (Oracle wallets or equivalent)
- Certificate pinning for production deployments

### 4.3 Data at Rest

**Policy:** All data stored by the platform must be encrypted at rest.

**Rationale:** NIST SC-28 (Protection of Information at Rest)

**Implementation:**
- PostgreSQL TDE (Transparent Data Encryption) or filesystem-level encryption
- Encrypted backups
- AES-256 for credential storage

### 4.4 Data Minimization

**Policy:** The platform collects only the data necessary for monitoring, alerting, and compliance. No application data (user tables, business data) is collected unless explicitly configured and approved.

**Rationale:** NIST PR-3 (Data Minimization), GDPR Article 5(1)(c)

**Implementation:**
- Collection queries target data dictionary views only
- No `SELECT *` patterns in collectors
- Explicit allowlist of views and tables per collector

---

## 5. Data Retention and Disposal Policy

### 5.1 Retention Periods

**Policy:** Data is retained for the minimum period required by operational and regulatory needs.

| Data Type | Retention Period | Disposal Method |
|---|---|---|
| Collection metrics | 90 days (rolling) | Secure deletion |
| Alert log entries | 1 year | Secure deletion |
| Incidents and resolutions | 3 years | Secure deletion |
| Compliance evaluation results | 3 years | Secure deletion |
| Audit trail entries | 7 years | Secure deletion |
| Forecasting models and data | 1 year | Secure deletion |
| RAG document embeddings | Until source document is removed | Secure deletion + vector index cleanup |
| Knowledge documents | Until explicitly removed by owner | Secure deletion |

**Rationale:** NIRMA (NARA) records schedules, ISO 27001 A.5.13 (Logging)

### 5.2 Secure Disposal

**Policy:** When data reaches its retention period, it must be securely deleted using methods that prevent recovery.

**Rationale:** NIST MP-6 (Media Sanitization)

**Implementation:**
- Database rows are deleted and transaction logs are purged per retention policy
- Vector embeddings are removed from pgvector indexes
- Backups containing expired data are rotated out

### 5.3 Data Subject Rights

**Policy:** Where applicable (GDPR, CCPA), the platform supports data subject access requests and right-to-erasure for any personal data collected during monitoring.

**Rationale:** GDPR Articles 15, 17; CCPA Section 1798.100, 1798.105

---

## 6. Change Management Policy

### 6.1 Change Classification

**Policy:** All changes to the platform or monitored databases are classified and processed according to their risk level.

| Type | Description | Approval Required |
|---|---|---|
| **Standard** | Pre-approved, low-risk, repeatable changes | Peer review |
| **Normal** | Changes requiring assessment and approval | Change advisory board (CAB) |
| **Emergency** | Urgent changes to resolve critical incidents | Emergency CAB (post-facto documentation) |

### 6.2 Operation Proposals

**Policy:** Database changes proposed through the platform are proposal-only in Release 1. No automatic DDL execution is permitted.

**Rationale:** NIST CM-3 (Configuration Change Management), separation of duties

**Implementation:**
- Proposals include: target database, intended SQL, impact assessment, and rollback plan
- DBA approval with 24-hour expiry
- All proposals are audited regardless of outcome

### 6.3 Policy Versioning

**Policy:** Compliance policy definitions are versioned and require review and approval before deployment to production.

**Rationale:** NIST CM-2 (Baseline Configuration), CIS Control 4

**Implementation:**
- Policy YAML files are version-controlled in the repository
- Changes require pull request review
- Production policy updates require explicit approval status

### 6.4 Rollback Procedures

**Policy:** Every change must have a documented rollback procedure that can be executed within the defined recovery time objective (RTO).

**Rationale:** NIST CP-2 (Recovery Plan)

---

## 7. Incident Response and Escalation Policy

### 7.1 Severity Classification

**Policy:** Incidents detected by the platform are classified by severity to determine response priority and escalation path.

| Severity | Response Time | Escalation | Examples |
|---|---|---|---|
| **Critical** | 15 minutes | Immediate: on-call DBA → manager | Database down, data corruption, security breach |
| **High** | 1 hour | 2 hours: on-call DBA → manager | Tablespace full, ARCHIVELOG failure, authentication failure |
| **Medium** | 4 hours | 8 hours: on-call DBA | High wait events, slow-growing tablespaces, compliance failures |
| **Low** | 24 hours | Next business day | Informational alerts, forecast warnings, non-critical compliance gaps |

**Rationale:** NIST IR-4 (Incident Handling), ITIL incident management

### 7.2 Incident Lifecycle

**Policy:** All incidents follow a structured lifecycle:

1. **Detection** — Automated rule evaluation or manual report
2. **Triage** — Severity assignment and initial classification
3. **Acknowledgment** — On-call operator acknowledges and begins investigation
4. **Investigation** — Root cause analysis using platform data and AI-assisted diagnostics
5. **Resolution** — Fix applied and verified
6. **Closure** — Incident closed with resolution notes
7. **Post-Incident Review** — For critical and high severity: lessons learned documented within 5 business days

**Rationale:** NIST IR-4, ITIL incident management

### 7.3 Communication

**Policy:** Stakeholders are notified according to severity and impact:

- **Critical:** Immediate notification to on-call team and management
- **High:** Notification within 1 hour to affected teams
- **Medium:** Notification via dashboard and email digest
- **Low:** Dashboard-only notification

### 7.4 Evidence Preservation

**Policy:** All incident-related data, including collection metrics, alert log entries, and AI-assisted analysis, is preserved for post-incident review and compliance auditing.

**Rationale:** NIST IR-6 (Incident Reporting), regulatory requirements

---

## 8. Backup and Disaster Recovery Policy

### 8.1 Backup Evidence

**Policy:** The platform tracks backup freshness as a compliance indicator. A database is considered compliant if evidence of a successful backup exists within the defined threshold.

**Rationale:** NIST CP-9 (System Recovery), CIS Control 11

**Compliance Check:** `oracle.backup.freshness` — evaluates backup evidence when a supported source is configured

### 8.2 Recovery Objectives

**Policy:** Recovery objectives are defined per environment:

| Environment | RPO (Recovery Point Objective) | RTO (Recovery Time Objective) |
|---|---|---|
| Production | 1 hour | 4 hours |
| Staging | 4 hours | 8 hours |
| Development | 24 hours | 24 hours |

### 8.3 Platform Data Backup

**Policy:** The application database (PostgreSQL) containing collection data, incidents, compliance results, and audit trails is backed up daily with point-in-time recovery capability.

**Rationale:** NIST CP-9, CIS Control 11

**Implementation:**
- Daily full backups with WAL archiving for PITR
- Offsite backup storage
- Quarterly restore testing

---

## 9. Audit and Compliance Monitoring Policy

### 9.1 Audit Trail

**Policy:** An immutable audit trail records all significant platform activities.

**Rationale:** NIST AU-2 (Audit Events), ISO 27001 A.8.15 (Logging)

**Audited Events:**
- Authentication and authorization events (login, logout, role changes)
- Configuration changes (database additions, rule modifications, policy updates)
- Incident lifecycle events (creation, acknowledgment, resolution, closure)
- Operation proposals (creation, approval, rejection, expiry)
- Knowledge document ingestion and removal
- Compliance evaluation results
- AI-assisted query execution and tool usage

### 9.2 Audit Log Protection

**Policy:** Audit logs are write-once and tamper-evident. Only security auditors and platform administrators with explicit authorization can access audit logs.

**Rationale:** NIST AU-9 (Protection of Audit Information)

**Implementation:**
- Audit table uses append-only design
- Cryptographic hashing chain for tamper detection
- Access to audit logs generates its own audit entry

### 9.3 Compliance Evaluation Schedule

**Policy:** Compliance policies are evaluated on a scheduled basis:

| Policy Category | Evaluation Frequency |
|---|---|
| Database configuration (OMF, ARCHIVELOG, FRA, Flashback) | Every collection cycle |
| Backup freshness | Daily |
| Access control review | Monthly |
| Credential rotation check | Weekly |
| Full compliance report generation | Monthly |

### 9.4 Regulatory Reporting

**Policy:** Compliance reports are generated and distributed to stakeholders on a scheduled basis. Reports include pass/fail status, evidence, and recommended actions.

**Rationale:** NIST PM-9 (Risk Assessment), ISO 27001 A.5.3 (Threat Intelligence)

---

## 10. AI and Machine Learning Usage Policy

### 10.1 Monitoring Independence from AI

**Policy:** Core monitoring functions — collection, rule evaluation, incident generation, and alerting — must never depend on an LLM or AI service. If AI is disabled or unavailable, monitoring continues without degradation.

**Rationale:** System reliability, NIST SC-5 (Concurrent Session Control)

**Implementation:**
- AI components are optional and independently toggleable
- No AI dependency in the collection or rules pipeline
- Graceful degradation when AI services are unavailable

### 10.2 AI-Assisted Diagnostics

**Policy:** AI-assisted features (RAG search, Copilot queries, root cause analysis) are advisory only. AI-generated recommendations must not be executed without human review and approval.

**Rationale:** NIST AI RMF (Governance), responsible AI principles

**Implementation:**
- All AI responses include source citations and confidence indicators
- No automatic execution of AI-generated SQL or commands
- User must explicitly approve any action suggested by AI

### 10.3 Data Privacy in AI Processing

**Policy:** Sensitive data extracted from monitored databases is not sent to external AI services. Local embedding models and LLMs are preferred; if external services are used, data is anonymized and minimized.

**Rationale:** GDPR Article 28 (Processing by Controllers), NIST PR-3 (Data Minimization)

**Implementation:**
- Local embedding models run within the platform infrastructure
- External LLM calls exclude credentials, PII, and restricted data
- Data sent to external services is logged in the audit trail

### 10.4 Model Governance

**Policy:** AI models used by the platform are tracked, versioned, and periodically evaluated for accuracy and bias.

**Rationale:** NIST AI RMF (Measure, Manage), responsible AI

**Implementation:**
- Model version and configuration are recorded
- Forecasting models include confidence scoring
- Models with insufficient data return `unknown` rather than speculative results

### 10.5 RAG Document Integrity

**Policy:** Documents ingested into the RAG knowledge base are validated for integrity and authorization before processing.

**Rationale:** NIST SI-7 (Software, Firmware, and Information Integrity)

**Implementation:**
- SHA-256 hash verification for change detection
- File type and size validation
- Quarantine for corrupted or unauthorized documents
- Source attribution preserved in all embeddings

---

## 11. Network and Connectivity Policy

### 11.1 Network Segmentation

**Policy:** The platform is deployed in a segregated network zone with controlled access to monitored Oracle databases.

**Rationale:** NIST SC-7 (Boundary Protection), CIS Control 12

**Implementation:**
- Platform components communicate over internal network only
- API endpoints are exposed through a reverse proxy with rate limiting
- Direct database access from the internet is prohibited

### 11.2 Connection Security

**Policy:** All connections to Oracle databases use encrypted channels.

**Rationale:** NIST SC-8 (Transmission Confidentiality)

**Implementation:**
- Oracle SQL*Net encryption for database connections
- TLS for all HTTP/HTTPS communication
- Certificate validation enabled for all connections

### 11.3 Firewall Rules

**Policy:** Firewall rules follow the principle of least privilege, allowing only the minimum required ports and protocols.

| Direction | Protocol | Port | Purpose |
|---|---|---|---|
| Inbound | HTTPS | 443 | API and web portal access |
| Outbound | Oracle Net | 1521 (or configured) | Database collection connections |
| Outbound | PostgreSQL | 5432 | Application database access |
| Outbound | Redis | 6379 | Cache and queue access |
| Outbound | Ollama | 11434 (optional) | Local LLM inference |

---

## 12. Vulnerability Management Policy

### 12.1 Dependency Scanning

**Policy:** All third-party dependencies are scanned for known vulnerabilities before deployment.

**Rationale:** NIST SI-2 (Flaw Remediation), CIS Control 3

**Implementation:**
- Automated dependency scanning in CI/CD pipeline
- Critical and high vulnerabilities must be remediated within 7 days
- Medium vulnerabilities must be remediated within 30 days
- Low vulnerabilities are tracked and addressed in regular maintenance

### 12.2 Patch Management

**Policy:** Platform components are kept up to date with security patches.

| Component | Patch Frequency |
|---|---|
| Python runtime and dependencies | Monthly security updates |
| PostgreSQL | As released by vendor |
| Oracle client libraries | As released by Oracle |
| Operating system | Monthly or as required |

### 12.3 Oracle Patch Tracking

**Policy:** The platform tracks Oracle database patch levels and alerts when critical patches are missing.

**Rationale:** NIST SI-2 (Flaw Remediation)

**Implementation:**
- Collection includes Oracle version and patch level information
- Rules evaluate patch compliance against defined baselines
- Alerts generated for databases missing critical patches

---

## 13. Third-Party and Supply Chain Policy

### 13.1 Software Bill of Materials (SBOM)

**Policy:** A Software Bill of Materials is maintained for all platform components, listing all third-party dependencies with versions and licenses.

**Rationale:** NIST SP 800-218 (SSDF), Executive Order 14028

**Implementation:**
- SBOM generated during build process
- License compliance checked for all dependencies
- SBOM stored with each release artifact

### 13.2 Third-Party Service Evaluation

**Policy:** Before integrating any third-party service (e.g., external LLM providers, cloud storage), a security assessment is conducted.

**Rationale:** NIST SA (Supply Chain Risk Management)

**Assessment Criteria:**
- Data handling and privacy practices
- Security certifications (SOC 2, ISO 27001)
- Data residency and sovereignty
- Service level agreements
- Incident response capabilities

### 13.3 Open Source Governance

**Policy:** Open source components are evaluated for security, license compatibility, and maintenance status before inclusion.

**Rationale:** CIS Control 3, NIST SA (Supply Chain)

**Criteria:**
- Active maintenance (commits within last 12 months)
- No known critical vulnerabilities
- Compatible license (MIT, Apache 2.0, BSD preferred)
- Community support and documentation quality

---

## 14. Privileged Operations Policy

### 14.1 Disabled by Default

**Policy:** Privileged operation execution is disabled by default in Release 1. No DDL, DML, or administrative commands are executed by the platform without explicit human intervention outside the platform.

**Rationale:** NIST AC-6 (Least Privilege), CIS Control 4

**Implementation:**
- `OPERATIONS_EXECUTION_ENABLED=false` in all environments
- All execution endpoints return HTTP 501 when disabled
- Clear indication that operations are proposal-only

### 14.2 Separation of Duties

**Policy:** When privileged operations are enabled in future releases, separation of duties is enforced: the person who proposes a change cannot be the sole approver.

**Rationale:** NIST AC-3 (Access Enforcement), SOX compliance

**Implementation:**
- Dual approval workflow for production changes
- Approver must have a different role than the proposer
- Approval expiry to prevent stale authorizations

### 14.3 Pre-Flight Validation

**Policy:** Before any privileged operation is approved, automated pre-flight checks validate:

- Target database and environment correctness
- Storage availability for space-related operations
- Maintenance window compliance
- Impact assessment on dependent systems

**Rationale:** NIST CM-3 (Configuration Change Management)

### 14.4 Audit of Privileged Access

**Policy:** All attempts to access or execute privileged operations are audited, regardless of success or failure.

**Rationale:** NIST AU-2 (Audit Events)

**Audited Data:**
- Requester identity and role
- Target database and operation type
- Parameters and intended SQL
- Approval chain and timestamps
- Execution result and any errors

---

## Appendix A — Policy Reference Matrix

| Policy ID | Policy Name | Related Compliance Check | Framework Mapping |
|---|---|---|---|
| POL-INT-101.2 | Database Security | `oracle.omf.configured`, `oracle.archivelog.enabled`, `oracle.fra.configured`, `oracle.flashback.enabled` | NIST AC-6, CIS Control 4 |
| POL-INT-101.3 | Access Control | RBAC enforcement, credential rotation | NIST IA-2, IA-5, AC-2 |
| POL-INT-101.4 | Data Classification | Data minimization in collectors | NIST PR-3, GDPR Art. 5 |
| POL-INT-101.5 | Data Retention | Automated retention enforcement | NIST MP-6, ISO 27001 A.5.13 |
| POL-INT-101.6 | Change Management | Operation proposals, policy versioning | NIST CM-2, CM-3 |
| POL-INT-101.7 | Incident Response | Severity classification, lifecycle | NIST IR-4, ITIL |
| POL-INT-101.8 | Backup and DR | `oracle.backup.freshness` | NIST CP-9, CIS Control 11 |
| POL-INT-101.9 | Audit and Compliance | Audit trail, compliance evaluation | NIST AU-2, AU-9 |
| POL-INT-101.10 | AI Usage | AI independence, data privacy | NIST AI RMF |
| POL-INT-101.11 | Network Security | Connection encryption, firewall rules | NIST SC-7, SC-8 |
| POL-INT-101.12 | Vulnerability Management | Dependency scanning, patch tracking | NIST SI-2, CIS Control 3 |
| POL-INT-101.13 | Third-Party | SBOM, service evaluation | NIST SA, SP 800-218 |
| POL-INT-101.14 | Privileged Operations | Execution disabled, separation of duties | NIST AC-3, AC-6 |

---

## Appendix B — Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-11-10 | Platform Engineering | Initial release |

---

## Appendix C — Approval

| Role | Name | Signature | Date |
|---|---|---|---|
| Platform Engineering Lead | | | |
| Security Officer | | | |
| Database Operations Manager | | | |
| Compliance Officer | | | |

---

*This document is intended for ingestion by the Oracle Sentinel AI RAG pipeline. It serves as a reference for compliance evaluation, AI-assisted diagnostics, and policy-aware incident response.*
