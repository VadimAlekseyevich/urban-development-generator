# UG-AI-112 / S15-T03 — Territory B FROM_SCRATCH input

**Review candidate: reproducible preprocessing contract, not archived real research data.**

Territory B deliberately contrasts with Territory A:

| Property | Territory A | Territory B |
| --- | --- | --- |
| Study geometry | Ryazan reference, projected 5 × 5 km | Tula-region reference, projected 10 × 10 km |
| Generation mode | \`EXPANSION\` | \`FROM_SCRATCH\` |
| Declared area | 25 km² | 100 km² |
| Urban source import | OSM roads/buildings/landuse/water | **no fixed roads or buildings** |
| Environment inputs | polygonal landuse/water | polygonal landuse/water |
| Source provenance | SHA-pinned operator-acquired PBF | SHA-pinned **dated archive** PBF |

The proposed Tula-region centre (37.60 E, 54.25 N, WGS84) is a reproducible
coordinate, **not** an independently verified greenfield, official Tula
municipal boundary, cadastral unit, flood risk map or suitability verdict.
\`FROM_SCRATCH\` intentionally omits existing urban geometry from the fixed
model state even when the source contains it. The report records counts of
OSM roads/buildings observed in the AOI but excluded as fixed inputs.
Always disclose this counterfactual assumption in results.

## Source, licence and repeatability

Geofabrik's Central Federal District is available as a dated PBF archive,
for example the [2026-11-02 snapshot](https://download.geofabrik.de/russia/central-fed-district-261102.osm.pbf).
The OSM data are © OpenStreetMap contributors, ODbL 1.0. Archive source
acquisition date, original raw bytes, 64-character lowercase SHA-256, code
commit, dependency lock and GDAL/GEOS/PROJ versions with the study. The
manifest intentionally contains **no guessed digest**. A dated filename is
a more stable acquisition identifier than \`-latest\`, but the code still
requires byte-for-byte SHA-256 verification.

From the project root, after \`uv sync --frozen --dev\`:

\`\`\`sh
mkdir -p /private/udg-sources
# Obtain the exact dated PBF above yourself; do not download in CI.
sha256sum /private/udg-sources/central-fed-district-261102.osm.pbf
cp studies/territory_b/manifest.template.json /private/udg-sources/territory-b.json
# Replace source_sha256 with the verified 64-hex checksum.
uv run python -m scripts.package_territory_b \
  --manifest /private/udg-sources/territory-b.json \
  --source /private/udg-sources/central-fed-district-261102.osm.pbf \
  --output-dir /private/udg-sources/territory-b --verify-only

uv run python -m scripts.package_territory_b \
  --manifest /private/udg-sources/territory-b.json \
  --source /private/udg-sources/central-fed-district-261102.osm.pbf \
  --output-dir /private/udg-sources/territory-b
\`\`\`

The offline preprocessor verifies exact source bytes and uses the S03
OSM identity/tag/classification rules and S15 Territory A bounded GDAL
reader/geometry helpers. It filters \`lines\` and \`multipolygons\` by a
WGS84 envelope, intersects real features with the exact projected
EPSG:32637 100 km² square, and emits \`boundary.geojson\`,
\`landuse.geojson\`, \`water.geojson\` and \`package-report.json\`.
The output is sorted by canonical OSM identity and each file carries a
reproducible SHA-256. The report records source row counts, raw input
checksum, excluded built-feature counts, projected area and ingestable
constraint kinds.

Only polygonal OSM landuse and water are exported. Linear watercourses,
existing roads/buildings, terrain (DEM), parcels, demography, official
environmental restrictions and infrastructure are **not** fabricated.
If no OSM polygon constraint intersects the AOI, preparation **fails**;
it cannot quietly produce an empty but supposedly validated research
package. Empty individual polygon files are emitted only for archive
consistency and flagged \`ingest_enabled=false\`—do not enqueue those
through ingest.

## Safety and canonical consumption

- The upstream 800+ MB PBF is never fetched automatically. No database,
  Redis, object store or API request is made by the preparer.
- Source features are read with at most 512 records per Pyogrio call and
  the manifest enforces a finite upper bound on spatially filtered rows
  and derived feature counts. GDAL may still scan its large OSM input
  internally; this is **not** a constant-RSS guarantee for the driver.
- Input digest mismatch, missing layers, unacceptable CRS, duplicate
  identities, limits, missing polygon constraints or existing output dir
  fail closed. No silent truncation; a sibling temporary directory is
  published only after all files and checksums are written.
- Only nonempty polygon files are submitted to the ordinary
  \`POST /api/v1/uploads\` and \`POST /api/v1/projects/{project_id}/ingests\`
  with kind \`landuse\` or \`water\`, source_format \`geojson\`.
  Actual DatasetVersion \`ready\` status belongs to the ingest worker.
  There are no fake or implicitly accepted existing roads/buildings.
- The public project API **still does not set** the geometry of
  \`Project.boundary\`. The boundary output is a verifiable handoff to a
  future supported API; PR #201 addresses this separately. Do not bypass
  it with unreviewed SQL or claim an end-to-end live GIS run.
- The experiment-matrix adapter is PR #198. This branch is stacked on
  Territory A PR #200 because it shares its tested preparation helpers,
  not because Territory B must share project IDs or canonical run state.

## Evidence and acceptance

\`\`\`sh
uv run pytest tests/unit/test_territory_b_package.py
uv run ruff check .
uv run mypy backend core worker
uv run pytest
\`\`\`

The synthetic multi-layer GeoPackage unit tests exercise real
GDAL/Pyogrio, square clipping, OSM identities, geometry family,
byte-repeatability, no-fixed-urban output, SHA refusal, caps and
empty-constraint refusal. They are **not** real Tula-source acceptance.
Actual operator snapshot acquisition, independent environmental source
verification, proper project boundary submission, and full canonical
GIS execution must be checked separately before UG-AI-112 or R3–R9
can be called complete.
