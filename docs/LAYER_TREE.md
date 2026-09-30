# S13-T06 — full canonical layer tree (UG-AI-086)

The browser map now exposes one complete logical layer tree derived from the
S13 `layer-catalog-v1` frontend registry. The tree is a presentation/control
surface over existing owner-scoped read models; it does not create layer
availability, bypass backend authorization/readiness checks, or change any
persisted geometry.

## Canonical tree

`frontend/src/layerTree.ts` groups all 18 registered definitions by semantic
source kind while preserving registry order inside each group:

- analysis: suitability raster;
- source/fixed: project boundary, six DatasetVersion source layers and
  run-existing facilities;
- generated: zones, demography, blocks, parcels, buildings, roads, generated
  facilities and infrastructure demand;
- validation: violations.

`LayerTree.tsx` renders those definitions directly. A node shows the exact
owner caption when the current context can bind it to a project,
DatasetVersion, shared map run or suitability artifact. An unbound logical
layer remains a visibility preference rather than inventing an owner. This is
important for the pre-existing S12 legacy mode where panels may inspect an
independent local run while no compare map pin is active. Once compare pins a
run, the exact run owner applies to every generated/validation node and panel;
a missing read model stays empty rather than falling back to another run.

## One visibility state

`frontend/src/layerVisibility.ts` is the single in-browser visibility store.
Its initial state comes from the 18 registry `defaultVisible` values.
`useSyncExternalStore` gives React panels a consistent snapshot and
functional/boolean updates.

The full tree and all map-producing panels share that state:

- suitability -> `analysis.suitability`;
- zoning -> `generated.zones`;
- roads -> `generated.roads`;
- blocks/parcels -> their two canonical IDs;
- buildings -> `generated.buildings`;
- demography -> `generated.demography`;
- infrastructure -> run-existing/generated facilities + demand;
- validation -> `validation.violations`;
- root source viewport -> project boundary + all DatasetVersion source IDs.

Existing panel checkboxes remain as local convenience controls, but they read
and write the same store as the tree, so there is no second visibility truth.

## Source convergence

The root source viewport now loads all six dataset-backed catalog layers:
landuse, water, constraints, facilities, buildings and roads. Constraints and
facilities use the same bounded specialized source API and catalog-derived
owner URLs as the prior four layers.

Two pre-catalog duplicate render paths were removed:

- zoning no longer creates a separate `zoning-fixed-*` source/layer or
  refetches source landuse;
- roads no longer creates `roads-ui-existing-*` or refetches source roads.

The immutable source representations are rendered once through canonical
`source.landuse` and `source.roads` tree entries. Zoning and roads panels
now fetch/render only their generated run deltas. This also eliminates mixed
styling/visibility behavior between the root source legend and those panels.

## Scope boundary

S13-T06 does **not** switch every existing specialized panel to MVT, remove
panel-specific inspectors/metrics/opacity controls, add backend endpoints,
perform export, or claim M5 complete. The current generated/validation
panels still own their bounded data fetching and domain-specific inspection.
The tree centralizes logical layer membership, owner context and visibility;
S13-T07 starts asynchronous export work.

## Verification

`frontend/tests/layerTree.test.mjs` checks:

- all 18 catalog definitions appear in the four semantic groups;
- exact project/version/run/artifact owner binding and no hidden fallback;
- shared visibility defaults, subscriptions and functional updates;
- all six dataset source layers participate in the source viewport;
- root source special-case legend is gone;
- duplicate fixed zoning/road layers are gone;
- every generated/validation/raster panel binds visibility to its canonical
  logical layer ID.

Required repository CI additionally runs frontend typecheck/build, the full
Python test/benchmark/migration suite and Docker Compose smoke on the final PR
HEAD.
