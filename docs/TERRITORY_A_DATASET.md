# S15-T02 / UG-AI-111 — Territory A: reproducible Ryazan OSM expansion input

**Status: review candidate, not a shipped real-territory dataset.**

This package is an **offline recipe plus a versioned input manifest**, not a
pretend set of real OSM features checked into Git. Territory A uses the Ryazan
reference city ([reference policy](DEMO_REFERENCE.md)), `EXPANSION` mode
and the existing EPSG:32637 metre working CRS. The test corpus is synthetic;
no Geofabrik PBF has been downloaded, validated or stored by repository CI.
Do not report real feature counts or validated output checksums until an
operator actually prepares an exact snapshot from external data.

## Source provenance and limitations

- Upstream OSM extract: [Geofabrik Central Federal District PBF](https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf)
  (OSM data © OpenStreetMap contributors, ODbL 1.0). Preserve attribution
  and review upstream usage/attribution terms for redistribution.
- This URL's `latest` name is **mutable**. The package only proceeds when
  `source_sha256` matches the locally downloaded byte-for-byte PBF. Copy
  that PBF, its checksum, source acquisition timestamp and `uv.lock` /
  application commit into the private research archive. The manifest template
  contains a deliberate untrusted placeholder—not an invented hash.
- A 5,000 × 5,000 m metric square centred at 39.74 E / 54.62 N (EPSG:4326)
  is transformed into EPSG:32637 before clipping. The **projected** area is
  exactly 25,000,000 m² (25 km²); it is neither an administrative Ryazan
  boundary nor a claim about real-world developable area. `boundary.geojson`
  is provided in WGS84 for the ordinary project boundary API.
- The script reads GDAL reconstructed OSM `lines` and `multipolygons`
  spatially filtered to the WGS84 envelope, clips in the metric CRS, then
  emits bounded GeoJSON `roads`, `buildings`, `landuse`, `water`
  files with stable sorted OSM IDs/tags. **Only polygonal water** is included in
  `water.geojson`; linear streams/rivers are not mixed into that polygon
  FeatureCollection because canonical ingest takes a single geometry family.
  Linear waterways need a separate, future source ingestion workflow. The **classification/tag/OSM
  identity rules are reused from the S03 reader**, not invented research
  class names. The normal worker still owns GeoJSON inspection,
  normalization, reclassification, DatasetVersion promotion and persistence.
- Sources not included in this OSM extract (DEM, census, complete
  facilities/demography, official restrictions and authoritative parcel
  cadastre) are **not fabricated**. Water/landuse may be empty; roads and
  buildings must be nonempty. Real-town pipeline completeness requires those
  independent sources and the unresolved production generation runtime.

## Prepare an immutable input package

Run from the repository root after `uv sync --frozen --dev`:

```sh
mkdir -p /private/udg-sources
# Download the actual PBF yourself from the Geofabrik source above.
# Save it, for example, as:
# /private/udg-sources/central-fed-district-snapshot.osm.pbf
sha256sum /private/udg-sources/central-fed-district-snapshot.osm.pbf

cp studies/territory_a/manifest.template.json /private/udg-sources/territory-a.json
# Replace source_sha256 with the actual lowercase 64-hex checksum, then:
uv run python -m scripts.package_territory_a \
  --manifest /private/udg-sources/territory-a.json \
  --source /private/udg-sources/central-fed-district-snapshot.osm.pbf \
  --output-dir /private/udg-sources/territory-a --verify-only

uv run python -m scripts.package_territory_a \
  --manifest /private/udg-sources/territory-a.json \
  --source /private/udg-sources/central-fed-district-snapshot.osm.pbf \
  --output-dir /private/udg-sources/territory-a
```

**Outputs:** `boundary.geojson` (WGS84 projected-square footprint),
`roads.geojson`, `buildings.geojson`, `landuse.geojson`,
`water.geojson`, and `package-report.json`. The report captures the
actual input digest, spatial bounds, source row counts, per-kind feature
counts, file sizes and SHA-256 checksums. Re-run with the **same raw PBF,
manifest, code and pinned Python/GDAL/GEOS/PROJ environment** to compare
byte-level digests. No network or database access occurs during packaging.

Default bounds: 512 OGR features per read; at most 30,000 spatially filtered
raw features per OSM source layer and 20,000 output features per canonical
kind (operator-configurable validated 1–100,000). The Pyogrio spatial filter
does not guarantee constant-time reads of a very large PBF; GDAL may scan
upstream records. Exceeding a cap aborts, rather than silently truncating
research data. Duplicate OSM IDs abort. The script rejects any existing
output directory, builds files in a sibling temporary directory, then
publishes the complete directory; interrupted temp dirs are cleaned by
the language runtime on ordinary exceptions. SIGKILL can strand a hidden
temporary directory and needs operator cleanup **after inspection**.

## Canonical data flow and evidence

Create a project through the ordinary project API with EPSG:32637;
the API currently persists `boundary_metadata` but **does not accept or
persist the geometry of `Project.boundary`**. The generated
`boundary.geojson` is a reproducible geometry handoff, not proof that
project-boundary ingestion is implemented. A supported boundary-setting
adapter/API is required before ordinary scenario creation can validate a
non-null project boundary. Do not bypass this release blocker with undocumented
manual SQL or claim that the package can immediately run a full generation.

For the source layers, submit each **nonempty** `*.geojson` through the
existing `POST /api/v1/uploads` and
`POST /api/v1/projects/{project_id}/ingests` with matching `dataset_kind`
and `source_format=geojson`. Poll until the **worker**, not this script,
marks each DatasetVersion `ready`. Record project ID, successful input
Artifact checksums, ready DatasetVersion IDs, the raw PBF checksum and the
application commit. Do not bypass ingestion with direct SQL or mark versions
READY manually.

See [ingest submission](INGEST_SUBMISSION.md), [OSM reader](OSM_PBF_READER.md),
[normalization](VECTOR_NORMALIZATION.md) and
[research experiment matrix](adr/0007-deterministic-scenario-matrix.md)
(the application-level UG-AI-110 PR is still pending review).

```sh
uv run pytest tests/unit/test_territory_a_package.py
uv run ruff check scripts/package_territory_a.py tests/unit/test_territory_a_package.py
uv run mypy backend core worker
```

Regression uses a **synthetic local multi-layer GeoPackage**, not a
downloaded Ryazan PBF. It executes the real Pyogrio/GDAL reader,
reprojection, exact square clip and canonical OSM tag/identity
helpers. It proves byte-for-byte repeatability, checksum mismatch refusal,
geometry/feature-budget checks and absence of DB access. This is not a
real-data performance test, full ingest E2E, or completed thesis dataset.
