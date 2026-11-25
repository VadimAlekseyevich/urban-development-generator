# Infrastructure feasibility contract

> **Status: Complete through UG-AI-032**

S10-T09 decides whether a generated infrastructure candidate can physically host the proposed
facility capacity. UG-AI-030 defines the result and rejection vocabulary, UG-AI-031 implements the
validator over already prepared T05 geometry, and UG-AI-032 gates greedy acceptance with those
typed results.

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

## Validator

`validate_infrastructure_candidate_feasibility()` consumes one already prepared
`InfrastructureCandidateGeometry`, its owning `InfrastructureType` and a positive proposed
capacity.

For `SITE` candidates, the validator uses the exact T05 `site_geometry` /
`site_area_m2` already stored on the candidate. It never calls the candidate geometry builder,
clips the polygon again or creates a replacement site.

For `HOST_BUILDING` candidates, the caller may supply an explicit polygonal building
footprint/envelope together with its working SRID. The geometry must be non-empty, valid, 2D,
positive-area and expressed in the same working CRS as the T05 candidate. If no explicit geometry
is supplied, the normal feasibility result is
`HOST_BUILDING_GEOMETRY_UNAVAILABLE`; the validator does not infer or search for another building.

The validator applies all applicable hard checks in one decision:

- proposed capacity greater than `InfrastructureType.capacity`;
- explicit site area below `minimum_site_area_m2`;
- host-building geometry unavailable;
- explicit host-building area below `minimum_site_area_m2`.

Equality at the capacity and minimum-area boundaries is feasible. Malformed geometry, mismatched
type identity or CRS provenance raises `InfrastructureFeasibilityError` instead of being silently
converted into ordinary infeasibility.

## Greedy acceptance gate

`select_infrastructure_greedy_candidate()` accepts an optional immutable tuple of
`InfrastructureFeasibilityResult` values for the currently unaccepted candidates. When supplied,
the tuple must match the canonical candidate order exactly and each result's `proposed_capacity`
must match the corresponding candidate benefit capacity.

Hard-infeasible candidates are removed from winner comparison before any greedy acceptance. A
higher-benefit infeasible candidate therefore cannot displace a lower-benefit feasible candidate.
If unaccepted candidates remain but every supplied result is infeasible, selection returns
`NO_FEASIBLE_CANDIDATES`; this is distinct from `NO_POSITIVE_BENEFIT`.

The gate consumes completed T09 results only. It does not regenerate site geometry, resolve another
host building, rerun snapping/routing, or mutate remaining demand.

## Explicit non-goals after S10-T09

This capability does not:

- regenerate or clip candidate sites;
- infer a new host building;
- persist generated infrastructure;
- define infrastructure metrics.

Ordered follow-up:

- UG-AI-033 / S10-T10 — add the typed `GeneratedInfrastructure` persistence schema/migration.
