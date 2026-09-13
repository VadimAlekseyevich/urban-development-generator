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
- `docs/SOURCE_LAYERS_UI.md` — interactive source-map capabilities: boundary, roads, buildings, water, landuse, layer toggles, fit and feature inspector.
- `docs/SUITABILITY_LAYER_API_UI.md` — suitability artifact metadata/preview API, MapLibre raster UI and the exact synthetic Ryazan demo command.
- `docs/OSM_PBF_READER.md`, `docs/OSM_MAPPING_RULES.md`, `docs/OSM_CANONICAL_WRITER.md` — OSM import path when preparing Ryazan source data from PBF.

## Current hands-on status

The source-layers map expects an existing project and a ready `dataset_version_id`. The suitability UI can now also open an existing canonical suitability artifact by `suitability_artifact_id` and display its heatmap, hard exclusions, statistics and factor metadata.

For an immediately runnable suitability visualization, start the stack and seed the **synthetic demo only**:

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec api uv run python scripts/seed_ryazan_suitability_demo.py
```

The command prints a URL of the form:

```text
http://localhost:5173/?suitability_artifact_id=<printed-uuid>
```

This raster is only a UI fixture positioned over the Ryazan reference area; it is not derived from real Ryazan terrain, roads or landuse and is not a planning result.

The remaining usability gap for real user data is a single guided flow that creates/selects the project, imports source datasets, waits for ingest, runs suitability calculation, persists the resulting artifact and opens the combined source+suitability map without manually assembling IDs.
