# Milestones

> **Status: Accepted**
>
> Milestones are integrated capability gates, not module-completion labels.

## M0 — Architecture Aligned

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

Exit:

- S11 complete;
- one scenario reaches final validation and canonical raw metrics;
- hard/soft violations are explainable and spatially attributable where applicable;
- score is derived from persisted raw metrics and is not the only result;
- regression fixture detects algorithm drift.

## M3 — Durable Generation Run

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

Exit:

- ScenarioBatch;
- seed/config matrix;
- exact rerun;
- provenance manifest;
- compare backend;
- run controls/progress;
- compare UI;
- same semantic run can be reproduced from recorded provenance subject to documented tolerance.

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
