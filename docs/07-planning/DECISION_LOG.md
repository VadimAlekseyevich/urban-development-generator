# Decision Log

> **Status: Accepted — living index**
>
> Detailed rationale belongs in canonical specs or ADRs.

| ID | Decision | Status | Canonical source |
|---|---|---|---|
| D-001 | Expansion is the primary mode; FROM_SCRATCH uses the same pipeline | Accepted | ../DEVELOPMENT_PLAN.md |
| D-002 | Fixed source state is immutable; generated state is run-scoped | Accepted | ../DATA_MODEL.md |
| D-003 | Spatial calculations use explicit metric working CRS | Accepted | ../DEVELOPMENT_PLAN.md |
| D-004 | Run randomness comes from namespaced deterministic RunContext RNG | Accepted | ../02-architecture/PIPELINE_MODEL.md |
| D-005 | core remains independent of HTTP, SQLAlchemy, Redis/ARQ and UI | Accepted | ../ARCHITECTURE.md |
| D-006 | core Stage/StageResult is the single stage contract | Accepted | ../adr/0001-canonical-stage-execution-boundary.md |
| D-007 | Execution/persistence metadata is not part of core StageResult | Accepted | ../adr/0001-canonical-stage-execution-boundary.md |
| D-008 | Cancellation/retry/progress belong to orchestration, not core RunContext | Accepted | ../adr/0001-canonical-stage-execution-boundary.md |
| D-009 | NetworkBackend is the routing/snapping port; NetworkX is an adapter | Accepted | ../ARCHITECTURE.md |
| D-010 | S10 infrastructure snapping reuses NetworkBackend.snap; no second spatial index | Accepted | ../02-architecture/PIPELINE_MODEL.md |
| D-011 | ConstraintEngine/ValidationReport remains the canonical validation family | Accepted | ../CONSTRAINT_ENGINE.md |
| D-012 | S11 metrics extend RawMetricId/MetricDefinition rather than fork metric IDs | Accepted | ../02-architecture/PIPELINE_MODEL.md |
| D-013 | PostgreSQL Job/outbox is authoritative across DB/Redis handoff | Accepted | ../DEVELOPMENT_PLAN.md |
| D-014 | ArtifactStore is the blob port; Artifact records own persisted lifecycle metadata | Accepted | ../DATA_MODEL.md |
| D-015 | Work-item completion count is not an architecture-readiness metric | Accepted | ../00-overview/DOCUMENTATION_RULES.md |
| D-016 | S13 layer registry is the required convergence point for final frontend workspace | Accepted | ARCHITECTURE_DEBT_AUDIT.md |

## Maintenance

When a decision changes:

1. create/update/supersede an ADR when the trade-off is material;
2. update the canonical source;
3. update this index;
4. update dependent roadmap/AI tasks/tests;
5. retain historical rationale.
