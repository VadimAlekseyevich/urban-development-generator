# Fixed-network attachment for generated roads

> **Status: Implemented as M0 debt closure**

The original S06 components could generate an MST/growth network between candidate anchors, but
did not guarantee that generated components attach to the fixed/source network in EXPANSION mode.
Semantic noding only connects geometry that actually intersects, so relying on accidental crossings
violated the S06 sprint gate.

`FixedNetworkAttachmentConnector` makes this explicit.

## Contract

Inputs:

- generated `CandidateRoadAnchor` values;
- connected anchor pairs from accepted MST/growth connections;
- the already-built fixed-source `RoadGraph`;
- the same `WeightedSuitabilityResult` and `HardExclusionMask`;
- explicit attachment and least-cost policies.

The connector finds connected components of the generated anchor topology. Each component receives
one deterministic attachment to the nearest valid fixed graph node inside
`max_distance_m`.

## Spatial and routing semantics

Fixed nodes are indexed with the existing `SpatialSnapIndex`; no anchor×node all-pairs matrix is
built.

A fixed graph node is eligible only when it lies inside the suitability grid and its containing
raster cell is valid/non-excluded. Least-cost routing targets that cell's center. The final short
segment extends from the valid cell center to the exact fixed node coordinate, which lies in the
same raster cell. Final semantic noding can therefore create an exact graph connection.

If the candidate anchor is already in the fixed node's raster cell, no A* search is needed.

## Determinism and bounds

- generated components are ordered by stable anchor ids;
- nearest choice is ordered by distance, anchor id, fixed node id;
- fixed nodes use the indexed snapping primitive;
- component count is bounded by `max_attachments`;
- fixed node index size is bounded;
- each routed attachment keeps the existing least-cost visited-cell bound.

## Failure semantics

Each component records one of:

- CONNECTED;
- ALREADY_CONNECTED;
- NO_FIXED_NODE_IN_RANGE;
- NO_PATH;
- SEARCH_LIMIT_REACHED.

The result exposes `complete`; road-stage validation must not silently accept unattached generated
components in EXPANSION.

FROM_SCRATCH has no fixed-network attachment requirement and uses the same later noding/graph
pipeline without this step.
