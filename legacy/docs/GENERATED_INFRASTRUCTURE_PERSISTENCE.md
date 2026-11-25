# Generated infrastructure persistence

> **Status: Complete through UG-AI-035 / S10-T10**

S10-T10 persists accepted generated facilities in the existing run-scoped
`generated_infrastructure` table. UG-AI-033 specializes that table with typed columns and a
migration while preserving the generic generated-entity invariants established in S02.

## Ownership and identity

Every row remains owned by one `GenerationRun` through `run_id`. The existing generated-entity
trigger continues to require geometry in the run working CRS and prevents mutation after the run
reaches `succeeded`.

The typed identity is:

- `candidate_id` — the stable T04/T05 candidate id;
- `infrastructure_type_code` — the S10 `InfrastructureType.code`;
- `acceptance_index` — zero-based greedy acceptance order within one infrastructure type.

`(run_id, candidate_id, infrastructure_type_code)` and
`(run_id, infrastructure_type_code, acceptance_index)` are unique when typed values are present.
The persistence UUID remains an adapter concern; core infrastructure contracts continue to use
stable string ids.

## Facility semantics

Typed rows persist:

- `category` using the canonical S10 categories `education`, `healthcare`, `retail`,
  `recreation`;
- positive `capacity`;
- `geometry_kind` using the T05 vocabulary `site` or `host_building`.

The table existed before S10-T10, so the migration permits one legacy shape where every new typed
column is null. New T10 rows use the complete typed shape; partial typed rows are rejected by a
database check constraint.

## Site versus host-building representation

A `site` row stores the prepared T05 Polygon/MultiPolygon in the existing `geometry` column and
requires positive `site_area_m2`. It must not set `host_building_id`.

A `host_building` row stores the T05 candidate anchor Point in `geometry`, requires
`host_building_id` referencing `generated_buildings.id`, and must not set `site_area_m2`.
The host reference uses restrictive deletion rather than cascade so an infrastructure row cannot
silently lose its host due to a building replacement.

S10-T10 does not duplicate or regenerate a host-building footprint. The authoritative host
building geometry stays on `generated_buildings`.

## Network provenance

Accepted generated facilities also persist the successful T06 snap provenance:

- `network_snapshot_id`;
- `network_node_id`;
- non-negative `network_snap_distance_m`.

These are backend-independent string/network references from the core contract. Persistence does
not store a NetworkX node object or rerun snapping.

## Retry-safe writer

`SqlAlchemyGeneratedInfrastructureWriter` consumes one completed
`InfrastructureGreedyPlacementState`, its exact T05
`InfrastructureCandidateGeometryResult`, the T06 network-snap batch and the canonical
`InfrastructureType`.

Before deleting any existing rows it:

- locks and loads the target `GenerationRun`;
- rejects `succeeded` runs;
- checks run/type/working-CRS/network-snapshot provenance;
- requires T05 geometry to cover exactly the placement candidate order;
- requires every accepted candidate to have a successful T06 candidate snap;
- resolves every host-building string id to a `generated_buildings` row owned by the same run.

Replacement scope is only `(run_id, infrastructure_type_code)`. Retrying one infrastructure type
therefore cannot delete another type. Inserts are chunked, total accepted rows are bounded by the
existing placement hard limit, and all validation/ref resolution happens before the destructive
delete.

Persisted UUID identity is deterministic:

`uuid5(run_id, "generated-infrastructure:<type-code>:<candidate-id>")`.

Site rows reuse the exact prepared T05 polygon. Host-building rows persist the T05 anchor Point and
the resolved generated-building UUID. The writer never reruns site generation, snapping or routing.

## Existing spatial access paths

The pre-existing generated-layer indexes remain authoritative:

- B-tree `(run_id, id)`;
- GiST `(geometry)`.

UG-AI-033 adds only deterministic typed identity indexes. Additional query/index validation belongs
to UG-AI-035 together with database integration coverage.

## Database integration evidence

UG-AI-035 exercises the writer against migrated PostgreSQL/PostGIS rather than only ORM metadata.
The integration fixture proves that:

- site and host-building rows persist their typed capacity/geometry/network provenance;
- host-building refs resolve to generated buildings owned by the same run;
- retry replaces only the target `run_id + infrastructure_type_code` scope;
- deterministic UUID identity survives replacement;
- invalid host refs fail before existing rows are deleted;
- successful-run writes are rejected;
- the database rejects partial typed rows and site/host geometry-shape mismatches;
- `generated_infrastructure` retains the canonical `(run_id, id)` B-tree and GiST geometry
  index, while the typed `(run_id, infrastructure_type_code, acceptance_index)` unique index
  covers the writer/read scope prefix.

No extra speculative index is added: the required run/type prefix is already provided by the typed
acceptance index and spatial predicates remain covered by the existing GiST index.

## Explicit non-goals after S10-T10

Persistence does not:

- recompute infrastructure metrics;
- add infrastructure read API or UI;
- rerun routing, site generation, feasibility or placement.

Ordered follow-up:

- UG-AI-036 / S10-T11 — compute canonical infrastructure raw metrics from T07-T10 results without
  rerunning routing.
