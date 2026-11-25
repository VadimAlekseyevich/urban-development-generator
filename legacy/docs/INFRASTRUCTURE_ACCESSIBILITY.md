# Infrastructure accessibility contract

> **Status: Implemented through UG-AI-024**

S10-T07 consumes the typed network-snap identities from S10-T06 and defines the stable records and
bounded executors used by infrastructure placement. This document covers **UG-AI-020..024** and
closes S10-T07.

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

## Candidate-site executor

`compute_candidate_site_accessibility()` preserves one accessibility row per reachable
candidate-demand relationship, so it cannot use multiple candidates as sources in one
`multi_source_distances()` call: that port intentionally returns only the nearest source per
target.

Instead, the executor groups candidate refs and demand refs by snapped network node. For each
unique candidate node it performs bounded demand-target batches with exactly one source node. This
reuses one graph search across many demand targets while still preserving per-candidate
accessibility. Candidate refs sharing a node reuse the same searches and fan the successful
distances back out to their stable refs.

`InfrastructureCandidateAccessibilityPolicy` bounds:

- selected candidate refs;
- selected demand refs;
- demand nodes per backend call;
- total projected routing calls;
- maximum possible/materialized candidate-demand result rows.

The pair-result and routing-call budgets are checked before the first backend call. This prevents a
configuration from silently materializing or executing an unbounded candidate×demand workload.
Every backend call uses exactly `InfrastructureType.max_network_distance_m`, and final successful
rows are canonically sorted.

## Unavailable outcomes

UG-AI-023 adds `InfrastructureAccessibilityUnavailable` and
`InfrastructureAccessibilityBatchResult`. An unavailable outcome has **no `distance_m` field**.
Missing accessibility is therefore never represented by zero, infinity, NaN or another numeric
sentinel.

Typed reasons are:

- `DEMAND_UNSNAPPED`;
- `FACILITY_SITE_UNSNAPPED`;
- `BOTH_UNSNAPPED`;
- `NO_PATH_WITHIN_MAX_DISTANCE`;
- `NO_SNAPPED_FACILITY_SITE` for the demand-level existing-facility search.

Pair-specific candidate snap failures and demand snap failures preserve the original
`InfrastructureNetworkUnsnappedReason` from T06 in dedicated fields. A routing miss after both
sides are snapped becomes
`NO_PATH_WITHIN_MAX_DISTANCE` and carries no snap reason.

`compute_existing_facility_accessibility_batch()` produces one outcome per demand ref: either the
nearest reachable existing facility or a demand-level unavailable record.

`compute_candidate_site_accessibility_batch()` produces exactly one outcome per bounded
candidate-demand pair. Its pair budget includes snapped **and unsnapped** refs before routing, so
explicit unavailable materialization cannot bypass the UG-AI-022 result bound.

Both batch results expose typed diagnostics whose reachable and unavailable counts conserve the
number of logical accessibility subjects.

## Synthetic graph and search-budget acceptance

UG-AI-024 exercises T07 through the production `NetworkXBackend` adapter on small in-memory
synthetic graphs whose coordinates and edge lengths are in metres in EPSG:3857.

Acceptance proves three properties:

- equivalent graphs built with opposite node/edge insertion order produce identical existing- and
  candidate-accessibility batch results, including deterministic equal-distance facility ties;
- candidate accessibility performs the expected bounded number of real
  `multi_source_distances()` searches: unique candidate nodes multiplied by bounded demand
  batches, while preserving the configured service-distance cutoff on every call;
- the adapter-level `max_routing_visited_nodes` guard is exercised through the real T07 call path:
  a fixture passes at the documented five-node search envelope and fails fast when reduced to four.

These are structural performance assertions rather than wall-clock thresholds. Machine-dependent
reference timing and larger candidate×demand benchmark envelopes remain S10-T14 / UG-AI-044..045,
so unit CI does not acquire a flaky timing gate.

## Explicit non-goals through UG-AI-024

This implementation does not:

- materialize candidate×demand data beyond the configured result budget;
- calculate candidate benefit or place facilities;
- persist accessibility rows.

Those responsibilities now move to the next capability:

- UG-AI-025 / S10-T08 — define greedy placement state over the completed T07 accessibility
  results; no pathfinding is added inside placement.
