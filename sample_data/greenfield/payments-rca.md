---
id: RCA-101
type: RCA
title: Payments database connection exhaustion
service: payments
owner: payments-platform
environment: production
last_reviewed: 2026-11-01
---
## Summary
Payments API returned HTTP 503 errors after a deployment exhausted the PostgreSQL connection pool.
## Impact
18% of payment requests failed for 12 minutes. No completed transactions were lost.
## Timeline
14:02 UTC: deployment started.
14:05 UTC: connection timeout and 503 alerts fired.
14:17 UTC: rollback restored normal service.
## Root cause
A new request handler did not release database connections on its error path. The leaked connections exhausted the pool under load.
## Prevention
The team fixed connection cleanup and added a pool saturation alert and an error-path integration test.
