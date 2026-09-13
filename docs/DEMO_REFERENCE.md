# Demo/reference scenario

This note is an orientation aid for development and manual checks. It is not an architectural constraint and does not make the product city-specific.

## Reference city

For examples, screenshots, manual acceptance checks, sample datasets, and future demo scenarios, use **Ryazan (Рязань)** as the default reference city unless a work item explicitly needs a synthetic fixture or another location.

City-specific assumptions must not be embedded into `core`, persistence schemas, algorithms, CRS contracts, or API contracts. Ryazan is only the common real-world example used to keep manual testing and documentation consistent.

## Where to look

- `README.md` — how to start the complete development stack.
- `docs/IMPLEMENTATION_VERSION_ROADMAP.md` — what is being implemented next and when new UI vertical slices appear.
- `docs/INGEST_WORKER.md` — how uploaded vector/raster source data is normalized and promoted to a ready DatasetVersion.
- `docs/SOURCE_LAYER_GEOJSON_API.md` — API used to read normalized source layers by viewport.
- `docs/SOURCE_LAYERS_UI.md` — current interactive source-map capabilities: boundary, roads, buildings, water, landuse, layer toggles, fit and feature inspector.
- `docs/OSM_PBF_READER.md`, `docs/OSM_MAPPING_RULES.md`, `docs/OSM_CANONICAL_WRITER.md` — OSM import path when preparing Ryazan source data from PBF.

## Current hands-on status

The source-layers map is already implemented, but it expects an existing project and a ready `dataset_version_id`. The remaining usability gap for a non-developer demo is a single guided flow that creates/selects the project, imports the source dataset, waits for ingest, and opens the resulting map without manually assembling IDs.

When such a guided demo flow or checked-in Ryazan fixture is added, document the exact command/file/source here so this page remains the first place to find it.
