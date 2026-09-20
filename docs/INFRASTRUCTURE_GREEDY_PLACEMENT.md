# Infrastructure greedy placement contract

> **Status: S10-T08 complete through UG-AI-029**

S10-T08 consumes the completed S10-T07 accessibility outputs. UG-AI-025 defines the immutable
state vocabulary; UG-AI-026 adds pure incremental candidate-benefit calculation; UG-AI-027 adds
bounded deterministic candidate selection; UG-AI-028 applies accepted selections to remaining demand. UG-AI-029 closes the capability with deterministic acceptance fixtures.

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

## Remaining-demand transition

`apply_infrastructure_greedy_selection()` applies only a `SELECTED` decision. It never reruns
routing and preserves the T07 coverage cache unchanged.

Before changing state, the function verifies that:

- the selected candidate has not already been accepted;
- the selected capacity matches the canonical `InfrastructureType.capacity`;
- the selected candidate still has the same current reachable remaining demand and capacity-capped
  benefit. A decision calculated from an older state is rejected as stale.

Capacity is then consumed only from demand refs present in the selected candidate's successful T07
cache rows, in canonical demand-ref order. For each demand:

```text
served = min(current_remaining_demand, remaining_facility_capacity)
new_remaining_demand = current_remaining_demand - served
```

Both quantities are clamped by construction at zero. Because every transition uses the **current**
remaining demand and an already accepted candidate cannot be applied again, overlapping candidates
cannot double-cover demand. The total reduction is checked against the selected incremental benefit.

The resulting state appends one `InfrastructureAcceptedFacility` with the next contiguous
acceptance index while retaining the same snapshot, candidate order and coverage cache.

## Bounds

Placement state reuses the existing T07 hard envelopes:

- candidate count <= `MAX_CANDIDATE_ACCESSIBILITY_CANDIDATES`;
- demand count <= `MAX_CANDIDATE_ACCESSIBILITY_DEMANDS`;
- candidate×demand subjects / cached successful rows remain within
  `MAX_CANDIDATE_ACCESSIBILITY_RESULTS`.

No new unbounded N×M path is introduced.

## Greedy acceptance fixtures

UG-AI-029 composes the complete T08 path over typed T07 candidate-accessibility batches and asserts:

- a small known-optimum matrix selects the expected two facilities and reduces total remaining
  demand to zero;
- demand saturation stops with `NO_POSITIVE_BENEFIT` before a redundant candidate is accepted;
- exact-benefit ties select the same canonical candidate under candidate input permutation;
- a complete T07 matrix with no reachable candidate-demand rows stops without changing demand.

Within this T08 acceptance, "no feasible candidate" means no candidate has positive coverable demand
from the completed T07 reachability matrix. Site/capacity geometry feasibility remains S10-T09 and
is deliberately not simulated here.

## Explicit non-goals after S10-T08

This task does not:

- run pathfinding or snapping;
- perform site/capacity feasibility;
- persist generated facilities.

Ordered follow-up moves to the next capability:

- UG-AI-030 / S10-T09 — define capacity/site/host-building feasibility results and rejection
  reasons.
