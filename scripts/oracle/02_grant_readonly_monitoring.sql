-- ============================================================================
-- Oracle Sentinel AI — GSYSTEM Read-Only Monitoring Grants
-- ============================================================================
-- Additional grants for GSYSTEM if needed. Run as SYSDBA.
-- ============================================================================

-- Grant SELECT ANY DICTIONARY if specific view grants are insufficient
-- WARNING: This is broader than needed — use only if specific grants fail
-- GRANT SELECT ANY DICTIONARY TO GSYSTEM;

-- Grant access to additional views if needed
<<<<<<< HEAD
-- NOTE: Use SYS.V_$ not v$ — direct GRANT on v$ fails with ORA-02030.
GRANT SELECT ON SYS.V_$SESSION TO GSYSTEM;
GRANT SELECT ON SYS.V_$SYSSTAT TO GSYSTEM;
GRANT SELECT ON SYS.V_$SYSMETRIC TO GSYSTEM;
=======
GRANT SELECT ON v$session TO GSYSTEM;
GRANT SELECT ON v$sysstat TO GSYSTEM;
GRANT SELECT ON v$sysmetric TO GSYSTEM;
>>>>>>> 52d1e42f9e9a3a1cd2443f67e95224f00e15bdac
