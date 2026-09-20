# Infrastructure accessibility contract

> **Status: Implemented through UG-AI-021**

S10-T07 consumes the typed network-snap identities from S10-T06 and defines the stable records and
bounded executors used by infrastructure placement. This document covers **UG-AI-020..021**;
candidate batching, unreachable semantics and performance acceptance remain UG-AI-022..024.

## Goal

An accessibility relationship is identified by:

- one `infrastructure_type_code`;
- one `InfrastructureDemandRef`;
- one service location ref: either `ExistingInfrastructureFacilityRef` or
  `InfrastructureCandidateRef`.

The service-location family is part of the key, so an existing facility and candidate with the same
raw identifier never collapse to one accessibility record.

## Query contract

`InfrastructureAccessibilityQuery` contains:

- `snapshot_id` for the immutable network snapshot that owns the node refs;
- `infrastructure_type_code`;
- `demand_ref`;
- `facility_site_ref`;
- snapped `demand_node` and `facility_site_node` as canonical `NetworkNodeRef` values;
- explicit positive finite `max_network_distance_m`.

The demand and facility/site refs must carry the same infrastructure type code as the query.
`max_network_distance_m` is the service cutoff from the owning `InfrastructureType`; it is not
the T06 geometric snap tolerance.

The stable logical key is:

```text
(infrastructure_type_code, demand_block_id, facility_site_family, facility_site_id)
```

`snapshot_id` is provenance/context rather than part of that logical service key.

## Successful result contract

`InfrastructureAccessibilityResult` preserves the full query identity and network provenance and
adds finite non-negative `distance_m`.

A successful result must satisfy:

```text
distance_m <= max_network_distance_m
```

This contract therefore represents only a successful reachable relationship inside the configured
service cutoff. It never uses zero or infinity as a sentinel for missing reachability.

## Units and network boundary

All distances are metres on the canonical network snapshot. T07 does not create a second routing
backend and does not expose NetworkX objects. Executors added by later tasks must use the existing
`NetworkBackend` distance operations over the node refs produced by T06.

## Existing-facility executor

`compute_existing_facility_accessibility()` consumes one canonical
`InfrastructureNetworkSnapBatchResult`, one `InfrastructureType` and the canonical
`NetworkBackend`.

Before routing it verifies that the snap batch and backend use the same immutable network snapshot
and working SRID. It selects only successful demand and existing-facility snaps for the requested
infrastructure type, applies a hard subject bound, and returns no successful rows without making a
network call when either selected side is empty.

Existing facilities are passed as multi-source nodes and demand nodes as targets in exactly one
`NetworkBackend.multi_source_distances()` call. The routing cutoff is exactly
`InfrastructureType.max_network_distance_m`; T06 snap tolerance is never reused as a service
distance.

Repeated facility or demand node refs are deduplicated before routing. If multiple fixed facilities
share one network node, the stable facility ref ordering chooses the canonical facility identity for
that node. A reachable target node is then fanned back out to every demand ref snapped to that node.
Returned rows are canonically sorted by their accessibility key.

The default hard subject cap is
`MAX_EXISTING_ACCESSIBILITY_SUBJECTS = MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE`; callers may choose a
smaller bound but not a larger one.

## Explicit non-goals through UG-AI-021

This implementation does not:

- calculate candidate-site accessibility;
- materialize candidate×demand all-pairs data;
- define unreachable/unsnapped result variants;
- calculate candidate benefit or place facilities;
- persist accessibility rows.

Those responsibilities remain ordered as:

- UG-AI-022 — candidate-site bounded batching;
- UG-AI-023 — explicit unreachable/unsnapped handling;
- UG-AI-024 — deterministic graph acceptance and search-budget assertions.
