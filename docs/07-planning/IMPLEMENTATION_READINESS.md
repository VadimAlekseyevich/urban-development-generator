# Implementation Readiness

> **Status: BLOCKED — architecture stabilization in progress**
>
> Baseline audited: `main@405890d820751d07156099f6f7073f22cdd083fa`

## 1. Meaning of this gate

This document is the gate between the current completed capability set and additional feature work.

“Blocked” does not mean the existing algorithms are invalid. It means the repository has known integration/contract debt that must be removed before adding new infrastructure, validation, orchestration, or UI complexity.

## 2. Accepted architecture decisions

The following are accepted and must not be re-invented:

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
- ConstraintEngine as shared validation rule engine;
- NetworkBackend as routing/snapping port;
- ArtifactStore as blob storage port;
- raw metric vocabulary from `domain.benchmarking`;
- Job + outbox as DB-authoritative queue handoff foundation.

## 3. Blocking stabilization work

The finite stabilization scope is defined by [ARCHITECTURE_STABILIZATION_PLAN.md](ARCHITECTURE_STABILIZATION_PLAN.md). The next executable work is **M0-04**, not S10-T06. New findings must be absorbed by one of the ten frozen M0 gates rather than creating an open-ended stabilization program.

- [x] M0-01 — Documentation ownership and architecture authority.
- [x] M0-02 — Current-state inventory and debt ledger.
- [x] M0-03 — Canonical contract convergence baseline.
- [ ] M0-04 — S04–S09 typed Stage integration.
- [ ] M0-05 — In-memory execution spine through demography.
- [ ] M0-06 — Persistence/future-orchestration boundary proof.
- [ ] M0-07 — Cross-cutting invariant audit.
- [ ] M0-08 — Final future-roadmap architecture audit.
- [ ] M0-09 — Architecture regression gates.
- [ ] M0-10 — Debt-zero closure, full CI and readiness decision.

## 4. Explicitly not required before leaving stabilization

The following remain future roadmap work and must not be pulled into STAB:

- persistent DAG/checkpoints;
- production generation worker orchestration;
- cancellation/retry execution policy;
- scenario batches;
- exact rerun;
- provenance manifest;
- MVT/export;
- production queues/backpressure;
- experiments.

STAB fixes architecture boundaries; it does not implement S12 early.

## 5. Definition of Ready for S10-T06

S10-T06 becomes Ready only when:

- there is one Stage model in the repository;
- completed coarse stages through demography are available behind typed adapters;
- one deterministic in-memory execution fixture composes them;
- `NetworkBackend.snap()` is the documented dependency for infrastructure snapping;
- no critical/high architecture debt remains open;
- all required CI checks are green.

## 6. Definition of Ready for any AI task

Before an AI task starts:

- the exact `UG-AI-xxx` entry exists;
- parent work item and prerequisites are complete;
- canonical docs are listed;
- input/output contracts are known;
- non-goals are explicit;
- test/fixture evidence is defined;
- architecture changes are either prohibited or owned by a named ADR task.

## 7. Current next action

Execute the frozen M0 plan in `ARCHITECTURE_STABILIZATION_PLAN.md` strictly in order. Current gate: **M0-04**. Do not start S10-T06 until M0-10 closes.
