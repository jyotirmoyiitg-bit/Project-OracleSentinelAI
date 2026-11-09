# Oracle Sentinel AI

Central, browser-based platform for monitoring registered Oracle databases on Windows Server without installing a custom agent on each database server.

## Quick Start

From the repository root in PowerShell:

1. Copy `.env.example` to `.env` if you do not already have one. Configure PostgreSQL,
   `TNS_ADMIN` (prefer an absolute path), and the read-only Oracle monitoring credential.
2. Start infrastructure: `docker compose up -d postgres redis`
3. Install backend: `.\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"`
4. Apply migrations: `.\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head`
5. Set `SCHEDULER_ENABLED=true` in your local configuration to enable collection.
6. Run backend: `.\.venv\Scripts\python.exe -m uvicorn sentinel.main:app --host 127.0.0.1 --port 8000`
7. In another terminal: `cd frontend; npm ci; npm run dev`
8. Open `http://localhost:5173`. Register targets through `http://localhost:8000/docs`.

For an existing Phase 1 database, read the migration instructions before upgrading.
See [the operations runbook](docs/operations-runbook.md) for credential-free synthetic
validation, polling profiles, and recovery checks. Collection does not require an LLM.

Authentication/entitlements are a later phase; keep this development deployment on
localhost or a trusted isolated network.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full architecture overview.

## Phases

- [Phase 0: Bootstrap](Phase_0_Bootstrap.md)
- [Phase 1: Oracle Connection](Phase_1_Oracle_Connection.md)
- [Phase 2: Central Collection](Phase_2_Central_Collection.md)
- [Phase 3: Alert-Log, Rules, Incidents](Phase_3_AlertLog_Rules_Incidents.md)
- [Phase 4: Compliance & Forecasting](Phase_4_Compliance_Forecasting.md)
- [Phase 5: Knowledge & RAG](Phase_5_Knowledge_RAG.md)
- [Phase 6: AI Copilot](Phase_6_AI_Copilot.md)
- [Phase 7: Operations, Auth, Audit](Phase_7_Operations_Auth_Audit.md)
- [Phase 8: Privileged Operations](Phase_8_Privileged_Operations.md)

## License

Proprietary
