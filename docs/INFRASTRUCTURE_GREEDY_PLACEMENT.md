# Infrastructure greedy placement contract

> **Status: Implemented through UG-AI-025**

S10-T08 consumes the completed S10-T07 accessibility outputs. UG-AI-025 defines the immutable
state vocabulary only; benefit calculation, candidate acceptance, demand mutation and placement
termination remain ordered follow-up tasks.

## State ownership

`InfrastructureGreedyPlacementState` owns one infrastructure type and one network snapshot. It
contains:

- `remaining_demand`: canonically sorted `InfrastructurePlacementDemandState` records;
- `accepted_facilities`: accepted candidate refs in explicit acceptance sequence;
- `coverage_cache`: one `InfrastructureCoverageCacheEntry` per candidate;
- `candidate_order`: the stable total order used by later deterministic tie-breaking.

The state is frozen and does not expose a mutable dictionary or graph object.

## Remaining demand

Each `InfrastructurePlacementDemandState` carries the canonical T06
`InfrastructureDemandRef`, the demand amount at placement initialization, and the current
remaining amount.

Initialization uses `BlockInfrastructureDemand.unmet_demand` as both values. Remaining demand
must stay finite, non-negative and no greater than its initial amount. UG-AI-028 owns the actual
state transition after a facility is accepted.

## Accepted facilities

Before S10-T10 persistence, an accepted generated facility is identified by the canonical
`InfrastructureCandidateRef` that won a greedy iteration. `InfrastructureAcceptedFacility`
stores that ref and a zero-based contiguous `acceptance_index`.

UG-AI-025 does not assign capacity, geometry feasibility or persisted generated identity here.
Those remain owned by the existing `InfrastructureType`, S10-T09 and S10-T10 contracts.

## Coverage cache

The cache reuses successful `InfrastructureAccessibilityResult` rows from T07. It does not define
another distance or reachability record.

Each candidate has exactly one cache entry, including candidates with no reachable demand. Inside
an entry, successful T07 rows are sorted and unique by demand ref. The cache preserves snapshot and
infrastructure-type provenance.

The canonical initializer validates that the supplied candidate accessibility batch is complete
for exactly the supplied demand×candidate subjects, including T07 unavailable outcomes. After that
validation, absence from a candidate's successful cache means "not reachable in the complete T07
batch", not "not computed".

## Deterministic candidate order

`candidate_order` is the ascending canonical `InfrastructureCandidateRef.key` order. Input tuple
permutation therefore cannot change the fallback ordering used by later greedy tie-breaking.

The ordering is intentionally independent of benefit. UG-AI-026 computes benefit from cached T07
results, and UG-AI-027 defines bounded iteration and benefit-tie resolution using this stable order.

## Bounds

Placement state reuses the existing T07 hard envelopes:

- candidate count <= `MAX_CANDIDATE_ACCESSIBILITY_CANDIDATES`;
- demand count <= `MAX_CANDIDATE_ACCESSIBILITY_DEMANDS`;
- candidate×demand subjects / cached successful rows remain within
  `MAX_CANDIDATE_ACCESSIBILITY_RESULTS`.

No new unbounded N×M path is introduced.

## Explicit non-goals through UG-AI-025

This task does not:

- calculate candidate benefit;
- run pathfinding or snapping;
- accept a candidate automatically;
- update remaining demand;
- impose facility/iteration stopping limits;
- perform site/capacity feasibility;
- persist generated facilities.

Ordered follow-up remains:

- UG-AI-026 — incremental candidate benefit from the T07 cache;
- UG-AI-027 — bounded facilities/iterations and deterministic tie-breaking;
- UG-AI-028 — remaining-demand update;
- UG-AI-029 — greedy acceptance fixtures.
