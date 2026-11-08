# Operations Runbook

## Deployment

1. Start infrastructure: `docker compose up -d`
2. Configure `.env` with database and Oracle settings
3. Install backend: `cd backend && pip install -e ".[dev]"`
4. Run migrations: `alembic upgrade head`
5. Start app: `uvicorn sentinel.main:app --host 0.0.0.0 --port 8000`

## Health Checks

- Liveness: `GET /health/live`
- Readiness: `GET /health/ready`

## Adding a Database Target

1. Register via API: `POST /api/v1/databases`
2. Test connection: `POST /api/v1/databases/{id}/test-connection`
3. Verify metrics: `GET /api/v1/databases/{id}/metrics`

## Troubleshooting

- Check logs: `docker compose logs -f`
- Database connection: verify `DATABASE_URL` in `.env`
- Oracle connection: verify `TNS_ADMIN` and TNS alias
