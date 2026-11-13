---
id: RCA-102
type: RCA
title: Kafka consumer lag after partition expansion
service: events
owner: streaming-team
environment: production
last_reviewed: 2026-10-28
---
## Summary
Kafka events consumers accumulated a backlog after the topic partition count increased.
## Impact
Event processing was delayed by up to 24 minutes; no messages were lost.
## Timeline
09:00 UTC: partition expansion.
09:08 UTC: consumer lag alert.
09:32 UTC: consumers recovered.
## Root cause
Consumer concurrency remained fixed at four while partitions increased to sixteen. The consumer group could not drain the increased workload.
## Prevention
Capacity review is now required before changing partition counts. Consumer lag alerts include group concurrency.
