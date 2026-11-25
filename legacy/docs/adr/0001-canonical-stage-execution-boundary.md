# ADR 0001 — Canonical Stage Execution Boundary

> **Status: Accepted**
>
> Date: 2026-09-19

## Context

The repository introduced a typed infrastructure-independent `Stage` / `StageResult` contract in S01. An older scaffold under `core/urban_generator/pipeline/` also defines `PipelineStage` and `GenerationPipeline` using `dict[str, Any]`.

S04-S09 then implemented substantial algorithm libraries and vertical slices without adopting either scaffold as an executable end-to-end pipeline.

If both models survive into S10-S12, orchestration would have to choose between them or maintain adapters between two sources of stage identity.

The development plan also used broader wording for RunContext/StageResult than the implemented core types, mixing algorithm semantics with execution/persistence concerns.

## Decision

1. `core.urban_generator.domain.Stage` is the only canonical stage contract.
2. The legacy `PipelineStage` / `GenerationPipeline` model is removed.
3. Existing algorithms are composed by thin typed stage adapters; algorithms are not rewritten merely to “be stages”.
4. Core `StageResult` owns typed output, deterministic fingerprint and diagnostics only.
5. Persisted execution metadata belongs to `RunStageResult` and related application/persistence models.
6. Core `RunContext` owns deterministic run semantics, not cancellation/logging/DB/queue services.
7. S12 executable DAG/orchestration must use the stabilized Stage metadata and may not introduce another stage identity model.
8. Infrastructure snapping consumes `NetworkBackend`; metrics extend `domain.benchmarking`; validation extends the existing Constraint family.

## Consequences

Positive:

- no duplicate pipeline abstraction;
- completed algorithms remain reusable/testable outside orchestration;
- S12 becomes orchestration work rather than architecture repair;
- persistence concerns do not leak into core;
- stage identity can map directly to RunStageResult provenance.

Costs:

- S04-S09 require adapter work before feature development continues;
- an integration spine must be added even though subsystem tests are already green;
- DEVELOPMENT_PLAN wording and future task descriptions require synchronization.

## Rejected alternatives

### Keep both pipeline models

Rejected because two stage identity systems would make checkpointing, dependency metadata and provenance ambiguous.

### Move DB/job/progress fields into core StageResult

Rejected because it couples algorithm execution to persistence/worker concerns and violates the existing module boundary.

### Delay adaptation until S12

Rejected because S10/S11 would add more components without proving that already-completed stages can compose through the intended contract.
