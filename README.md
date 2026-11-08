# Oracle Sentinel AI

Central, browser-based platform for monitoring registered Oracle databases on Windows Server without installing a custom agent on each database server.

## Quick Start

1. Copy `.env.example` to `.env` and configure your settings
2. Start infrastructure: `docker compose up -d`
3. Install dependencies: `cd backend && pip install -e ".[dev]"`
4. Run the app: `uvicorn sentinel.main:app --reload`

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
