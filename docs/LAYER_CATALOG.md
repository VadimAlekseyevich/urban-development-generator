# Canonical LayerCatalog — S13-T01 / UG-AI-081

> Contract owner: `backend/app/application/layer_catalog.py`. Schema ID:
> `layer-catalog-v1`. This task defines typed, immutable metadata; it adds
> **no** new HTTP route, layer table, GIS computation, MVT renderer or UI tree.

## Identity and ownership

The catalog is the single S13 logical **map presentation** registry. It is
not a second `TerritorySnapshot`, a replacement for canonical
`SourceLayerName`, a generated-feature model, or a source of available data.
A `LayerDefinition` declares a stable lowercase `layer_id`,
`definition_version`, `LayerOwnerScope`, semantic `LayerSourceKind`,
`LayerGeometryKind`, delivery kind/route and render hints. Changing the
meaning, URL/delivery, geometry or presentation contract of an existing ID
requires bumping the definition version; schema-breaking catalog changes
require a new `LAYER_CATALOG_SCHEMA_VERSION`.

`LayerOwnerRef` binds exactly one of these combinations:

| Owner scope | Required IDs | Forbidden IDs |
|---|---|---|
| `project` | `project_id` | dataset version, run, artifact |
| `dataset_version` | `project_id`, `dataset_version_id` | run, artifact |
| `run` | `project_id`, `run_id` | dataset version, artifact |
| `artifact` | `artifact_id` | project, dataset version, run |

All IDs are real UUIDs. The artifact owner intentionally has no project
claim: the existing suitability artifact API does not currently expose a
project-owner link. An artifact and a project can coexist in a map context
without a fabricated relationship. The run owner has project scope but does
**not** smuggle in a dataset version; that provenance lives on
`GenerationRun.dataset_versions`. A run's immutable result may depend on
multiple dataset versions. Services serving each path must still verify
the persisted project/run/version ownership and materialization state.

Semantic `source_kind` is orthogonal to persistence ownership. In
particular, `run.existing_facilities` is a run-owned **projection of
source-origin existing facilities**, distinct from project/dataset-owned
`source.facilities` and run-owned `generated.facilities`. Validation
violations are run-owned and derived from the canonical `ValidationReport`;
they are not a new physical layer or source geometry.

`LayerCatalogEntry.instance_key` includes stable definition ID, scope and
the complete owner identity, so switches between runs or dataset versions
cannot reuse the same cache identity. `LayerCatalog` rejects duplicate
instances, unregistered/redefined entries and noncanonical display order.
`layer_catalog_for_context(...)` returns only definitions whose exact
owner UUIDs were explicitly provided. No fallback, implicit previous run,
database query, data availability claim or cross-project inference is made.

## Delivery/render metadata and implemented endpoints

Presentation is EPSG:4326. The existing source/generated/validation viewport
GeoJSON endpoints require a bounded bbox and declare `max_features`
(5,000, except canonical validation GeoJSON: 1,000). Boundary delivery is
a project-owned, single-feature GeoJSON route without bbox. The artifact
suitability preview has its own PNG route **and** separate metadata route
that supplies WGS84 map corners; the PNG itself is not treated as a
georeferenced vector/tile. In all cases the path is a fixed local API
template with *exactly* the owner's UUID placeholders; no arbitrary URL,
format expression, query injection or raw ArtifactStore path is accepted.

The stable catalog v1 consists of 18 entries in canonical z order:

| ID(s) | Owner | Semantic kind | Delivery |
|---|---|---|---|
| `analysis.suitability` | artifact | analysis | suitability preview/metadata |
| `project.boundary` | project | source | project GeoJSON |
| `source.landuse`, `source.water`, `source.constraints`, `source.facilities`, `source.buildings`, `source.roads` | dataset version | source | source-layer bbox GeoJSON |
| `generated.zones`, `generated.demography`, `generated.blocks`, `generated.parcels`, `generated.buildings`, `generated.roads` | run | generated | run bbox GeoJSON |
| `run.existing_facilities` | run | source | infrastructure bbox GeoJSON `origin=existing` |
| `generated.facilities` | run | generated | infrastructure bbox GeoJSON `origin=generated` |
| `generated.infrastructure_demand` | run | generated | infrastructure demand bbox GeoJSON |
| `validation.violations` | run | validation | canonical violation bbox GeoJSON |

Each immutable `LayerRenderMetadata` contains a `style_key`, `legend_key`,
bounded integer `z_index`, boolean `default_visible` and finite 0..1
`default_opacity`. These are renderer-neutral tokens/hints, **not** a
parallel MapLibre paint/expression implementation. S13-T05 maps the
canonical tokens to the declarative frontend style registry. The original 18-entry catalog remains the stable identity/owner
contract. S13-T02/T03 provide bounded GeoJSON/UUID-keyset and MVT read
adapters for its 15 table-backed vector entries; they do not assert
availability or rewrite canonical T01 definition versions. Immutable
immutable state-qualified tile ETags and 304 caching are implemented by
S13-T04's read adapter, without changing the catalog's definition IDs.

## Gates and next steps

`tests/unit/test_layer_catalog.py` checks all 18 definitions against
existing GET decorator paths extracted from endpoint sources (including fixed router prefixes, independent of mutable ASGI state), typed provenance, route placeholder exactness,
invalid combinations, deterministic binding, immutability, repeat selection,
no cross-run fallback and bounded transport metadata. Required CI still runs
the existing Python suite, benchmarks, migration smoke, frontend build/test
and Docker Compose smoke. No new migration is required.

UG-AI-082 consumes owner-qualified entries to generalize bounded bbox/keyset
reads, projection and simplification policy without changing the canonical
raw source/generated/validation models. S13-T03/T04 can add MVT/cache
delivery; S13-T05/T06 own rendering/registry/tree migration from existing
separate React panels. This T01 contract does not assert that all catalog
entries have data for a selected run/version.
