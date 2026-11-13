# Configuration and Knowledge Intake

**Status:** Non-secret application settings are stored in PostgreSQL and an administrator can edit the safe ones from the portal. Oracle 19c manuals are on disk under `knowledge-intake/ora-19c-manual`. The indexer scans that folder and `Policy/`. A separate vector database was not added.<br>
**Previous phase:** [Phase 9 handoff](./Progress_Summary_Phase9.md)<br>
**How to run:** [How_To_Run_Sentinel.html](../How_To_Run_Sentinel.html)<br>
**API contract:** [docs/api-contracts.md](../docs/api-contracts.md)

Migration head is `0010`. It adds `app_config`. The local database was upgraded `0009 -> 0010` on 13 Nov 2026. Privileged execution stays disabled, and `privileged_operations` is still not written.

## Knowledge intake

The indexer no longer walks `knowledge-intake/Knowledge`. That folder was removed. Approved manuals live in `knowledge-intake/ora-19c-manual` and are stored with the Knowledge category. `knowledge-intake/Policy` is unchanged.

Downloaded into `ora-19c-manual`:

| File | Pages |
|---|---|
| database-concepts.pdf | 702 |
| database-performance-tuning-guide.pdf | 380 |
| data-guard-concepts-and-administration.pdf | 438 |
| database-licensing-information-user-manual.pdf | 459 |
| database-pl-sql-language-reference.pdf | 834 |
| automatic-storage-management-administrators-guide.pdf | 826 |
| oracle-ai-database-backup-and-recovery-users-guide.pdf | 971 |
| database-security-guide.pdf | 1,195 |
| database-administrators-guide.pdf | 1,647 |
| sql-language-reference.pdf | 2,192 |
| database-reference.pdf | 2,487 |
| database-error-messages.pdf | 5,778 |

`KNOWLEDGE_MAX_PAGES` is 800. A reindex can accept Concepts, Performance Tuning, Data Guard, and Licensing. The other eight guides would be quarantined and moved out of the folder. The parser timeout remains 30 seconds. Embeddings stay in PostgreSQL `document_chunks.embedding` through pgvector. The default provider is the local hasher `local-hashing-v1` at 128 dimensions. Search still combines that vector with full-text ranking and does not require a language model.

The same scan has three triggers. An administrator can reindex from the Knowledge page or `POST /api/v1/knowledge/reindex` while `KNOWLEDGE_ENABLED=false`. The Phase 5 and Phase 6 live scripts already call that reindex. `KNOWLEDGE_ENABLED=true` starts a repeating scan. The first scheduled scan waits for `KNOWLEDGE_SCAN_INTERVAL_SECONDS`.

## Configuration table

Startup reads `.env` and inserts any missing non-secret setting into `app_config`. A value already stored is left in place, so an administrator edit survives a restart. Passwords, API keys, `secret://` references, `DATABASE_URL`, `REDIS_URL`, and `SECRET_KEY` are not written to the table.

`GET /api/v1/admin/config` lists the stored settings with a section and an editable flag. `PUT /api/v1/admin/config/{key}` changes one editable setting. The permission is `config.write`, which only the administrator (`admin` / `platform_admin`) has. The change is audited as `config.update` without storing a secret. The running API uses the saved value. The collector and the knowledge scan keep the timer they started with until the API process restarts.

The Administration page groups the settings into Application, Database, Oracle, Collection, Alerts, Language model, Knowledge, Mail, Sign-in, Operations, and Logging. A section stays closed. **Show** opens it. The values stay read-only until **Edit**, and **Save** writes that section. A row marked Fixed cannot be saved.

These settings stay fixed because a portal edit would change deployment identity, a connection target, a filesystem path, or a safety boundary:

- Application and database pool: name, version, environment, debug, CORS origins, pool size, and pool overflow.
- Oracle identity: `tns_admin` and the monitoring username. Connection and statement timeouts can be edited.
- Safety switches: synthetic mode, `ai_fallback_provider`, and all three privileged-execution flags.
- Files and embeddings: rule path, knowledge folder, malware scanner, embedding provider, model, the 128-dimension width, and remote embedding approval.
- Sign-in boundary: whether sign-in is required, the secure-cookie flag, the bootstrap account, Active Directory, and OIDC. Session length can be edited.

Collection intervals, alert windows, model names and limits, knowledge scan limits, mail delivery, proposal expiry, and logging can be saved.

## Launchers

`test_real_phase8` and `test_real_phase9` now expect Alembic head `0010` and the `app_config` table. They still require an empty `privileged_operations` table. Those full Oracle launchers were not run for this change.

## Verification

- `backend/tests/config/test_store.py` and `backend/tests/api/test_config.py`: secrets are omitted, an edited value survives the next startup sync, a fixed setting is rejected, and an administrator can save `log_level` while a DBA cannot.
- `backend/tests/test_migrations.py` expects head `0010` and `app_config`.
- `frontend/src/AdminPage.test.tsx`: open Logging, edit `log_level`, and save. **1 passed**.
- The full backend suite was not rerun.

## Resume / Next-Agent Pickup

1. Restart the API after pulling these edits so the Administration page and `app_config` seed run.
2. Do not raise `KNOWLEDGE_MAX_PAGES` until the larger manuals are meant to be indexed. A reindex above the limit quarantines the file.
3. Do not store a password or a `secret://` value in `app_config`. A vault provider is not implemented. `SECRETS_PROVIDER` is still the process environment.
4. Do not add an executor. Privileged execution stays off.
5. These edits are uncommitted. Commit only when requested.
