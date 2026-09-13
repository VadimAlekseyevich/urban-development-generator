from pathlib import Path

import pytest
from shapely.geometry import LineString, Point, Polygon

from backend.app.services.osm_mapping import (
    BuildingClass,
    DEFAULT_OSM_MAPPING_RULESET,
    FacilityClass,
    LanduseClass,
    OSM_MAPPING_RULESET_VERSION,
    OsmCategoryMapping,
    OsmMappedLayer,
    OsmMappingError,
    OsmMappingRuleset,
    OsmTagMapper,
    OsmTagRule,
    RoadClass,
    WaterClass,
)
from backend.app.services.osm_pbf_reader import (
    OsmElementType,
    OsmFeature,
    OsmFeatureBatch,
    OsmFeatureCategory,
    OsmPbfReadLimits,
    OsmPbfReader,
)

FIXTURE = Path("tests/fixtures/osm/minimal.pbf")


def _feature(
    *,
    osm_id: int,
    tags: dict[str, str],
    geometry: Point | LineString | Polygon | None = None,
    element_type: OsmElementType = OsmElementType.WAY,
) -> OsmFeature:
    return OsmFeature(
        source_feature_id=f"osm:{element_type.value}:{osm_id}",
        element_type=element_type,
        osm_id=osm_id,
        tags=tags,
        geometry=geometry or LineString([(0, 0), (1, 0)]),
    )


def _batch(
    category: OsmFeatureCategory,
    *features: OsmFeature,
    crs: str = "EPSG:4326",
) -> OsmFeatureBatch:
    return OsmFeatureBatch(
        category=category,
        source_layer="lines",
        start_feature=0,
        features=features,
        crs=crs,
    )


def test_default_ruleset_is_complete_and_versioned() -> None:
    assert DEFAULT_OSM_MAPPING_RULESET.version == OSM_MAPPING_RULESET_VERSION == "osm-v1"
    assert {mapping.category for mapping in DEFAULT_OSM_MAPPING_RULESET.categories} == set(
        OsmFeatureCategory
    )
    assert DEFAULT_OSM_MAPPING_RULESET.for_category(OsmFeatureCategory.POI).layer is (
        OsmMappedLayer.FACILITIES
    )


def test_mapper_maps_road_class_and_typed_canonical_attributes() -> None:
    feature = _feature(
        osm_id=10,
        tags={
            "highway": "primary",
            "name": "Main Street",
            "lanes": "2",
            "maxspeed": "30 mph",
            "oneway": "-1",
            "surface": "asphalt",
        },
    )

    mapped = OsmTagMapper().map_batch(_batch(OsmFeatureCategory.ROADS, feature)).features[0]

    assert mapped.target_layer is OsmMappedLayer.ROADS
    assert mapped.internal_class is RoadClass.PRIMARY
    assert mapped.rule_id == "road.primary"
    assert mapped.canonical_attributes["road_class"] == "primary"
    assert mapped.canonical_attributes["name"] == "Main Street"
    assert mapped.canonical_attributes["lanes"] == 2
    assert mapped.canonical_attributes["max_speed_kph"] == pytest.approx(48.28032)
    assert mapped.canonical_attributes["one_way"] is True
    details = mapped.canonical_attributes["attributes_json"]
    assert isinstance(details, dict)
    assert details["osm_tags"] == feature.tags
    assert details["osm_mapping_version"] == "osm-v1"
    assert details["osm_mapping_rule"] == "road.primary"


def test_mapper_maps_building_attributes_without_inventing_invalid_numbers() -> None:
    feature = _feature(
        osm_id=20,
        tags={
            "building": "apartments",
            "building:levels": "6",
            "height": "60 ft",
            "name": "House A",
        },
        geometry=Polygon([(0, 0), (1, 0), (1, 1), (0, 0)]),
    )

    mapped = OsmTagMapper().map_feature(category=OsmFeatureCategory.BUILDINGS, feature=feature)

    assert mapped.internal_class is BuildingClass.RESIDENTIAL
    assert mapped.canonical_attributes["building_class"] == "residential"
    assert mapped.canonical_attributes["levels"] == 6
    assert mapped.canonical_attributes["height_m"] == pytest.approx(18.288)

    invalid = _feature(
        osm_id=21,
        tags={"building": "yes", "building:levels": "2;3", "height": "approx 10"},
    )
    fallback = OsmTagMapper().map_feature(
        category=OsmFeatureCategory.BUILDINGS,
        feature=invalid,
    )
    assert fallback.internal_class is BuildingClass.OTHER
    assert fallback.canonical_attributes["levels"] is None
    assert fallback.canonical_attributes["height_m"] is None


