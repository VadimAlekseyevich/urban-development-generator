# Generated infrastructure persistence

> **Status: Implemented through UG-AI-034 / S10-T10**

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

## Existing spatial access paths

The pre-existing generated-layer indexes remain authoritative:

- B-tree `(run_id, id)`;
- GiST `(geometry)`.

UG-AI-033 adds only deterministic typed identity indexes. Additional query/index validation belongs
to UG-AI-035 together with database integration coverage.

## Retry-safe writer

UG-AI-034 adds `SqlAlchemyGeneratedInfrastructureWriter`. One replace call owns exactly one
`run_id + infrastructure_type_code` scope. It locks the GenerationRun, rejects a successful run,
validates all cross-stage references, resolves host-building ids, and only then deletes/reinserts
that type's rows in one transaction. Retrying one type therefore cannot erase generated facilities
of another type.

The writer consumes authoritative outputs rather than recomputing them:

- T01 `InfrastructureType` supplies category/type semantics;
- T05 `InfrastructureCandidateGeometryResult` supplies site geometry or host anchor/ref;
- T06 `InfrastructureNetworkSnapBatchResult` supplies snapshot/node/snap-distance provenance;
- T08 `InfrastructureGreedyPlacementState.accepted_facilities` supplies accepted candidates and
  contiguous acceptance order;
- T09 `InfrastructureFeasibilityResult` supplies the accepted proposed capacity and confirms the
  geometry kind is feasible.

Every accepted candidate must resolve in all relevant inputs and must have a successful candidate
snap. Validation happens before the first DELETE. Host-building string ids are resolved against
`GeneratedBuilding.building_key` in the same run. Generated row UUIDs are deterministic UUID5
values scoped by run, type code and candidate id.

The writer is explicitly bounded by the S10 placement facility hard limit and uses chunked INSERTs.
An empty accepted set is a valid authoritative replacement and clears only the selected run/type
scope.

## Explicit non-goals

UG-AI-034 does not:

- rerun site generation, feasibility, snapping, routing or greedy placement;
- add DB integration fixtures for retry/foreign-key/check/index behavior;
- compute infrastructure metrics;
- add infrastructure read API or UI.

Ordered follow-up:

- UG-AI-035 — add persistence/database integration tests and required run/spatial index evidence.
