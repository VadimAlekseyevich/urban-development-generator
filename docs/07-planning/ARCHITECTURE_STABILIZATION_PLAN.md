# M0 Architecture Stabilization Plan

> **Status: ACTIVE / SCOPE FROZEN**
>
> Baseline branch: `architecture/stabilization-audit`
>
> Feature development after S10-T05 is blocked until every M0 gate below is complete.

## Why this document exists

Architecture stabilization must be finite. Audit findings are not allowed to create an endless stream of new initiatives.

From this point onward every finding must be classified into one of the ten gates below. If it does not block one of their exit criteria, it is recorded for the future roadmap and does **not** expand M0.

A new M0 gate may be added only if there is concrete evidence that the current list cannot prove one of the project-wide invariants in section 3. Adding a gate requires an ADR/change to this plan.

## 1. Ordered finite checklist

### M0-01 — Documentation ownership and architecture authority
**Goal:** one canonical owner for every cross-cutting contract.

Exit:
- Documentation Rules accepted;
- canonical architecture/pipeline documents identified;
- ADR process defined;
- planning/readiness/debt documents linked from README.

**State:** COMPLETE.

### M0-02 — Current-state architecture inventory and debt ledger
**Goal:** know what is implemented, integrated, stubbed, duplicated, or deferred.

Exit:
- duplicate/legacy architectural models identified;
- worker/persistence/runtime placeholders explicitly documented;
- existing ports/contracts inventoried;
- debt entries have severity, owner gate and closure condition.

**State:** COMPLETE for the audited baseline; ledger remains updated during M0 without creating new gates.

### M0-03 — Canonical contract convergence
**Goal:** exactly one contract for each cross-cutting concern.

Must converge:
- Stage/StageResult/RunContext;
- ConstraintEngine/ValidationReport;
- NetworkBackend;
- ArtifactStore/lifecycle;
- Job + outbox authority;
- fixed/source vs generated/run ownership;
- RawMetricId/MetricDefinition vocabulary.

Exit:
- legacy competing pipeline removed;
- DEVELOPMENT_PLAN/ARCHITECTURE/roadmap use the same ownership boundaries;
- architecture regression tests prevent reintroduction of competing models.

**State:** COMPLETE for Stage/RunContext and documented for the remaining existing contracts; final regression check occurs in M0-09.

### M0-04 — S04–S09 stage integration
**Goal:** completed algorithmic sprints are exposed through the canonical Stage contract without rewriting algorithms.

Required adapters:
- evaluate_constraints;
- suitability;
- zoning;
- roads;
- blocks_and_parcels;
- buildings;
- demography.

Exit for each adapter:
- typed input/config/output;
- stable name/version/dependencies;
- no HTTP/DB/queue imports;
- deterministic fingerprint;
- existing algorithms reused;
- fixed/generated semantics preserved;
- unit tests prove the adapter contract.

**State:** COMPLETE. All seven adapters are implemented and CI-green at `8db28e5`.

**Scope rule:** while implementing an adapter, fix only gaps required to make the already-declared sprint gate true. Such fixes remain part of this gate; they do not create new M0 initiatives.

### M0-05 — In-memory execution spine through demography
**Goal:** prove that the completed capabilities actually compose.

Exit:
- one synthetic fixture executes canonical stages through demography;
- dependencies are validated;
- stage outputs feed the next stage through typed contracts;
- EXPANSION preserves fixed state;
- FROM_SCRATCH uses the same stage family with empty fixed state where allowed;
- deterministic rerun produces the same semantic outputs/fingerprints;
- no FastAPI/SQLAlchemy/Redis/ARQ dependency is needed.

**State:** COMPLETE at `1d849f1`. Full EXPANSION spine, deterministic rerun and existing FROM_SCRATCH mode evidence are CI-green.

### M0-06 — Persistence and future orchestration boundary proof
**Goal:** ensure S12 can orchestrate the stabilized stages without redesigning them.

Exit:
- Stage name/version maps unambiguously to RunStageResult identity;
- core fingerprint vs persisted input/config hashes are explicitly separated;
- persistence models cover stage result/job/artifact/provenance needs without leaking ORM into core;
- current `worker.run_generation` placeholder is explicitly marked deferred;
- S12 ordering and ownership are validated against the stabilized stage catalog.

**Non-goal:** no production DAG/checkpoint/worker implementation in M0.

**State:** COMPLETE at `d139c3e`. Dedicated output provenance, migration-from-zero, Stage identity mapping and deferred worker boundary are CI-green.

