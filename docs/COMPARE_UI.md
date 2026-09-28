# S12 compare UI / shared run-on-map contract (UG-AI-080)

The sidebar Compare panel calls only existing S11 `GET /metric-runs?limit=100`
and S12 `POST /compare` APIs. Users choose 2–10 unique successful runs
with a persisted evaluation score. Selection order is explicit: first ID is
the baseline; "Сделать базой" moves an existing selected ID to the front
without silently sorting. Changing selection or baseline invalidates the old
comparison and cancels its pending HTTP request.

The comparison table displays the **server-returned** canonical raw scalar
score-input metrics with native units and direction metadata, absolute delta
against baseline and server ranks. TARGET / DESCRIPTIVE / missing values
keep null ranks/deltas as returned. All runs show persisted composite scores
and counts of canonical hard/soft/spatial validation failures. The UI only
shows score deltas/ranks when the backend explicitly returns
`scores_comparable=true` (matching scoring provenance). An unavailable
validation report, unfinished run, mismatched working SRID or malformed
persisted data produces a visible API error; the frontend does not estimate
missing values or rerun any GIS/metric/score algorithms.

After a successful compare, the baseline is selected on the map. A run
switch button chooses one of the returned compared run IDs and pins that
single ID across generated zones, roads, blocks/parcels, buildings,
demographic choropleth, infrastructure and validation-violations layers.
Every panel independently checks whether its persisted run read model
contains the pinned ID. If it does not, its generated source is cleared and
its selector indicates missing read model; **never** replace it with another
run's geometries. Source/fixed layers still follow the selected
DatasetVersion. The manually selected suitability artifact is not a
run-scoped source and is not rebound by this S12 change.

Legacy independent run selectors remain available when the compare pin is
cleared by changing comparison selection or refreshing the panel; while
pinned they are disabled to prevent a mixed-run map. A project switch
clears the comparison and map pin. Per-layer AbortControllers cancel stale
viewport/detail requests when switching run. No new backend state, API,
schema migration, LayerCatalog (S13), MVT or exports are introduced.

## Acceptance and limitations

- `frontend/tests/compareSelection.test.mjs` exercises comparison bounds,
  explicit order/baseline, idempotent toggling and no foreign-run fallback;
  frontend CI runs it with Node's TypeScript stripping.
- `tests/integration/test_run_control_api.py` proves create -> stored
  score/validation publication -> project-scoped compare -> read-only
  successful state. Existing S12 suites cover worker stage commits,
  cancel/retry/outbox, ScenarioBatch/exact rerun/provenance and compare.
- User-selected comparisons are limited to the 100 most recent metric-run
  results; compare is limited to 10. The UI is one-run-at-a-time map switching,
  not side-by-side simultaneous GIS rendering. Browser E2E for complete
  create/upload/run/compare/export remains S13-T14; real production
  stage-configuration/deployment coverage is not claimed by the synthetic
  integration fixture.
