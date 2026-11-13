---
id: SOP-103
type: SOP
title: Stabilize catalog cache memory usage
service: catalog
owner: catalog-team
environment: production
last_reviewed: 2026-10-26
---
## Prerequisites
Catalog dashboard access and permission to pause the bulk import job. Record the original import batch size.
## Steps
1. Check whether the cache pods are OOMKilled and inspect memory usage.
2. Pause the catalog bulk import job with the on-call engineer's approval.
3. Reduce the import batch size to the last known healthy value.
4. Resume the import and observe cache memory and catalog latency.
## Verification
Memory remains below the configured alert threshold and catalog latency returns to baseline.
## Rollback
Pause the import again if memory rises. Restore the recorded batch size only after the incident commander approves.
