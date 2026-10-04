# Implementation Readiness

> **Status: ACCEPTED — M0 architecture stabilization complete**
>
> Original audited baseline: `main@405890d820751d07156099f6f7073f22cdd083fa`
>
> Stabilization evidence commit: `39eaa1688e06669b0e01e999304710873fd9cf0e`
>
> Required CI on that commit: **green**.

## 1. Decision

The repository is architecture-ready for post-S10 feature development. S10/M1 and S11/M2 are complete; S12 is implemented through UG-AI-080, including DB-authoritative cancellation/retry, outbox recovery, stage artifact publication, bounded ScenarioBatch admission, deterministic seed/full-config matrix expansion, and exact successful-run input replay with strict source blob/checksum and code availability verification plus durable lineage (ADR-0006/0007/0008), and deterministic read-only successful-run provenance manifests. S13-T01 introduces the immutable application-layer LayerCatalog with no new delivery endpoint or database model. S13-T02 adds an owner-authorized, 15-table-backed bounded bbox/UUID-keyset GeoJSON path; the project boundary, suitability raster and canonical integer-index validation report retain their specialized APIs. S13-T03 adds bounded, owner-scoped PostGIS MVT tiles for all 15 table-backed UUID layers, without introducing immutable caching before S13-T04. S13-T04 adds publication-qualified immutable tile ETags/304. S13-T05 introduces the frontend owner-qualified 18-layer registry and separated render recipes. S13-T06 builds the complete registry-derived tree. S13-T07 adds DB-outbox-backed single-layer GeoJSON export over immutable ready DatasetVersion/succeeded run owners, bounded keyset reads and referenced ArtifactStore publication. S13-T08 extends the same job/outbox/artifact authority to canonical-order multi-layer GeoPackage exports with exact dataset/run owners, 5,000-row keyset batches, hard per-layer/total caps and retry-safe ready-container reuse. S13-T09 adds bounded synchronous 1–10-run canonical raw-metrics CSV export from the persisted evaluation envelope, with no GIS/validation/score recomputation. S13-T10 exposes the existing deterministic S12 provenance manifest as a project-scoped JSON download, preserving normalized config, seed/working CRS, dataset-version/source checksums, code revision, stage fingerprints/artifacts and persisted evaluation without a second provenance model or GIS recomputation. The current ordered task is **UG-AI-091 / S13-T11**.

This decision means the cross-cutting contracts needed by the remaining roadmap are stable enough
that S10-S15 can extend them without another pre-feature architecture rewrite.

M3/M4 S12 integration contracts are closed with synthetic worker/persistence and API acceptance (see `MILESTONES.md`). This does **not** assert a real-territory browser E2E, production hardening, S13 delivery or S15 experiment results; those remain future roadmap work.

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

- real-territory browser E2E across complete workspace — S13;
- real-territory output reproducibility/tolerance experiments — S15;
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
UG-AI-091 / S13-T11
Implement S3/MinIO ArtifactStore adapter contract parity with LocalArtifactStore.
```

Feature work may resume only through this ordered backlog; do not skip directly to a later item.
