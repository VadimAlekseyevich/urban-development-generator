# Infrastructure network-snap contract

S10-T06 starts by defining the typed boundary between infrastructure subjects and the canonical
road-network port.

This document covers **UG-AI-015 only**. It defines records; it does not yet choose snap policy or
execute snapping.

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

## Deferred to the next UG-AI tasks

UG-AI-015 deliberately does **not** define:

- maximum snap distance;
- graph/input CRS compatibility policy;
- unsnapped reason codes or diagnostics;
- batch-size limits;
- batch execution/order;
- calls to `NetworkBackend.snap()`.

Those belong to UG-AI-016 and UG-AI-017.

This keeps T06 layered as:

```text
typed subject/ref records
 -> explicit snap policy/bounds
 -> bounded deterministic NetworkBackend.snap() adapter
 -> acceptance/permutation tests
 -> final boundary documentation
```
