# API Contracts

## Database Registry

### List Databases
- `GET /api/v1/databases?enabled_only=true`
- Returns: `list[DatabaseTargetResponse]`

### Register Database
- `POST /api/v1/databases`
- Body: `DatabaseTargetCreate`
- Returns: `DatabaseTargetResponse` (201)

### Get Database
- `GET /api/v1/databases/{target_id}`
- Returns: `DatabaseTargetResponse`

### Update Database
- `PUT /api/v1/databases/{target_id}`
- Body: `DatabaseTargetUpdate`
- Returns: `DatabaseTargetResponse`

### Delete Database
- `DELETE /api/v1/databases/{target_id}`
- Returns: 204 No Content

### Test Connection
- `POST /api/v1/databases/{target_id}/test-connection`
- Body: `ConnectionTestRequest` (optional)
- Returns: `ConnectionTestResponse`

### Get Metrics
- `GET /api/v1/databases/{target_id}/metrics?limit=100`
- Returns: `list[ObservationResponse]`

## Health

### Liveness
- `GET /health/live`
- Returns: `{"status": "alive"}`

### Readiness
- `GET /health/ready`
- Returns: `{"status": "ready", "database": "connected"}`
