# Infrastructure network-snap contract

> **Status: Implemented — S10-T06 / UG-AI-015..019**

S10-T06 starts by defining the typed boundary between infrastructure subjects and the canonical
road-network port.

This document covers the complete S10-T06 contract through **UG-AI-019**. It defines typed
records, policy/diagnostics semantics, the bounded deterministic batch executor, acceptance
coverage and the enforced ownership boundary.

## Stable subject references

Three subject families remain distinct:

- `InfrastructureDemandRef(block_id, infrastructure_type_code)`;
- `ExistingInfrastructureFacilityRef(facility_id, infrastructure_type_code)`;
- `InfrastructureCandidateRef(candidate_id, infrastructure_type_code)`.

Each reference can be derived directly from the authoritative S10 record produced by earlier work:

- `BlockInfrastructureDemand`;
- `ExistingInfrastructureFacility`;
- `InfrastructureCandidateGeometry`.

The separate dataclass types intentionally prevent a demand item, fixed facility and candidate from
collapsing into one untyped string identity.

## Snap input records

Each subject family has a typed input record containing:

- the stable subject reference;
- a backend-independent `NetworkPoint`;
- an explicit metric `working_srid`.

The point is already resolved by the owning infrastructure adapter before network snapping. T06
does not move geometry ownership into the road backend.

## Successful snap records

Each successful snap record contains:

- the same typed subject reference;
- a backend-independent `NetworkNodeRef`;
- finite non-negative `distance_m`.

No NetworkX node object, STRtree object or persistence model crosses this boundary.

## Snap policy and compatibility

`InfrastructureNetworkSnapPolicy` owns a dedicated `max_snap_distance_m`. It is deliberately
separate from `InfrastructureType.max_network_distance_m`: snap tolerance is a geometric
attachment rule, while the type-level value is a later service/accessibility distance.

A snap tolerance of zero is valid for exact-node matches.

All snap inputs and the network snapshot must use the exact same validated metric working SRID.
A CRS mismatch invalidates the batch; it is not reported as an unsnapped subject.

The batch contract has a hard maximum of
`MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE = 100_000` subjects. Policies may choose a smaller
bound but not a larger one.

## Unsnapped semantics

A valid subject may remain unsnapped for exactly two recoverable reasons at this boundary:

- `EMPTY_NETWORK` — the network snapshot has no nodes;
- `NO_NODE_WITHIN_MAX_DISTANCE` — a non-empty network has no routable node inside the configured
  snap radius.

Invalid CRS, invalid records and batch-limit violations are contract errors rather than unsnapped
results.

`InfrastructureNetworkSnapDiagnostics` conserves total input count and requires all unsnapped
items to be accounted for by one of the typed reasons.

## Batch execution

`snap_infrastructure_network_batch()`:

1. validates the batch against the network snapshot and snap policy;
2. canonicalizes inputs by subject family and stable reference;
3. rejects duplicate subject identities before any network call;
4. returns `EMPTY_NETWORK` for every subject without calling the backend when the snapshot has
   zero nodes;
5. otherwise calls only `NetworkBackend.snap(point, max_distance_m=...)`;
6. converts successful matches to typed snap records and `None` to
   `NO_NODE_WITHIN_MAX_DISTANCE`;
7. emits a canonically sorted `InfrastructureNetworkSnapBatchResult` with conserved diagnostics.

The executor does not call shortest-path or multi-source routing methods.

## Acceptance coverage

UG-AI-018 proves the executor against a real `NetworkXBackend` adapter for:

- exact node hits with zero snap distance;
- deterministic equal-distance ties;
- points outside the configured snap limit;
- empty-network accounting for every subject;
- mixed snapped/unsnapped batches;
- complete result equality under input permutation.

## Ownership boundary

Infrastructure owns subject identity, metric snap inputs, tolerance/batch policy, deterministic
ordering and typed diagnostics. It depends on the backend-independent network domain port only.

Production modules under `core/urban_generator/infrastructure/` must not:

- import `networkx` directly;
- import `core.urban_generator.roads.spatial_snapping` directly;
- import or construct `SpatialSnapIndex`;
- implement a second network nearest-node index or graph-specific tie-breaking path.

NetworkX graph ownership and `SpatialSnapIndex` stay inside the road-network adapter layer.
Infrastructure requests snapping only through `NetworkBackend.snap()` and receives only canonical
domain records such as `NetworkNodeRef` and `NetworkSnapResult`.

The acceptance test module may instantiate the real `NetworkXBackend` to prove port behavior; that
does not move NetworkX into production infrastructure code.

The rule is executable:
`tests/unit/test_architecture_boundaries.py::test_infrastructure_network_snap_does_not_bypass_network_backend`
fails required pytest CI if infrastructure imports NetworkX or `SpatialSnapIndex` directly.

## S10-T06 completion

UG-AI-015..019 are complete when required HEAD CI is green. T06 remains layered as:

```text
typed subject/ref records
 -> explicit snap policy/bounds
 -> bounded deterministic NetworkBackend.snap() adapter
 -> acceptance/permutation tests
 -> enforced NetworkBackend-only boundary
```

S10-T07 consumes these snap results through the typed contract in
`docs/INFRASTRUCTURE_ACCESSIBILITY.md`. Routing remains behind `NetworkBackend`, and pathfinding
must not move into the later placement loop.
