# Infrastructure network-snap contract

S10-T06 starts by defining the typed boundary between infrastructure subjects and the canonical
road-network port.

This document covers the S10-T06 contract through **UG-AI-016**. It defines typed records and
policy/diagnostics semantics; it does not yet execute snapping.

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

## Deferred to the next UG-AI tasks

UG-AI-017/018/019 still own:

- deterministic batch ordering and duplicate handling;
- calls to `NetworkBackend.snap()`;
- mapping backend `None` results to typed unsnapped records;
- exact-hit/tie/outside-limit/empty-network/mixed-result acceptance tests;
- final T06 boundary closure.

This keeps T06 layered as:

```text
typed subject/ref records
 -> explicit snap policy/bounds
 -> bounded deterministic NetworkBackend.snap() adapter
 -> acceptance/permutation tests
 -> final boundary documentation
```
