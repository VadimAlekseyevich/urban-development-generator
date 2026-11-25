# S13-T05 — declarative frontend layer registry (UG-AI-085)

The canonical backend contract remains `layer-catalog-v1`
(`backend/app/application/layer_catalog.py`). The frontend now mirrors its **18**
stable IDs, definition versions, exact owner scopes, origin/geometry/delivery,
z-order and render hints in `frontend/src/layerRegistry.ts`. It does not
replace the backend ownership/readiness checks or assert that every definition
has persisted data for a selected run.

## Separation of responsibilities

- `layerRegistry.ts`: typed definitions, `layerInstancesForContext`, immutable
  owner-qualified `instanceKey`, exact dataset-version/run/artifact/project
  binding and safe URL factories. No React, SQL, MapLibre or network requests.
- `layerStyles.ts`: declarative `MAP_LAYER_STYLES` with named MapLibre
  paint/filter recipes (including mixed-geometry violation and existing/generated
  facility filters), source legend and stable existing layer IDs. Components
  do not define the root source MapLibre paint rules.
- `mapLayerAdapter.ts`: materializes the selected recipe as a GeoJSON source,
  an owner-qualified vector tile source or a georeferenced raster image. Its
  MVT mount has explicit disposal; source/layer identifiers incorporate owner
  and feature limit, so a run/version switch cannot accidentally reuse a
  previous MVT source. Render recipes and transport URL generation are separate.
- `App.tsx`: the existing five source layers are installed and toggled through
  the registry, and their project/version-scoped legacy viewport and boundary
  URLs are catalog-derived. No root-component hardcoded `addLayer` paint
  definitions or second source legend remain.
- `SuitabilityPanel.tsx`: artifact-owned metadata/preview URLs and georeferenced
  MapLibre `image`/raster rendering use the shared registry and adapter;
  its existing visibility/opacity controls remain in the panel.

S13-T06 now consumes this registry through `LayerTree.tsx` and the shared
`layerVisibility.ts` store. Generated/validation panels continue to own fetching
and persisted read-model inspection, but their map visibility is synchronized
with the complete 18-layer tree. Duplicate fixed zoning/road map layers were
removed in favor of canonical source entries.
Existing panel click/selection logic and the S12 pinned-run behavior are not
rewired by S13-T05.

## Four distinct source/delivery modes

| Mode | Registered owner | Rendering |
| --- | --- | --- |
| Project single-feature GeoJSON | `project_id` | Existing boundary feature; not silently treated as dataset-owned. |
| Bounded viewport GeoJSON | `project_id + dataset_version_id` or `project_id + run_id` | Specialized legacy `bbox&limit` paths remain active; generic `boundedVectorUrl` supports the 15 UUID-table-backed paths, exclusive UUID `after`, EPSG:4326. |
| Binary MVT | Same exact 15 table-backed source/run IDs | `mvtTileUrl` uses fixed `{z}/{x}/{y}`, z=0..16, 1..1000 feature limit and the owner's **only** required query param; MapLibre vector `source-layer` equals the canonical layer ID. Backend alone checks readiness and ETag eligibility. |
| Georeferenced raster image | `artifact_id` only | Suitability preview PNG and distinct metadata route supply WGS84 image corners. No made-up project owner or MVT route. |

`project.boundary`, `analysis.suitability` and
`validation.violations` correctly reject the generic UUID-keyset/MVT
factories. Violations retain canonical integer `ValidationReport` indices
and the dedicated bounded GeoJSON API. `run.existing_facilities` keeps its
`origin=existing` discriminator when extra bbox params are appended;
generated facility rendering is distinct although today's infrastructure
panel shares a GeoJSON source.

## Acceptance and non-goals

`frontend/tests/layerRegistry.test.mjs` covers definition order/count,
style/source ID consistency, exact owner-qualified selection and run isolation,
bbox/path/limit validation, MVT tile bounds and exclusion of specialist layers,
artifact routing and discriminator query preservation. CI runs the Node tests,
TypeScript typecheck and Vite production build as well as all Python/migration
and Compose checks.

This remains a frontend presentation/transport convergence contract, **not** a new
catalog HTTP endpoint, eager 18-layer data download, automatic MVT switch
for every current panel, unbounded export, tile authorization bypass or completed
M5 workspace. The complete S13-T06 tree is documented in `docs/LAYER_TREE.md`;
S13-T07 begins asynchronous GeoJSON export.
