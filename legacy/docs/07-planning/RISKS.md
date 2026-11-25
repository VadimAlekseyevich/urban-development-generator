# Architecture and Delivery Risks

> **Status: Active — living register**

| ID | Risk | Trigger / evidence | Mitigation | Gate |
|---|---|---|---|---|
| RSK-001 | Component-complete but pipeline-incomplete product | Stage contract existed without S04-S09 adapters | STAB adapters + typed spine fixture | M0 |
| RSK-002 | Duplicate architecture from vague AI tasks | legacy PipelineStage vs domain Stage; T06 wording vs existing snap | canonical docs + AI task non-goals | M0 |
| RSK-003 | Road/infrastructure performance degrades to N×M | many demand/site pairs | NetworkBackend, bounded candidates, batched Dijkstra/caches | M1/M6 |
| RSK-004 | CRS/units drift between stages | source API uses EPSG:4326 while core uses metric CRS | explicit boundaries and typed working SRID | all |
| RSK-005 | Retry corrupts immutable results | worker/DB/Redis are separate failure domains | DB-authoritative Job/outbox + idempotent writers/checkpoints | M3 |
| RSK-006 | Artifact metadata/blob lifecycle diverges | temp/ready/referenced across failures | publish protocol + GC + reliability E2E | M3/M6 |
| RSK-007 | Validation becomes stage-specific duplicated logic | future violations/score work | extend ConstraintResult/ValidationReport only | M2 |
| RSK-008 | Metric definitions fork between research and UI | early benchmarking vocabulary already exists | extend RawMetricId registry, preserve raw metrics | M2/M7 |
| RSK-009 | Composite score hides model quality | score used as single answer | persist raw metrics, normalization metadata, sensitivity | M2 |
| RSK-010 | Frontend root becomes state/layout bottleneck | vertical slices are manually wired | declarative layer registry before complete workspace | M5 |
| RSK-011 | S12 becomes a rewrite of completed algorithm sprints | stages not adapted before orchestration | finish M0 before S10 resumes | M0/M3 |
| RSK-012 | Work-item count creates false confidence | README 127/210 while generation worker is stub | report milestones/readiness separately | all |
| RSK-013 | Benchmarks optimize synthetic micro-cases only | existing reference benches are subsystem-specific | 25/100 km² integrated profiles and real territories | M6/M7 |
| RSK-014 | FROM_SCRATCH diverges into second pipeline | pressure to special-case missing fixed state | same adapters/DAG; vary snapshot/config only | M8 |
| RSK-015 | Documentation forks into multiple authorities | many flat docs accumulated per task | documentation rules + canonical owner index | M0 |
