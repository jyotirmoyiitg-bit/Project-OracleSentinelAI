# Security Threat Model

## Assets

- Oracle database credentials (GSYSTEM)
- Collected metrics and diagnostic events
- Knowledge base documents
- AI model API keys

## Threats

1. **Credential Exposure** — GSYSTEM passwords stored in environment variables
2. **Unauthorized Access** — Unauthenticated API access
3. **SQL Injection** — Malicious input through API endpoints
4. **Prompt Injection** — Malicious documents or alert messages influencing AI
5. **Data Leakage** — Sensitive data exposed through AI responses

## Mitigations

- Secret references instead of hardcoded passwords
- OIDC/OAuth2 authentication when `AUTH_OIDC_ISSUER` is set. Local and Active Directory sign-in remain available.
- Parameterized queries only
- Named read-only tools, no free-form SQL
- Document quarantine for corrupted files
- Audit trails for all operations
