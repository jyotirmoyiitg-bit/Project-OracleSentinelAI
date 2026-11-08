-- ============================================================================
-- Oracle Sentinel AI — GSYSTEM Read-Only Monitoring Grants
-- ============================================================================
-- Additional grants for GSYSTEM if needed. Run as SYSDBA.
-- ============================================================================

-- Grant SELECT ANY DICTIONARY if specific view grants are insufficient
-- WARNING: This is broader than needed — use only if specific grants fail
-- GRANT SELECT ANY DICTIONARY TO GSYSTEM;

-- Grant access to additional views if needed
-- NOTE: Use SYS.V_$ not v$ — direct GRANT on v$ fails with ORA-02030.
GRANT SELECT ON SYS.V_$SESSION TO GSYSTEM;
GRANT SELECT ON SYS.V_$SYSSTAT TO GSYSTEM;
GRANT SELECT ON SYS.V_$SYSMETRIC TO GSYSTEM;
