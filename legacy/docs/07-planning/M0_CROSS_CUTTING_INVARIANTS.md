# M0 Cross-cutting Invariant Audit

> **Gate:** M0-07
>
> This audit maps every stabilization invariant to one canonical owner and concrete automated
> evidence. It does not introduce new feature behavior.

## Result

No new Critical/High architecture contradiction was found after M0-04 through M0-06.

Two missing regression checks were identified for EXPANSION state above the road stage:
fixed-building spacing/state preservation and demographic baseline preservation. M0-07 adds those
tests without changing production algorithms.

## 1. Metric CRS and unit boundaries

**Canonical owners**

- `core.urban_generator.domain.crs.WorkingCRS`;
- `ProjectSettings.working_srid`;
- `RunContext.working_srid`;
- typed metric fields such as `*_m`, `*_m2`.

**Evidence**

- `tests/unit/test_project_crs_contract.py`;
- `tests/unit/test_run_context.py::test_context_requires_metric_working_crs`;
- stage adapter alignment checks for suitability/zoning/roads/blocks/buildings/demography.

Geographic EPSG:4326 and projected non-metre CRS are rejected before metric core operations.

## 2. Deterministic RNG and tie-breaking

**Canonical owners**

- `RunContext.rng(namespace)` / `rng_seed(namespace)`;
- stable-id/distance ordering inside spatial and network adapters;
- deterministic Stage fingerprints.

**Evidence**

- `tests/unit/test_run_context.py`;
- `tests/unit/test_spatial_snapping.py`;
- `tests/unit/test_networkx_backend.py`;
- all Stage adapter deterministic-rerun tests;
- `tests/integration/test_in_memory_generation_spine.py`.

No algorithmic stage may use an unscoped process-global RNG.

## 3. Bounded/indexed spatial and network work

**Canonical owners**

- `SpatialSnapIndex`;
- `NetworkBackend` / `NetworkXBackend`;
- explicit road routing/snap/attachment policies;
- explicit building and demography max-work configuration.

**Evidence**

- snap target limits and tolerance tests;
- network visited-node limits and cutoffs;
- endpoint/fixed-network road attachment bounds;
- Stage configs expose max cells/candidates/iterations/buildings/blocks;
- architecture regression confines NetworkX to its adapter.

No unbounded all-pairs fallback is accepted as a core path.

## 4. Fixed source state immutability

**Canonical owners**

- immutable `TerritorySnapshot` / `SnapshotLayerRef`;
- `WorldStateContract.fixed_source()`;
- mode guards in Stage adapters;
- DB immutability triggers for successful runs and ready source state.

**Evidence**

- EXPANSION road stage preserves fixed edges;
- full M0 EXPANSION spine preserves fixed road ownership;
- M0-07 building EXPANSION test proves fixed footprint participation in spacing and unchanged refs;
- M0-07 demography EXPANSION test proves baseline population and unchanged fixed refs;
- generated writers reject mutation of successful runs.

FROM_SCRATCH rejects fixed state where the stage owns that state family.

## 5. Generated state is run-scoped

**Canonical owners**

- `WorldStateContract.generated_for(run_id)`;
- generated entity tables with `run_id`;
- deterministic run-scoped generated-zone/building identities;
- generated writers.

**Evidence**

- road Stage emits fixed/generated ownership distinctly;
- generated road/block/parcel/building writer integration tests;
- retry tests replace only the target run state and preserve deterministic identities where required.

Generated state never mutates source rows.

## 6. Retry and idempotency boundaries

**Canonical owners**

- DB-authoritative `Job`;
- `JobOutbox` with stable `job:<job_id>` enqueue identity;
- `ArtifactStore` idempotent promotion/deletion semantics;
- replace-style generated writers guarded by successful-run immutability.

**Evidence**

- `tests/unit/test_job_persistence.py`;
- `tests/unit/test_job_outbox_dispatcher.py`;
- `tests/unit/test_artifact_store_contract.py`;
- `tests/unit/test_local_artifact_store.py`;
- generated writer retry tests.

Redis/ARQ delivery is not authoritative state.

## 7. Errors and diagnostics are transport-independent

**Canonical owners**

- core `UrbanGeneratorError` taxonomy;
- `StageDiagnostic` / `StageDiagnosticLevel`;
- application adapters translate those contracts to transport/persistence forms.

**Evidence**

- architecture test forbids FastAPI/SQLAlchemy/Redis/ARQ/backend/worker imports in core;
- Job failure serialization uses stable domain error taxonomy;
- Stage adapters return diagnostics without persistence or HTTP concerns.

## 8. Expensive analysis outputs are reusable

**Canonical owners**

- `RoadStageOutput.metrics`;
- `BuildingStageOutput.area_metrics`;
- `DemographyStageOutput.metrics` and demand profile;
- future S11 raw metric registry extending canonical `RawMetricId`.

**Evidence**

- Stage adapter tests assert these authoritative results are emitted once with the stage output;
- M0 spine passes typed outputs forward rather than recomputing upstream analysis.

S11/S12 compare and scoring work must consume persisted/raw authoritative results and provenance.
Any deliberate recomputation requires a task-specific reason; it must not become a parallel metric
pipeline.

## M0-07 exit

M0-07 closes when the two EXPANSION regression tests are CI-green together with the existing
evidence above. No production algorithm change is required.
