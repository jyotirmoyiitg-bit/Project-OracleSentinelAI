-- ============================================================================
-- Oracle Sentinel AI — GSYSTEM Read-Only Monitoring Grants
-- ============================================================================
-- Additional grants for GSYSTEM if needed. Run as SYSDBA.
-- ============================================================================

-- Grant SELECT ANY DICTIONARY if specific view grants are insufficient
-- WARNING: This is broader than needed — use only if specific grants fail
-- GRANT SELECT ANY DICTIONARY TO GSYSTEM;

-- Grant access to additional views if needed
GRANT SELECT ON v$session TO GSYSTEM;
GRANT SELECT ON v$sysstat TO GSYSTEM;
GRANT SELECT ON v$sysmetric TO GSYSTEM;
