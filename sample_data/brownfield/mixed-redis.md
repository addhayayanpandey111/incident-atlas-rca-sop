---
title: Redis replica incident and recovery notes
service: session
owner: session-team
last_reviewed: 2026-09-13
---
# Redis replica notes
## Impact
Session reads were delayed for three minutes.
## Root cause
Redis replica synchronization saturated network bandwidth during a snapshot.
## Prerequisites
Read access to Redis replication metrics and on-call approval.
## Steps
1. Inspect replication delay and network saturation.
2. Pause the scheduled snapshot after on-call approval.
3. Confirm replication delay returns to baseline.
## Verification
Session read latency and replication delay remain normal for five minutes.
## Rollback
Resume the snapshot only when network capacity is available and the on-call engineer approves.
