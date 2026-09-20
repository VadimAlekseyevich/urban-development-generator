# Infrastructure greedy placement contract

> **Status: Implemented through UG-AI-027**

S10-T08 consumes the completed S10-T07 accessibility outputs. UG-AI-025 defines the immutable
state vocabulary; UG-AI-026 adds pure incremental candidate-benefit calculation; UG-AI-027 adds
bounded deterministic candidate selection. Demand mutation remains the next ordered task.

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

The ordering is intentionally independent of benefit. UG-AI-026 returns benefits in this order,
and UG-AI-027 defines bounded iteration and benefit-tie resolution using the same stable order.

## Incremental candidate benefit

`calculate_infrastructure_candidate_benefits()` is a pure transformation over the current
`InfrastructureGreedyPlacementState` and canonical `InfrastructureType`. It performs no snapping,
routing or spatial search.

For each **unaccepted** candidate, it sums current `remaining_demand` only for demand refs present
in that candidate's T07 successful coverage-cache rows:

```text
reachable_remaining_demand = sum(current remaining demand for cached reachable demand refs)
benefit = min(InfrastructureType.capacity, reachable_remaining_demand)
```

The configured type capacity is the nominal maximum service contribution of one proposed facility.
S10-T09 still owns whether that capacity is spatially/site feasible; UG-AI-026 does not perform that
feasibility decision.

Every unaccepted candidate receives one `InfrastructureCandidateBenefit`, including zero-benefit
candidates. Already accepted candidates are omitted. Results preserve `candidate_order`, allowing
UG-AI-027 to apply deterministic winner/tie rules without recomputing reachability.

Network distance is used only to establish T07 reachability. Once a row is in the cache, benefit is
unweighted demand coverage; distance-weighted scoring is not introduced.

## Bounded deterministic selection

`InfrastructureGreedyPlacementPolicy` defines explicit positive `max_facilities` and
`max_iterations`. Both are capped by the existing placement candidate hard limit. The
`iteration_index` passed to selection is zero-based: an index equal to `max_iterations` is
already outside the allowed selection budget.

`select_infrastructure_greedy_candidate()` validates that benefits contain every currently
unaccepted candidate exactly once and in canonical `candidate_order`. It then applies stop
conditions in deterministic order:

1. facility limit reached;
2. iteration limit reached;
3. no unaccepted candidates;
4. no positive benefit.

Otherwise it selects the highest-benefit candidate. Benefits are traversed in canonical order and
the selected item is replaced only by a **strictly greater** benefit, so exact benefit ties resolve
to the earliest canonical candidate ref without randomness.

The function returns `InfrastructurePlacementSelection` with a typed
`InfrastructurePlacementSelectionStatus`; it does not mutate state. UG-AI-028 owns the state
transition after a `SELECTED` decision.

## Bounds

Placement state reuses the existing T07 hard envelopes:

- candidate count <= `MAX_CANDIDATE_ACCESSIBILITY_CANDIDATES`;
- demand count <= `MAX_CANDIDATE_ACCESSIBILITY_DEMANDS`;
- candidate×demand subjects / cached successful rows remain within
  `MAX_CANDIDATE_ACCESSIBILITY_RESULTS`.

No new unbounded N×M path is introduced.

## Explicit non-goals through UG-AI-027

This task does not:

- run pathfinding or snapping;
- update remaining demand;
- perform site/capacity feasibility;
- persist generated facilities.

Ordered follow-up remains:

- UG-AI-028 — remaining-demand update;
- UG-AI-029 — greedy acceptance fixtures.
