# OSM PBF reader foundation (S03-T10)

`backend.app.services.osm_pbf_reader` is the raw OpenStreetMap ingestion boundary used before tag mapping and canonical persistence.

## Scope

`OsmPbfReader` reads a local `.pbf` datasource through the existing Pyogrio/GDAL OSM driver and emits bounded `OsmFeatureBatch` values for five source families:

- `roads`
- `buildings`
- `poi`
- `landuse`
- `water`

This task does **not** translate OSM values such as `highway=residential` into internal enums. Versioned tag-to-internal mapping belongs to S03-T11. Canonical source-table persistence belongs to S03-T12.

One OSM feature may be present in more than one family. For example, a building carrying `amenity=school` is both a building and a POI at this boundary. T10 deliberately does not hide this behind precedence rules.

## Geometry and identity contract

GDAL reconstructs geometries from OSM nodes, ways and relations. Reader output is required to be `EPSG:4326`; no reprojection or metric operation happens in this service.

Every feature has a stable source identifier:

```text
osm:node:<id>
osm:way:<id>
osm:relation:<id>
```

For GDAL's `multipolygons` layer, closed ways use `osm_way_id` while relation-built multipolygons use `osm_id`. The reader requires exactly one of those identities so a retry cannot silently change source identity.

## Tags

The reader preserves a configured superset of raw tag keys needed for family classification and the next mapping stage. The default set includes road attributes (`highway`, `lanes`, `maxspeed`, `surface`, ...), building attributes, POI keys, land-use keys and water keys.

GDAL fields promoted by `osmconf.ini` and `other_tags` / `all_tags` are merged into one immutable `Mapping[str, str]`. `TAGS_FORMAT=JSON` is requested from the GDAL OSM driver; Pyogrio/GDAL may expose that value as either a mapping or JSON text, and both forms are accepted.

Unknown tags outside the configured relevant-key set are intentionally discarded at this boundary. Adding a key for future mapping is explicit through `relevant_tag_keys`; classification keys cannot be removed accidentally.

## Bounded reading

Defaults:

- batch size: `4096` source features;
- maximum features per GDAL OSM layer: `2,000,000`;
- maximum preserved relevant tags per feature: `128`.

All limits are explicit through `OsmPbfReadLimits` and can be raised for a known larger territory.

Before materializing feature chunks, the backend obtains the exact layer feature count and rejects a layer over the configured feature limit. Each `read_chunk` call is then capped by `batch_size`. The service never intentionally loads a complete large OSM layer into one GeoDataFrame.

Pyogrio documents an OSM-specific limitation: GDAL may scan a PBF to calculate feature count and may need to traverse prior records for `skip_features`. T10 therefore guarantees bounded Python-side memory, not constant-time random access. The backend is behind `OsmPbfBackend` so a future sequential/native reader can replace Pyogrio without changing the application-facing batch contract.

## GDAL OSM layers

The reader consumes the reconstructed spatial layers relevant to S03 source data:

- `points`
- `lines`
- `multilinestrings`
- `multipolygons`

`other_relations` is intentionally not emitted by T10 because it has no direct canonical target in the current source-layer schema.

## Classification boundary

Family detection only answers whether a raw OSM feature is a candidate for a source family:

- roads: `highway` is present;
- buildings: `building` or `building:part` is present;
- POI: presence of an explicitly listed POI key such as `amenity`, `shop`, `tourism`, `office`, ...;
- landuse: `landuse` is present;
- water: `waterway`, `water`, `wetland`, common raw water values of `natural`, or `landuse=reservoir|basin`.

These predicates do not assign internal road/building/facility/water classes. That separation is the handoff to S03-T11.

## Tests

`tests/unit/test_osm_pbf_reader.py` covers bounded chunk calls, multi-family projection, tag merging/filtering, CRS/feature/tag limits and a real Pyogrio/GDAL read of a minimal checked-in OSM PBF fixture built with standard protobuf wire encoding.
