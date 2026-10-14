# Persisted fixed-road inputs for real generation

**State:** implemented as a bounded source read adapter; **not** a fully wired generation worker.

`backend.app.adapters.fixed_road_source_reader.SqlAlchemyFixedRoadSourceReader`
maps immutable `TerritorySnapshot.roads` dataset-version references to canonical
`FixedRoadStageInput` values (`SemanticRoad` and existing classification). This
closes one input-hydration boundary before `RoadStage`, without adding a second
execution model or changing the algorithm.

## Safety and scope

- Each source ref must be an exact `dataset-version:<uuid>:v<number>` identity.
- The version must be **ready**, belong to the project, have kind `osm` or
  `roads`, and be linked to the exact GenerationRun / snapshot.
- Only `SourceRoad` rows intersecting the stored project boundary are read.
  Rows are ordered by persisted source ID and row UUID for deterministic assembly.
- The reader enforces a hard **max_roads + 1** bound across all source versions
  (default 5,000, configurable up to 50,000), failing explicitly rather than
  silently dropping roads in large urban areas.
- Full original metric CRS line geometry and the `layer`, `bridge`, `tunnel`
  and `road_class` source semantics are preserved. It fails on mismatched CRS
  or malformed geometry and never writes to immutable source tables.
- Road IDs include their immutable dataset version to avoid collisions across
  multiple data sources. The `fixed:` namespace cannot masquerade as generated.

This reader is deliberately **not** a broad City->Memory export path. A future
production `GenerationInputResolver` should hydrate the fixed road inputs at
the roads stage using this adapter, together with upstream typed zoning,
suitability and constraint outputs. Production config decoding, stage
registration, result persistence, final metric evaluation, ARQ startup
composition and an unseeded real run remain release blocker #178.

## Verification

`tests/integration/test_fixed_road_source_reader.py` checks persisted PostGIS
source roads, boundary isolation, grade separation/class metadata,
deterministic ordering, run-version isolation, a hard capacity bound, invalid
reference rejection and unready-version rejection.

Do not represent a successful execution of these reader tests as proof that
the end-to-end generation pipeline has been completed.
