# Oracle Scripts

DBA-reviewed provisioning templates for GSYSTEM read-only monitoring user.

- `01_create_gsystem.sql` — Create GSYSTEM user with minimal privileges
- `02_grant_readonly_monitoring.sql` — Additional read-only grants
- `03_verify_gsystem.sql` — Verify GSYSTEM access to required views
- `04_revoke_gsystem.sql` — Emergency revoke and lock
