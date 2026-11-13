---
id: SOP-102
type: SOP
title: Diagnose Kafka consumer lag
service: events
owner: streaming-team
environment: production
last_reviewed: 2026-10-29
---
## Purpose
Diagnose Kafka consumer lag and events processing backlog without losing messages.
## Prerequisites
Read access to consumer group metrics, the affected topic name, and incident commander approval for scaling.
## Steps
1. Identify the affected consumer group and topic from the lag alert.
2. Compare partition count, active consumers and processing rate.
3. Check downstream latency before changing consumer concurrency.
4. If downstream systems are healthy, request approval to scale consumers up to the partition count.
5. Observe lag reduction and downstream error rates for ten minutes.
## Verification
Confirm backlog is falling and downstream error rate remains at baseline.
## Rollback
Return consumer replicas to the recorded pre-change count if downstream errors increase, then escalate.
