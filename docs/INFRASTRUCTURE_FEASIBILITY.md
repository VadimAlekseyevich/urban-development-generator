# Infrastructure feasibility contract

> **Status: Implemented through UG-AI-030**

S10-T09 decides whether a generated infrastructure candidate can physically host the proposed
facility capacity. UG-AI-030 defines the result and rejection vocabulary only. The actual validator
is ordered separately as UG-AI-031.

## Contract boundary

`InfrastructureFeasibilityResult` records one deterministic feasibility decision for one T05
`InfrastructureCandidateGeometry` identity.

The result carries:

- `candidate_id`;
- `infrastructure_type_code`;
- metric `working_srid`;
- the existing T05 `InfrastructureCandidateGeometryKind` (`SITE` or `HOST_BUILDING`);
- positive finite `proposed_capacity`;
- `is_feasible`;
- canonical, unique hard `rejection_reasons`.

`is_feasible` is true exactly when the rejection tuple is empty.

No competing site, building, capacity or geometry vocabulary is introduced.

## Hard rejection reasons

The canonical hard reasons are:

- `CAPACITY_EXCEEDS_TYPE_CAPACITY` — proposed capacity exceeds the
  `InfrastructureType.capacity` ceiling;
- `SITE_AREA_BELOW_MINIMUM` — an explicit T05 polygon site is smaller than
  `InfrastructureType.minimum_site_area_m2`;
- `HOST_BUILDING_GEOMETRY_UNAVAILABLE` — a T05 host-building reference cannot be resolved to
  explicit building geometry for feasibility evaluation;
- `HOST_BUILDING_AREA_BELOW_MINIMUM` — the resolved host-building footprint/envelope is smaller
  than the same minimum area requirement.

Capacity rejection is valid for both geometry kinds. Site-only and host-building-only rejection
reasons are mutually constrained by the result type.

## Errors versus infeasibility

Malformed or inconsistent provenance is **not** represented as a feasibility rejection. Invalid
candidate/type IDs, invalid enum values, non-positive/non-finite capacity or wrong result-shape
combinations raise `InfrastructureFeasibilityError`; invalid working CRS remains owned by the
canonical `require_working_crs` guard.

This distinction prevents damaged inputs from being silently treated as an ordinary rejected
candidate.

## Spatial units and ownership

All area checks in S10-T09 operate in the project working CRS and square metres. T05 owns candidate
site creation and host-building identity. S10-T09 consumes those outputs and must not regenerate or
reshape sites.

UG-AI-031 will resolve explicit site/host geometry and evaluate the rejection reasons. UG-AI-032
will integrate that result before greedy acceptance.

## Explicit non-goals through UG-AI-030

This task does not:

- calculate feasibility;
- regenerate or clip candidate sites;
- infer a new host building;
- alter greedy placement state;
- persist generated infrastructure;
- define infrastructure metrics.

Ordered follow-up:

- UG-AI-031 — validate proposed capacity against `InfrastructureType` and explicit site/host
  geometry without regenerating sites;
- UG-AI-032 — filter greedy acceptance by feasibility and test impossible/edge capacities.
