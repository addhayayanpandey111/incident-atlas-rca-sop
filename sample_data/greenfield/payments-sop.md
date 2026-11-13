---
id: SOP-101
type: SOP
title: Recover payments database connection pool
service: payments
owner: payments-platform
environment: production
last_reviewed: 2026-11-02
---
## Purpose
Recover the payments API from PostgreSQL connection pool exhaustion, connection timeouts and HTTP 503 errors.
## Prerequisites
Incident commander approval, production cluster access, and the previous healthy deployment revision are required. Confirm the alert is for payments, not another service.
## Warnings
Do not restart the database or increase connection limits during this procedure. Commands are illustrative fixtures; validate deployment names in your environment.
## Steps
1. Record the current deployment revision with `kubectl rollout history deployment/payments -n production`.
2. Check connection pool saturation and compare the alert time with the latest deployment.
3. With incident commander approval, run `kubectl rollout undo deployment/payments -n production --to-revision=<HEALTHY_REVISION>`.
4. Wait for completion with `kubectl rollout status deployment/payments -n production --timeout=180s`.
5. Confirm the 503 rate and database connection usage return to the service baseline.
## Verification
Observe five minutes of normal payment success rate and pool utilization before resolving the incident.
## Rollback
If the deployment rollback increases errors, stop and escalate to the incident commander. Restore the recorded revision only with explicit approval and monitor payment success rate.
