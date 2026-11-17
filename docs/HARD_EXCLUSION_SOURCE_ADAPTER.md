# P0 #202 — fixed boundary and hard-exclusion source hydration

`SqlAlchemyHardExclusionSourceReader` is a **read-only PostGIS adapter** for
the existing canonical `ConstraintMaskStageInput` types. It is one focused
piece of the remaining production generation integration, **not** a new
scheduler, stage, fabricated dataset, or an end-to-end working city generator.

A `TerritorySnapshot` from `SqlAlchemyPipelineContextAdapter.assemble`
contains opaque `SnapshotLayerRef` values. This adapter turns the already
persisted **project boundary**, **water geometries** and **HARD/TERRITORY**
constraints into:

- `HardExclusionBoundary` with the precise metric PostGIS polygon;
- `HardExclusionGeometryLayer` per nonempty ready dataset version;
- `source_feature_count` for the exact selected spatially matching records.

The output is passed into `ConstraintMaskStageInput(grid=..., boundary=...,
geometry_layers=...)` only after the caller chooses an explicit metric
`SuitabilityGridSpec`. It is not invoked by the generation worker yet.

## Read invariants

- Fail closed if the snapshot run/project/working SRID, canonical project
  boundary digest, linked dataset version number, ready state or dataset kind
  do not match PostgreSQL. A forged, foreign or stale `SnapshotLayerRef`
  cannot read another project's geometries.
- Water comes from `source_water`; hard territory exclusions come from
  `source_constraints` where `severity=HARD` and
  `scope=TERRITORY`. Soft and narrow-scope restrictions are **not silently
  interpreted as blanket no-build territory constraints**; those require
  separate typed policy/adapters downstream.
- Geometry records remain **fixed source inputs**. This adapter reads and
  never writes to source tables. No implicit reprojection, buffering of
  points, dissolving or geometry repair is attempted.
- An explicit indexed `ST_Intersects` predicate excludes entirely outside
  features; geometries spanning the boundary remain unchanged. The canonical
  `build_hard_exclusion_mask` applies the actual project boundary at
  rasterization time.
- `ST_SRID` is checked on the entire referenced version before spatial
  intersection. Bad CRS, invalid/degenerate geometry and unsupported point
  geometry are errors, not dropped features.
- Rows are deterministic by source_feature_id/UUID. `LIMIT(N+1)` on the
  spatially matching result is a **fail-closed bound**, not truncation; the
  total cap spans all water and constraint sources (default 10 000; maximum
  configurable 100 000). This protects Python memory after PostgreSQL
  spatial filtering, **not** the database's internal scan/peak memory.
- No Redis, external source download, artifact write, queued Job,
  GenerationRun mutation or new canonical Stage implementation.

## Real PostgreSQL integration gate

```sh
uv run pytest tests/integration/test_hard_exclusion_source_reader.py
uv run ruff check .
uv run mypy backend core worker
uv run pytest
```

The synthetic PostGIS fixtures prove linked ready geometry hydration,
boundary preservation, selective HARD/TERRITORY semantics, stable ordering,
limits, rejected forged references and CRS errors. This is not a real
territory acceptance result. The separate existing
`SqlAlchemyFixedRoadSourceReader` already supplies fixed roads, while
other source categories (DEM, buildings, facilities, landuse) require
separately reviewed typed consumers. Issue #202 remains P0 until the
complete production DAG, output publication and unseeded API-run worker E2E
are proven.