### M0-07 — Cross-cutting invariant audit
**Goal:** prove architecture-wide invariants, not only subsystem behavior.

Audit and evidence:
- metric CRS / unit boundaries;
- deterministic RNG and tie-breaking;
- bounded spatial/network work and indexed queries;
- fixed state immutability;
- generated state run ownership;
- retry/idempotency assumptions at persistence boundaries;
- errors/diagnostics do not depend on UI/transport;
- expensive analysis results can be reused by later metrics/compare instead of recomputed.

Exit:
- every invariant has a canonical owner plus at least one regression/static/integration test or an explicitly deferred future gate;
- no open Critical/High contradiction remains.

**State:** IN PROGRESS. Existing invariant evidence is documented; the remaining automated proof is EXPANSION fixed-building and demographic-baseline regression coverage.

### M0-08 — Future roadmap architecture audit (S10–S15 + R1–R10)
**Goal:** guarantee that future tasks extend the stabilized architecture instead of inventing replacements.

Exit:
- every future capability names the contract it extends;
- task ordering reflects real dependencies;
- known duplicate risks are rewritten (network snapping, metrics, validation, pipeline, frontend registry, migration CI, research runner);
- one-request-sized UG-AI tasks have non-goals and acceptance evidence;
- release gates test one coherent pipeline rather than independent modules.

**State:** PARTIALLY COMPLETE; final pass happens after M0-04–07 so the audit reflects the actual stabilized contracts.

### M0-09 — Architecture regression gates
**Goal:** make the architecture enforceable by CI.

Required gates:
- core dependency rule;
- no legacy second pipeline model;
- NetworkX confined to its adapter;
- canonical stage catalog validity;
- adapter protocol conformance;
- execution-spine integration fixture;
- fixed/generated ownership invariants;
- deterministic fingerprint/rerun checks.

Exit:
- required checks are automated;
- failures are actionable and block merge.

**State:** IN PROGRESS.

### M0-10 — Debt-zero closure and readiness decision
**Goal:** make a binary decision before feature work resumes.

Exit:
- M0-01..09 complete;
- Architecture Debt Audit has zero open Critical/High items;
- Medium items are either fixed or assigned to a named future roadmap task with reason they do not threaten architecture;
- full required CI is green on one stabilization commit;
- README and IMPLEMENTATION_READINESS record that exact commit;
- readiness changes from BLOCKED to ACCEPTED only then.

**State:** PENDING.

## 2. Work order

Execute strictly:

```text
M0-01 ✓
 -> M0-02 ✓
 -> M0-03 ✓/final check later
 -> M0-04
 -> M0-05
 -> M0-06
 -> M0-07
 -> M0-08 final pass
 -> M0-09
 -> M0-10
 -> S10-T06
```

No S10-T06 code is allowed before M0-10.

## 3. Project-wide invariants M0 must protect

M0 is complete only if all of these are simultaneously true:

1. There is one canonical Stage model.
2. core is independent from HTTP, DB ORM, Redis/queue and frontend.
3. fixed source state is immutable; generated state is run-scoped.
4. EXPANSION and FROM_SCRATCH share the same pipeline architecture.
5. geometry calculations use explicit metric working CRS.
6. randomness/ties are deterministic under documented policies.
7. large spatial/network operations are bounded/indexed; no accidental unbounded all-pairs path exists.
8. validation has one Constraint/ValidationReport family.
9. road routing/snapping goes through the NetworkBackend/approved road primitives, not duplicated graph/index implementations.
10. raw metrics have one stable identity vocabulary and remain separate from normalized/composite score.
11. persistence/job/artifact state has one authoritative ownership model.
12. S12 can orchestrate stages without changing their domain contracts.
13. S13 can generalize delivery/UI through registries rather than root-component special cases.
14. S15 research executes normal scenarios and consumes normal provenance/metrics, not a parallel research pipeline.
15. release gates validate the same architecture used by normal operation.

## 4. Change-control rule

During M0 a discovered issue is handled as follows:

- **Blocks an exit criterion above:** fix it inside that gate and add it to the debt ledger.
- **Does not block an exit criterion but is real debt:** assign it to a concrete future UG-AI/roadmap item.
- **Nice-to-have/refactor:** do not do it.
- **Would change an Accepted architecture decision:** ADR first.

This rule is specifically intended to prevent endless stabilization.

## 5. Current next task

Continue **M0-07** only.

Current deliverable: add the two missing EXPANSION regression checks and keep the cross-cutting invariant evidence matrix green. Do not start M0-08 until those tests pass full CI.
