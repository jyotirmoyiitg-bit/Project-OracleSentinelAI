# Privileged operations — security review template

Release 1 does not execute privileged changes. `OPERATIONS_EXECUTION_ENABLED` defaults to false. Setting that flag without `OPERATIONS_EXECUTION_APPROVED` prevents the application from starting. Setting both flags still does not send a statement to Oracle. The reserved `privileged_operations` table is not written.

Do not enable an executor until every item below has a recorded reviewer and a date.

## Review checklist

- The change is one named operation, not a statement supplied by the operator or a model.
- The Oracle account used for a future executor is separate from the read-only monitoring account `GSYSTEM`.
- That account is not created by this release, and its password is not stored in this repository.
- Two people are required before a change can run: the requester cannot be the only approver.
- An approval expires. `PROPOSAL_EXPIRY_HOURS` is the current proposal lifetime.
- The target is not in a maintenance restriction.
- A preflight check confirms the database, the object, and the free space from measured values.
- The attempt, the decision, and any future result are written to `audit_events`. Audit rows cannot be updated or deleted.
- The portal has no control that runs the change.
- `POST /api/v1/operations/proposals/{id}/execute` stays refused.
- `POST /api/v1/operations/privileged/{id}/execute` stays unimplemented until this review is complete.

| Item | Reviewer | Date | Result |
| --- | --- | --- | --- |
| Named operation only | | | |
| Separate privilege account | | | |
| Dual approval | | | |
| Expiry and maintenance check | | | |
| Audit review | | | |
| No portal execution control | | | |

## Integration test database

Use a database created for this test, not a shared production instance. The read-only monitoring grants stay on `GSYSTEM`. A future executor account is created only in that test database, by a DBA, outside this application. The test restores or recreates the database after the run. This release does not connect an executor to that database.

## Rollback

Release 1 has nothing to roll back because it does not run the change. A future executor must record the statement it ran and the reverse statement, when one exists, in the execution log before it returns. If no reverse statement exists, the review stops and the change does not run. Restoring the integration database is the rollback for a test run.
