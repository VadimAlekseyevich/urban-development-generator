# M0 In-memory execution spine

> **Gate:** M0-05

The stabilization branch proves the canonical stage family with two complementary evidence sets.

## Full EXPANSION spine

`tests/integration/test_in_memory_generation_spine.py` executes, without FastAPI, SQLAlchemy,
Redis or ARQ:

```text
evaluate_constraints
 -> suitability
 -> zoning
 -> roads
 -> blocks_and_parcels
 -> buildings
 -> demography
```

The synthetic fixture contains one fixed square road network. Generated road work attaches to it,
the resulting RoadGraph feeds block/parcel generation, generated-zone refs feed block association,
building outputs feed demography, and the full semantic fingerprint sequence is stable on rerun.

## FROM_SCRATCH evidence

The same Stage implementations are used in FROM_SCRATCH mode; no alternate pipeline exists.

Mode-specific tests remain authoritative for empty fixed-state behavior:

- `test_road_stage_adapter.py::test_from_scratch_road_stage_builds_generated_graph_without_fixed_state`;
- `test_building_stage_adapter.py::test_building_stage_composes_s08_pipeline_deterministically`;
- `test_demography_stage_adapter.py::test_demography_stage_composes_s09_pipeline_deterministically`.

The full EXPANSION fixture is intentionally not duplicated with hand-injected source roads under
FROM_SCRATCH, because doing so would violate the mode contract merely to satisfy a test shape.

## M0-05 acceptance

M0-05 may close when:

- the full EXPANSION spine is CI-green;
- deterministic rerun yields the same ordered Stage fingerprints;
- fixed road ownership survives the road stage;
- downstream stage inputs are actual outputs of the preceding canonical stage;
- the existing FROM_SCRATCH mode tests remain green;
- architecture-boundary tests confirm core remains infrastructure-independent.
