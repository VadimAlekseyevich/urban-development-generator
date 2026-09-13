# OSM mapping rules

S03-T11 separates OSM tag interpretation from PBF reading and persistence. `OsmPbfReader`
(S03-T10) only reconstructs WGS84 features and preserves raw tags. `OsmTagMapper` applies the
versioned `DEFAULT_OSM_MAPPING_RULESET`; S03-T12 is responsible for reprojection and persistence
of the mapped entities into canonical source-layer tables.

## Version contract

The current ruleset version is `osm-v1`. Every `MappedOsmFeature` and `MappedOsmBatch` carries
that version, and every feature also records the exact `rule_id` that matched. Changing mapping
semantics requires a new ruleset version rather than silently changing the meaning of an existing
version.

Rules are ordered and declarative. A rule specifies a tag key, an exact set of accepted values (or
an explicit wildcard for any non-empty value), and one internal enum value. Validation rejects
duplicate matchers, duplicate rule IDs, invalid enum values, incomplete category coverage and
rules placed after a wildcard for the same key.

## Internal classes

The mapper produces typed internal classes for five T10 categories:

- roads: motorway, trunk, primary, secondary, tertiary, local, service, track, path, other;
- buildings: residential, commercial, industrial, retail, education, healthcare, civic, garage,
  other;
- POI/facilities: education, healthcare, emergency, transit, retail, food, recreation, culture,
  government, other;
- land use: residential, commercial, retail, industrial, recreation, forest, agriculture, grass,
  cemetery, military, water, other;
- water: river, stream, canal, drain, lake, reservoir, basin, wetland, coastal, other.

Unknown values deliberately map to `other`. The original OSM tags remain in `attributes_json`, so
a future ruleset can reinterpret them without losing provenance.

## Canonical-ready attributes

Mapping also performs deterministic, non-throwing parsing of simple canonical attributes:
`lanes`, `maxspeed`, `oneway`, `building:levels`, `height` and facility `capacity`. Unsupported or
ambiguous textual values become `None` and are preserved verbatim in the raw tag map. `maxspeed`
supports numeric km/h and mph values; height supports metres and feet.

Input batches must remain in `EPSG:4326`. Reprojection into the project's working CRS is not a
mapping concern and remains part of the S03-T12 writer path.

## Multi-category features

T10 intentionally allows one OSM feature to appear in multiple broad categories. T11 preserves
that behavior: each category is mapped independently. For example, `landuse=reservoir` maps to
land-use class `water` in the land-use batch and water class `reservoir` in the water batch. No
hidden precedence removes either projection.