def test_poi_mapping_uses_explicit_rule_precedence() -> None:
    feature = _feature(
        osm_id=30,
        tags={
            "amenity": "school",
            "shop": "convenience",
            "name": "Mixed tags",
            "capacity": "450",
        },
        geometry=Point(0, 0),
        element_type=OsmElementType.NODE,
    )

    mapped = OsmTagMapper().map_feature(category=OsmFeatureCategory.POI, feature=feature)

    assert mapped.target_layer is OsmMappedLayer.FACILITIES
    assert mapped.internal_class is FacilityClass.EDUCATION
    assert mapped.rule_id == "facility.education"
    assert mapped.canonical_attributes["facility_class"] == "education"
    assert mapped.canonical_attributes["capacity"] == 450.0


def test_landuse_and_water_rules_are_independent_for_multi_category_feature() -> None:
    feature = _feature(osm_id=40, tags={"landuse": "reservoir"})
    mapper = OsmTagMapper()

    landuse = mapper.map_feature(category=OsmFeatureCategory.LANDUSE, feature=feature)
    water = mapper.map_feature(category=OsmFeatureCategory.WATER, feature=feature)

    assert landuse.internal_class is LanduseClass.WATER
    assert landuse.canonical_attributes["landuse_class"] == "water"
    assert water.internal_class is WaterClass.RESERVOIR
    assert water.canonical_attributes["water_class"] == "reservoir"
    assert landuse.source_feature_id == water.source_feature_id


def test_unknown_values_use_explicit_other_default_and_preserve_raw_tags() -> None:
    feature = _feature(osm_id=50, tags={"highway": "future_road", "ref": "X-1"})

    mapped = OsmTagMapper().map_feature(category=OsmFeatureCategory.ROADS, feature=feature)

    assert mapped.internal_class is RoadClass.OTHER
    assert mapped.rule_id == "default"
    assert dict(mapped.source_tags) == feature.tags
    assert mapped.canonical_attributes["road_class"] == "other"


def test_ruleset_validation_rejects_unreachable_and_incomplete_config() -> None:
    with pytest.raises(ValueError, match="after wildcard"):
        OsmCategoryMapping(
            category=OsmFeatureCategory.ROADS,
            rules=(
                OsmTagRule("wild", "highway", None, "other"),
                OsmTagRule("later", "highway", frozenset({"primary"}), "primary"),
            ),
            default_value="other",
        )

    with pytest.raises(ValueError, match="every category"):
        OsmMappingRuleset(
            version="test-v1",
            categories=(DEFAULT_OSM_MAPPING_RULESET.categories[0],),
        )

    with pytest.raises(ValueError, match="invalid for roads"):
        OsmCategoryMapping(
            category=OsmFeatureCategory.ROADS,
            rules=(OsmTagRule("bad", "highway", frozenset({"primary"}), "hospital"),),
            default_value="other",
        )


def test_mapper_rejects_non_wgs84_batch_before_mapping_features() -> None:
    with pytest.raises(OsmMappingError, match="EPSG:4326"):
        OsmTagMapper().map_batch(_batch(OsmFeatureCategory.ROADS, crs="EPSG:3857"))


def test_reader_and_mapper_compose_on_real_pbf_fixture() -> None:
    reader = OsmPbfReader(
        limits=OsmPbfReadLimits(batch_size=2, max_features_per_layer=20)
    )
    mapper = OsmTagMapper()

    mapped_batches = [mapper.map_batch(batch) for batch in reader.iter_batches(FIXTURE)]
    features = {
        category: [
            feature
            for batch in mapped_batches
            if batch.source_category is category
            for feature in batch.features
        ]
        for category in OsmFeatureCategory
    }

    assert features[OsmFeatureCategory.ROADS][0].internal_class is RoadClass.LOCAL
    assert features[OsmFeatureCategory.BUILDINGS][0].internal_class is BuildingClass.OTHER
    assert features[OsmFeatureCategory.POI][0].internal_class is FacilityClass.EDUCATION
    assert all(batch.mapping_version == "osm-v1" for batch in mapped_batches)
    assert all(batch.crs == "EPSG:4326" for batch in mapped_batches)
