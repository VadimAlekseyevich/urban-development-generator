# Infrastructure synthetic-town acceptance fixture

> **Status: Complete through UG-AI-043 / S10-T13**

S10-T13 adds one deliberately small synthetic town that exercises the completed S10 contracts
together instead of restating them in another orchestration layer. The fixture lives in
`tests/integration/test_infrastructure_synthetic_town_e2e.py`.

## Fixture shape

The town uses EPSG:3857 and a five-node line network with 100 metre edges. It contains:

- two public-zone demographic demand blocks;
- one fixed school at the first network node;
- two polygonal generated school candidates;
- one canonical `school.general` InfrastructureType;
- a 250 metre service threshold.

Each block exposes the same typed S09 demographic signal schema. The school demand model consumes
the `child` age-group signal, so T03 demand is derived from the demographic profile rather than
hard-coded directly as placement state.

## End-to-end contract

The fixture invokes the production contracts in this order:

1. `UnmetDemandCalculator` creates gross block/type demand.
2. T06 `snap_infrastructure_network_batch()` snaps demand, the fixed facility and candidate
   anchors through a real `NetworkXBackend`.
3. T07 existing-facility accessibility identifies reachable fixed service.
4. A test-only deterministic allocator spends the single fixed facility capacity in canonical
   demand order and feeds explicit `InfrastructureServedDemand` back into T03. This is fixture
   plumbing only; it does not introduce a new production allocation API.
5. T07 candidate accessibility builds the complete bounded candidate×demand outcome set.
6. T08 benefit calculation/selection and T09 feasibility validation run iteratively until no
   additional candidate is selected.
7. T10 `SqlAlchemyGeneratedInfrastructureWriter` persists the accepted generated facilities in
   Postgres/PostGIS with the T06 network provenance.
8. T11 `InfrastructureMetricsBuilder` computes the canonical six infrastructure raw metrics from
   the authoritative T03/T07/T08 results without rerouting.

The fixture first asserts that every stage produced the expected structural subjects, that two
generated facilities were accepted/persisted, and that the canonical metric set was emitted.

UG-AI-043 adds the acceptance invariants on the same fixture:

- gross school demand is 20 capacity units;
- the fixed school contributes 5 served units before generated placement;
- two generated facilities contribute another 10 units, leaving final unmet demand 5;
- canonical population/child coverage is 0.75 and is accepted inside the documented 0.70–0.80
  regression range;
- capacity utilization is 1.0;
- weighted p50/p90 network distance are both 100 m for this town;
- two complete reruns have equal semantic T03/T06/T07/T08/T11 outputs and equal persisted
  generated-facility provenance apart from run-scoped identifiers;
- the same fixed ExistingInfrastructureResult instance retains its complete facility/geometry
  signature across both runs.

## Why the fixed-service allocator is test-only

The current S10 contract deliberately makes T03 existing service explicit through
`InfrastructureServedDemand`; T07 reports reachability and does not own capacity allocation. The
fixture has one fixed facility, so a minimal canonical-order allocation is sufficient to connect
those two accepted contracts without adding a competing production rule.

If production later gains a canonical existing-capacity assignment capability, this fixture should
use that capability instead of growing its helper into application logic.

## Ordered follow-up

S10-T13 is complete through UG-AI-043. Performance/all-pairs evidence remains UG-AI-044/045:
the next task is UG-AI-044 / S10-T14, which adds the bounded candidate×demand performance fixture
and counters proving that greedy iterations do not repeat full all-pairs routing.
