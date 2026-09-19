# Architecture Debt Audit

> **Status: Closed for M0 — non-blocking**
>
> Original audited baseline: `main@405890d820751d07156099f6f7073f22cdd083fa`
>
> Stabilization evidence commit: `39eaa1688e06669b0e01e999304710873fd9cf0e`
>
> Required CI on the evidence commit: **green** (Python, frontend, Docker Compose smoke).

## 1. Closure result

The M0 audit is closed for feature-readiness purposes.

There are **zero open Critical or High architecture findings**. The integration debt that blocked
S10-T06 was removed or converted into an explicit, non-architectural future implementation task.

The remaining Medium findings are assigned to named future UG-AI tasks and do not require changing
the stabilized Stage, persistence, network, validation, metric or ownership contracts.

## 2. Final finding ledger

### AD-001 — Legacy second pipeline model
**Severity:** Critical  
**State:** Closed

The untyped `dict[str, Any]` pipeline and independent `PipelineStage` vocabulary were removed.
`domain.Stage` / `StageResult` are the only canonical stage contract.

Regression evidence:
`tests/unit/test_architecture_boundaries.py::test_legacy_second_pipeline_model_does_not_return`.

### AD-002 — Stage contract not adopted by completed S04-S09 capabilities
**Severity:** Critical  
**State:** Closed

Suitability/constraints, zoning, roads, blocks/parcels, buildings and demography now have typed
Stage adapters with stable metadata, deterministic fingerprints and no infrastructure imports.

The in-memory execution spine composes the adapters through demography.

Evidence: M0-04, M0-05 and `tests/integration/test_in_memory_generation_spine.py`.

### AD-003 — Generation worker is a placeholder
**Severity:** High at audit time  
**State:** Stabilization-resolved; implementation intentionally deferred to S12

`worker.tasks.run_generation()` is explicitly documented as an S12 placeholder and is not
presented as completed orchestration.

The stabilized Stage and persistence boundaries now prove that S12 can implement the real worker
without redesigning core.

Future implementation owner:
- `UG-AI-068` / S12-T04 — real DAG execution;
- `UG-AI-069` / S12-T04 — worker integration fixture.

This is future functionality, not open architecture debt.

### AD-004 — Development plan overstated RunContext/StageResult responsibilities
**Severity:** High  
**State:** Closed

`PIPELINE_MODEL.md`, ADR/documentation and roadmap now agree on the narrower core boundary:
algorithmic Stage output/fingerprint/diagnostics remain in core; lifecycle, progress, retry,
timestamps and persistence provenance remain outside core.

### AD-005 — S10-T06 could duplicate snapping infrastructure
**Severity:** High  
**State:** Closed

S10-T06 and UG-AI-015..019 explicitly consume `NetworkBackend.snap()`.
Infrastructure-specific NetworkX/STRtree ownership is prohibited.

### AD-006 — S11-T04 could duplicate metric vocabulary
**Severity:** High  
**State:** Closed

S11 work explicitly extends canonical `RawMetricId` / `MetricDefinition`; it may not create
another metric identity vocabulary.

### AD-007 — ValidationReport needs richer violation detail
**Severity:** Medium  
**State:** Deferred with named owner; non-blocking

This is a planned additive extension of the canonical validation family, not a competing contract.

Future owner:
- `UG-AI-046` / S11-T01 — entity/problem-geometry detail;
- `UG-AI-047` / S11-T01 — aggregation/serialization evidence.

### AD-008 — Work-item count can overstate integrated readiness
**Severity:** Medium  
**State:** Closed

README/planning now report architecture readiness and milestone state separately from historical
work-item arithmetic. A raw completed-item percentage is not treated as product readiness.

### AD-009 — Frontend composition may accumulate in root wiring
**Severity:** Medium  
**State:** Deferred with named owner; non-blocking

Current UI slices do not require an architecture rewrite before S10. Generalized composition is an
explicit S13 gate.

Future owner:
- `UG-AI-085` / S13-T05 — declarative frontend layer registry;
- `UG-AI-086` / S13-T06 — full layer tree from catalog/registry.

### AD-010 — Stage output provenance was not persisted separately
**Severity:** High discovered during M0-06  
**State:** Closed

`RunStageResult.output_fingerprint` now persists the canonical `StageResult.fingerprint`
separately from `input_hash` and `config_hash`. Migration-from-zero and persistence boundary
tests are green.

S12 checkpoint work must consume dependency output fingerprints through resolved input provenance;
it must not collapse the three hash roles into one value.

## 3. Architecture invariants now enforced

Required CI protects:

- one Stage model and canonical stage catalog;
- core independence from HTTP/ORM/Redis/worker frameworks;
- NetworkX isolation behind the network adapter;
- metric CRS boundaries;
- deterministic RNG/fingerprints/tie-breaking;
- bounded spatial/network work;
- fixed/source immutability and generated/run ownership;
- EXPANSION and FROM_SCRATCH use of the same stage family;
- independent input/config/output provenance;
- Job/outbox and Artifact lifecycle authority;
- in-memory typed composition through demography.

See `M0_ARCHITECTURE_REGRESSION_GATES.md`.

## 4. Feature-readiness decision

The debt audit no longer blocks feature work.

The next allowed feature item after M0 closure is:

```text
UG-AI-015 / S10-T06
```

Any future task that would introduce a competing cross-cutting contract must stop for ADR review
rather than reopening an unbounded stabilization program.
