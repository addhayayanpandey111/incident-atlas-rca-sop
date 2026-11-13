---
id: RCA-103
type: RCA
title: Catalog cache OOM eviction incident
service: catalog
owner: catalog-team
environment: production
last_reviewed: 2026-10-25
---
## Summary
Catalog cache pods were OOMKilled during a bulk product import.
## Impact
Catalog reads experienced elevated latency for eight minutes.
## Root cause
An unbounded cache key cardinality increase exceeded the memory limit. The cache eviction policy did not constrain the imported keys.
## Prevention
Bound cache key cardinality, limit import batch size, and alert on memory growth before OOM.
