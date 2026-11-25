# Milestones

> **Status: Accepted**
>
> Milestones are integrated capability gates, not module-completion labels.

## M0 — Architecture Aligned

**State: COMPLETE.** Evidence commit: `39eaa1688e06669b0e01e999304710873fd9cf0e`.

Exit:

- documentation ownership rules accepted;
- architecture debt audit has no open Critical/High findings;
- one canonical Stage/StageResult model;
- S04-S09 capabilities exposed through typed stage adapters;
- deterministic in-memory execution spine passes through demography;
- persistence/orchestration boundary can carry Stage output provenance without changing core;
- cross-cutting CRS/determinism/bounds/ownership/retry invariants have regression evidence;
- future roadmap S10-S15 audited against existing contracts;
- required architecture regression and full CI green.

## M1 — Infrastructure Complete

**State: COMPLETE.** S10 closure evidence: PR #122 closure branch required CI run
`35560322208` was green on `ec01af3df7aa741f531a13f69bee2677f2e93523`; the run included
the dedicated infrastructure reference benchmark plus full Python/frontend/compose gates.

Exit:

- S10 complete;
- demographic demand reaches infrastructure placement;
- existing/generated facilities remain distinguishable;
- demand/sites/facilities snap through NetworkBackend;
- network accessibility is bounded and deterministic;
- placement respects capacity/site feasibility;
- persisted result + metrics + UI vertical slice exist;
- synthetic-town and performance gates pass.

## M2 — First Complete Deterministic Scenario

**State: COMPLETE.** S11 regression evidence: PR #142 CI run `36125165479` was green on
`9b21116ee5cb05737afec956829617e8775bf06e`; the run included full Python tests,
reference benchmarks, frontend build and Docker Compose readiness.

Exit:

- S11 complete;
- one deterministic scenario reaches canonical raw metrics and aggregate final validation;
- hard/soft violations remain independently explainable through canonical `ValidationReport`;
- score is derived from the same raw metrics and remains decomposable into normalized values,
  weights and contributions;
- S10 synthetic-town infrastructure metrics remain in canonical registry order;
- regression ranges/invariants detect algorithm drift without introducing a parallel execution path.

## M3 — Durable Generation Run

**State: COMPLETE (S12 contract/integration gate).** Evidence: `test_generation_worker_e2e.py`, `test_generation_state.py`, `test_outbox_dispatcher_recovery.py`, `test_checkpoint_reuse.py`, `test_artifact_publication_gc.py`, and `test_run_control_api.py` under required CI. These use a real Postgres/worker boundary with small typed synthetic stages, not a fabricated HTTP executor.

Exit:

- executable Stage DAG;
- persisted context assembly;
- checkpoint fingerprints;
- real worker generation outside HTTP;
- cancellation/retry consistency;
- DB/outbox delivery recovery;
- artifact publish/GC lifecycle;
- failed/retried execution cannot corrupt successful runs.

## M4 — Reproducible Scenario Workflow

**State: COMPLETE (S12 workflow contract/integration gate).** Evidence: `test_scenario_batch.py`, `test_scenario_matrix.py`, `test_exact_rerun.py`, `test_provenance_manifest.py`, `test_run_compare_api.py`, the created-run-to-persisted-compare acceptance in `test_run_control_api.py`, and `frontend/tests/compareSelection.test.mjs` under required CI. The compare interface and generated/validation map panels use one selected run with no cross-run fallback. Exact input replay verifies code/source availability and hashes; it does not claim an unperformed real-territory output experiment.

Exit:

- ScenarioBatch;
- seed/config matrix;
- exact rerun;
- provenance manifest;
- compare backend;
- run controls/progress;
- compare UI;
- same semantic run can be reproduced from recorded provenance subject to documented tolerance.

M3/M4 closure is for the persisted orchestration and scenario **integration contracts**, not a completed production/demo workflow. A real browser create→upload→run→compare→export E2E belongs to S13-T14; two-territory output reproducibility/tolerance experiments belong to S15. A deployment must provide a real executable commit and supported stage configuration.

## M5 — Complete GIS Workspace

Exit:

- canonical layer catalog;
- scalable bbox/MVT delivery;
- frontend layer registry/tree;
- GeoJSON/GPKG/CSV/config/provenance exports;
- large upload UX;
- complete project workspace;
- browser E2E create/upload/run/inspect/compare/export.

## M6 — Reliability Envelope

Exit:

- queue separation and backpressure;
- DB/raster/graph/building/infrastructure profiling;
- reference benchmark suite;
- operational metrics;
- bounded artifact GC;
- production-like Docker profile;
- security/backup/migration/reliability E2E gates;
- known performance exceptions documented explicitly.

## M7 — Research and Demo Candidate

Exit:

- experiment runner;
- two reproducible territory packages;
- reproducibility, seed, density, constraint, infrastructure and score-sensitivity experiments;
- raw tables/manifests;
- offline demo dataset;
- defense flow and fallback artifacts.

## M8 — v1.0 Acceptance

Exit:

- R1-R9 all pass together;
- expansion invariants pass;
- from-scratch uses the same pipeline;
- reproducibility and performance acceptance pass;
- research package is reproducible;
- only then create R10 / `v1.0.0`.

## Rule

A sprint can be “implemented” without completing the next milestone. A milestone is complete only when its capabilities work together through the accepted architecture.
