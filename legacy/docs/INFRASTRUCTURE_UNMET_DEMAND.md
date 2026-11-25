# Unmet infrastructure demand

S10-T03 transforms the S09 demographic demand profile into block-level infrastructure
demand using the S10-T01 InfrastructureType demand models.

## Demand item

For every block and infrastructure type the calculator records:

- infrastructure type and category;
- source demographic signal and optional cohort;
- source signal value and demand rate;
- gross demand;
- explicitly served demand;
- remaining unmet demand.

Gross demand is always `source signal × demand rate`.

## Existing infrastructure

S10-T02 existing facilities are included in per-type summaries as available existing
capacity. Their capacity is deliberately **not** assigned to blocks in T03.

At this stage there is no network snap, distance matrix, or max-distance coverage evidence,
so subtracting existing capacity globally would create false coverage. S10-T06/T07 will
produce spatial/network evidence; those stages or T08 may supply explicit
`InfrastructureServedDemand` assignments for exact block/type pairs and reuse this
calculator to update remaining demand.

## Determinism and bounds

Infrastructure types are canonicalized by code, output demand items are sorted by
`(block_id, type_code)`, and duplicate type codes or served assignments are rejected.
The calculator enforces a bounded block×type work-item budget.

## Scope boundary

T03 does not generate candidate sites, snap to the graph, calculate accessibility, or place
facilities. T04 begins candidate generation.
