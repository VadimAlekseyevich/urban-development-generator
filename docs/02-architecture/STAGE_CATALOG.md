# Stabilized Stage Catalog

> **Status: Implemented in part**
>
> This document defines coarse stage identity and the adapter ownership boundary used by M0.

## Canonical order

```text
prepare_snapshot
 -> evaluate_constraints
 -> suitability
 -> zoning
 -> roads
 -> blocks_and_parcels
 -> buildings
 -> demography
 -> infrastructure
 -> final_validation
 -> metrics
 -> persist_manifest
```

`infrastructure` also depends on `roads` because accessibility consumes the road network produced earlier.

Stable strings live in `core.urban_generator.stages.catalog`. They are constants, not a second execution enum. The Stage implementation remains the source of `name/version/dependencies`.

## Adapter ownership

### evaluate_constraints

Input owns:
- target SuitabilityGridSpec;
- resolved project boundary geometry;
- resolved hard vector/raster constraint layers.

Output:
- canonical HardExclusionMask.

This stage performs bounded hard-exclusion rasterization only. It does not persist artifacts or run later scope-specific ConstraintEngine rules.

### suitability

Stage instance owns resolved SuitabilityFactor implementations/ports.
Input owns:
- target grid;
- hard-exclusion mask.

Config owns canonical SuitabilityConfig and work bound.

Output:
- WeightedSuitabilityResult.

Factors execute through their existing contract. Aggregation remains `aggregate_weighted_suitability`; the stage does not duplicate normalization.

### zoning

Input owns:
- WeightedSuitabilityResult;
- metric developable-area geometry already resolved by orchestration.

Config owns:
- canonical ZoningConfig;
- seed count;
- bounded refinement iterations.

Stage composition owns the existing seed/partition/assignment/refinement/constraint evaluator implementations plus a ConstraintEngine port.

Output owns:
- partition;
- final refined assignment;
- canonical constraint evaluation;
- unchanged fixed-zone snapshot references.

The adapter does not persist generated zones and never mutates fixed source zones.

## Remaining M0 adapters

The following contracts must be defined/implemented before M0 exits:

- roads;
- blocks_and_parcels;
- buildings;
- demography.

Each must compose existing S06-S09 capabilities rather than copy them.

## Fingerprint rule

M0 adapter fingerprints include stage name/version, snapshot identity, run seed where relevant, versioned config identity, and canonical result/input bytes needed to distinguish semantic outputs.

Persistent checkpoint input/config hashing remains S12 scope and must not be inferred solely from these core fingerprints.


### roads

Input owns:
- stabilized zoning output;
- canonical suitability + hard mask;
- resolved fixed `SemanticRoad` values with preserved source class;
- optional forbidden geometries.

Config owns explicit bounded policies for endpoint snapping, candidate sampling, least-cost routing,
MST/growth, fixed-network attachment, graph cleanup, classification, validation and metrics.

Composition:

```text
fixed SemanticRoad
 -> endpoint snap -> semantic noding -> fixed graph
zoning+suitability -> anchors -> MST -> bounded growth
EXPANSION generated components -> explicit fixed-network attachment
generated+fixed semantic roads -> endpoint snap -> semantic noding -> graph build -> cleanup
 -> classification -> validation -> raw road metrics
```

Output owns candidate/baseline/growth provenance, optional attachment result, final `RoadGraph`,
`RoadClassificationResult`, `RoadValidationResult` and `RoadMetrics`. Persistence remains
outside core.

The adapter closes two integration gaps discovered by M0 without changing the S06 algorithms:
endpoint snapping is actually composed before noding/graph build, and EXPANSION generated
components are explicitly attached to the fixed network instead of relying on accidental geometric
crossings.
