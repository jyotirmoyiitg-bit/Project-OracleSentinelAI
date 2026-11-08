# Oracle Sentinel AI — GSYSTEM Permissions

## Overview

GSYSTEM is a local Oracle monitoring user with minimal read-only privileges. It is **not** a superuser and cannot execute DDL or DML.

## Required Privileges

- `CREATE SESSION` — to connect
- `SELECT` on specific `V$` and `DBA_` views for monitoring

## Provisioning

Run `scripts/oracle/01_create_gsystem.sql` as SYSDBA on each target database.

## Verification

Run `scripts/oracle/03_verify_gsystem.sql` as GSYSTEM to verify access.

## Emergency Revoke

Run `scripts/oracle/04_revoke_gsystem.sql` as SYSDBA to revoke all privileges and lock the account.
