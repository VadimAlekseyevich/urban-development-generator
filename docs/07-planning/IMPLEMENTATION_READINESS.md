# Implementation Readiness

> **Status: ACCEPTED — M0 architecture stabilization complete**
>
> Original audited baseline: `main@405890d820751d07156099f6f7073f22cdd083fa`
>
> Stabilization evidence commit: `39eaa1688e06669b0e01e999304710873fd9cf0e`
>
> Required CI on that commit: **green**.

## 1. Decision

The repository is architecture-ready for post-S10 feature development. S10/M1 is complete; S11 is implemented through UG-AI-058: canonical validation/raw metrics, versioned normalization, explainable composite scoring and deterministic weight sensitivity operate on persisted evaluation snapshots without rerunning GIS. The current ordered task is **UG-AI-059 / S11-T13**.

This decision means the cross-cutting contracts needed by the remaining roadmap are stable enough
that S10-S15 can extend them without another pre-feature architecture rewrite.

It does **not** mean S12 orchestration, S13 delivery, S14 hardening or S15 experiments are already
implemented; those remain normal future roadmap work.

## 2. Accepted architecture decisions

The following are canonical and must not be re-invented:

- modular monolith + separate worker process;
- core independent of HTTP/DB/Redis/UI;
- Postgres/PostGIS authoritative metadata/spatial persistence;
- Redis/ARQ as delivery/execution infrastructure, not source of truth;
- immutable DatasetVersion and successful GenerationRun semantics;
- fixed source state is never mutated by generation;
- generated state is run-scoped;
- metric working CRS for geometry calculations;
- deterministic namespaced RNG from RunContext;
- core `Stage` / `StageResult` as the only stage contract;
- canonical coarse stage catalog/dependency identities;
- `ConstraintEngine` / `ValidationReport` as the validation family;
- `NetworkBackend` as routing/snapping port;
- `ArtifactStore` as blob storage port;
- raw metric vocabulary from `domain.benchmarking`;
- `Job` + outbox as DB-authoritative queue handoff foundation;
- `RunStageResult.output_fingerprint` is distinct from input/config provenance hashes.

## 3. M0 completion

- [x] M0-01 — Documentation ownership and architecture authority.
- [x] M0-02 — Current-state inventory and debt ledger.
- [x] M0-03 — Canonical contract convergence.
- [x] M0-04 — S04–S09 typed Stage integration.
- [x] M0-05 — In-memory execution spine through demography.
- [x] M0-06 — Persistence/future-orchestration boundary proof.
- [x] M0-07 — Cross-cutting invariant audit.
- [x] M0-08 — Final future-roadmap architecture audit.
- [x] M0-09 — Architecture regression gates.
- [x] M0-10 — Debt-zero closure and readiness decision.

Critical/High architecture debt: **0 open**.

Remaining Medium item:
- AD-009 -> UG-AI-085/086 (frontend layer registry/tree).

AD-007 is closed through UG-AI-046/047. The remaining item is named roadmap work and does not
threaten the stabilized cross-cutting architecture.

## 4. What remains intentionally future work

Do not pull these forward merely because M0 is complete:

- executable persistent DAG/checkpoints — S12;
- production generation worker — S12;
- cancellation/retry execution policy — S12;
- scenario batches/exact rerun/provenance manifest — S12;
- MVT/export/workspace generalization — S13;
- production reliability/performance hardening — S14;
- experiments/research/demo package — S15.

## 5. Completed contract for S10-T06

S10-T06 must:

- use existing `NetworkBackend.snap()`;
- define typed demand/facility/candidate snap records;
- keep metric CRS and maximum snap distance explicit;
- define unsnapped diagnostics;
- bound batch size;
- be deterministic under input permutation/ties;
- not import NetworkX or `SpatialSnapIndex` from infrastructure code.

The ordered execution tasks are UG-AI-015 through UG-AI-019.

## 6. Definition of Ready for every future AI task

Before starting an AI task:

- use the exact `UG-AI-xxx` entry;
- verify parent/prerequisites;
- read the canonical contract owner;
- state input/output contracts and non-goals;
- preserve M0 architecture regression gates;
- define required tests/fixtures;
- use ADR before intentionally changing an Accepted architecture decision.

## 7. Current next action

```text
UG-AI-059 / S11-T13
Deliver run-scoped violations layer API/UI from canonical validation details.
```

Feature work may resume only through this ordered backlog; do not skip directly to a later item.
