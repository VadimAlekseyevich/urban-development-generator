# Territory A — Ryazan expansion-mode dataset (S15-T02)

**Status:** deterministic preprocessor delivered; **real-data run evidence pending**. This
is an experiment input package, not a generated scenario or a claim of successful
production worker execution. No proprietary input, Geofabrik PBF, database dump, or
unverified source hash is committed to Git.

## Source and spatial extent

`data/experiments/territory_a_ryazan.json` is the versioned input specification:
Geofabrik **Central Federal District OpenStreetMap PBF**, EPSG:4326 bounds
`[39.64, 54.60, 39.76, 54.69]` (west, south, east, north), target metric
working CRS **EPSG:32637**, mode `expansion`. The small rectangular research
window is **not** an official Ryazan administrative boundary. It is not a
real-growth holdout or a validation ground truth. OSM data is community-maintained,
can be incomplete and is licensed under ODbL; retain attribution and comply with
Geofabrik/OSM license terms when redistributing outputs.

The URL ends in `latest` and **is mutable**. Download and externally archive a
specific snapshot of the extract, record its retrieval date and SHA-256 in your
experiment ledger. To reproduce it later, you must use the **same bytes**; the URL
alone is insufficient evidence of reproducibility.

## Build once from immutable bytes

```bash
# Store large PBF outside Git; downloaded URL is in the input specification.
mkdir -p storage/experiments
# Example: curl -fL --retry 3 -o storage/experiments/central-fed.osm.pbf \
#   https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf
sha256sum storage/experiments/central-fed.osm.pbf

uv run python -m scripts.territory_a_package \
  --source storage/experiments/central-fed.osm.pbf \
  --source-sha256 '<the 64-character lowercase digest you verified>' \
  --output storage/experiments/territory-a-locked
```

The command rejects a source checksum mismatch and existing destination, without
silently switching to a newer `latest` extract. It uses Pyogrio/GDAL OSM reads
with a fixed spatial filter and per-source-layer cap of 100,000 records (+1 to
detect overflow). Raw features are filtered by OSM tags; source identity is
`osm:<node|way|relation>:<id>` rather than arbitrary input row position.
Features are clipped to the fixed WGS84 bounding box, snapped to a 1e-7 degree
coordinate grid, mapped through the existing versioned `OsmTagMapper`, sorted
by stable identity and emitted as canonical-property GeoJSON with byte-stable
JSON key ordering. The package rejects duplicate IDs, CRS mismatches and a
missing road/building layer. One output GeoJSON layer may be at most 32 MiB.

The generated `package-manifest.json` records **actual** PBF SHA-256,
preprocessor + mapping versions, bounds/CRS and each exported layer's
feature count and SHA-256. It includes only nonempty roads/buildings/landuse/
water/facilities. Keep the manifest plus original PBF archive and output files;
check the hashes again before a later experiment. Byte identity is guaranteed
for a pinned source, same config, Python/geospatial dependency versions and
GDAL/GEOS behavior; it is not a cross-version equivalence claim.

## Ingest into the application

Create one project (working SRID 32637); for each emitted GeoJSON file, use the
regular upload flow followed by `POST /api/v1/projects/{project_id}/ingests`
with `artifact_id` from upload, `dataset_kind` and `source_format` from the
package-manifest layer record. Wait until each DatasetVersion is really `ready`;
record each returned ID and source checksum in the experiment ledger. Do not
insert source rows by SQL, mark versions `ready` manually, or use
`scripts/import_ryazan_from_geofabrik.py` as a substitute for the canonical
uploaded-artifact worker ingestion. See `docs/INGEST_SUBMISSION.md`.

**Known limits:** clipping is rectangular, does not recover features omitted
by upstream OSM extraction, and does not invent constraints, terrain, verified
administrative boundaries, cadastral parcels or historical ground truth.
No large real snapshot, real ingest receipts or successful run hashes are
published in this PR. S15-T04+ must record actual experiment evidence and
runtime limitations separately. Territory B is S15-T03, not part of this task.
