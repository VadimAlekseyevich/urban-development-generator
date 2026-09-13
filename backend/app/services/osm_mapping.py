from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry.base import BaseGeometry

from backend.app.services.osm_pbf_reader import (
    OsmElementType,
    OsmFeature,
    OsmFeatureBatch,
    OsmFeatureCategory,
)

OSM_MAPPING_RULESET_VERSION: Final = "osm-v1"


class OsmMappingError(ValueError):
    """Raised when an OSM batch or versioned mapping contract is invalid."""


class OsmMappedLayer(StrEnum):
    ROADS = "roads"
    BUILDINGS = "buildings"
    FACILITIES = "facilities"
    LANDUSE = "landuse"
    WATER = "water"


class RoadClass(StrEnum):
    MOTORWAY = "motorway"
    TRUNK = "trunk"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    LOCAL = "local"
    SERVICE = "service"
    TRACK = "track"
    PATH = "path"
    OTHER = "other"


class BuildingClass(StrEnum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    INDUSTRIAL = "industrial"
    RETAIL = "retail"
    EDUCATION = "education"
    HEALTHCARE = "healthcare"
    CIVIC = "civic"
    GARAGE = "garage"
    OTHER = "other"


class FacilityClass(StrEnum):
    EDUCATION = "education"
    HEALTHCARE = "healthcare"
    EMERGENCY = "emergency"
    TRANSIT = "transit"
    RETAIL = "retail"
    FOOD = "food"
    RECREATION = "recreation"
    CULTURE = "culture"
    GOVERNMENT = "government"
    OTHER = "other"


class LanduseClass(StrEnum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    RETAIL = "retail"
    INDUSTRIAL = "industrial"
    RECREATION = "recreation"
    FOREST = "forest"
    AGRICULTURE = "agriculture"
    GRASS = "grass"
    CEMETERY = "cemetery"
    MILITARY = "military"
    WATER = "water"
    OTHER = "other"


class WaterClass(StrEnum):
    RIVER = "river"
    STREAM = "stream"
    CANAL = "canal"
    DRAIN = "drain"
    LAKE = "lake"
    RESERVOIR = "reservoir"
    BASIN = "basin"
    WETLAND = "wetland"
    COASTAL = "coastal"
    OTHER = "other"


type OsmInternalClass = RoadClass | BuildingClass | FacilityClass | LanduseClass | WaterClass


@dataclass(frozen=True, slots=True)
class _CategoryContract:
    layer: OsmMappedLayer
    class_column: str
    allowed_values: frozenset[str]


_CATEGORY_CONTRACTS: Final[dict[OsmFeatureCategory, _CategoryContract]] = {
    OsmFeatureCategory.ROADS: _CategoryContract(
        OsmMappedLayer.ROADS,
        "road_class",
        frozenset(item.value for item in RoadClass),
    ),
    OsmFeatureCategory.BUILDINGS: _CategoryContract(
        OsmMappedLayer.BUILDINGS,
        "building_class",
        frozenset(item.value for item in BuildingClass),
    ),
    OsmFeatureCategory.POI: _CategoryContract(
        OsmMappedLayer.FACILITIES,
        "facility_class",
        frozenset(item.value for item in FacilityClass),
    ),
    OsmFeatureCategory.LANDUSE: _CategoryContract(
        OsmMappedLayer.LANDUSE,
        "landuse_class",
        frozenset(item.value for item in LanduseClass),
    ),
    OsmFeatureCategory.WATER: _CategoryContract(
        OsmMappedLayer.WATER,
        "water_class",
        frozenset(item.value for item in WaterClass),
    ),
}


@dataclass(frozen=True, slots=True)
class OsmTagRule:
    """One ordered, auditable tag-value rule.

    ``values=None`` means any non-empty value for ``key``. Exact rules should precede a
    wildcard for the same key; validation rejects unreachable later rules.
    """

    rule_id: str
    key: str
    values: frozenset[str] | None
    mapped_value: str

    def __post_init__(self) -> None:
        rule_id = self.rule_id.strip()
        key = self.key.strip().casefold()
        mapped_value = self.mapped_value.strip().casefold()
        if not rule_id:
            raise ValueError("OSM mapping rule_id must not be blank")
        if not key:
            raise ValueError("OSM mapping rule key must not be blank")
        if not mapped_value:
            raise ValueError("OSM mapping rule mapped_value must not be blank")
        values = self.values
        if values is not None:
            normalized_values = frozenset(_normalize_tag_value(value) for value in values)
            if not normalized_values:
                raise ValueError("OSM mapping rule values must not be empty")
            values = normalized_values
        object.__setattr__(self, "rule_id", rule_id)
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "mapped_value", mapped_value)
        object.__setattr__(self, "values", values)

    def matches(self, tags: Mapping[str, str]) -> bool:
        raw_value = tags.get(self.key)
        if raw_value is None or not raw_value.strip():
            return False
        if self.values is None:
            return True
        return _normalize_tag_value(raw_value) in self.values


@dataclass(frozen=True, slots=True)
class OsmCategoryMapping:
    category: OsmFeatureCategory
    rules: tuple[OsmTagRule, ...]
    default_value: str

    def __post_init__(self) -> None:
        if not isinstance(self.category, OsmFeatureCategory):
            raise TypeError("category must be OsmFeatureCategory")
        contract = _CATEGORY_CONTRACTS[self.category]
        default_value = self.default_value.strip().casefold()
        if default_value not in contract.allowed_values:
            raise ValueError(
                f"default mapping value {default_value!r} is invalid for {self.category.value}"
            )

        rule_ids: set[str] = set()
        signatures: set[tuple[str, frozenset[str] | None]] = set()
        wildcard_keys: set[str] = set()
        for rule in self.rules:
            if not isinstance(rule, OsmTagRule):
                raise TypeError("rules must contain OsmTagRule values")
            if rule.rule_id in rule_ids:
                raise ValueError(f"duplicate OSM mapping rule id {rule.rule_id!r}")
            if rule.mapped_value not in contract.allowed_values:
                raise ValueError(
                    f"mapping value {rule.mapped_value!r} is invalid for {self.category.value}"
                )
            signature = (rule.key, rule.values)
            if signature in signatures:
                raise ValueError(
                    f"duplicate OSM mapping matcher for {self.category.value}: {rule.key!r}"
                )
            if rule.key in wildcard_keys:
                raise ValueError(
                    f"OSM mapping rule after wildcard for key {rule.key!r} is unreachable"
                )
            if rule.values is None:
                wildcard_keys.add(rule.key)
            rule_ids.add(rule.rule_id)
            signatures.add(signature)

        object.__setattr__(self, "default_value", default_value)

    @property
    def layer(self) -> OsmMappedLayer:
        return _CATEGORY_CONTRACTS[self.category].layer

    @property
    def class_column(self) -> str:
        return _CATEGORY_CONTRACTS[self.category].class_column

    def match(self, tags: Mapping[str, str]) -> tuple[str, str]:
        for rule in self.rules:
            if rule.matches(tags):
                return rule.mapped_value, rule.rule_id
        return self.default_value, "default"


@dataclass(frozen=True, slots=True)
class OsmMappingRuleset:
    version: str
    categories: tuple[OsmCategoryMapping, ...]

    def __post_init__(self) -> None:
        version = self.version.strip()
        if not version:
            raise ValueError("OSM mapping ruleset version must not be blank")
        if len(version) > 64:
            raise ValueError("OSM mapping ruleset version must be at most 64 characters")
        category_values = tuple(mapping.category for mapping in self.categories)
        if len(category_values) != len(set(category_values)):
            raise ValueError("OSM mapping ruleset contains duplicate categories")
        expected = frozenset(OsmFeatureCategory)
        observed = frozenset(category_values)
        if observed != expected:
            missing = ", ".join(sorted(item.value for item in expected - observed))
            extra = ", ".join(sorted(item.value for item in observed - expected))
            parts = (
                f"missing: {missing}" if missing else "",
                f"extra: {extra}" if extra else "",
            )
            details = "; ".join(part for part in parts if part)
            raise ValueError(f"OSM mapping ruleset must define every category ({details})")
        object.__setattr__(self, "version", version)

    def for_category(self, category: OsmFeatureCategory) -> OsmCategoryMapping:
        for mapping in self.categories:
            if mapping.category is category:
                return mapping
        raise OsmMappingError(f"mapping ruleset has no category {category.value!r}")


@dataclass(frozen=True, slots=True)
class MappedOsmFeature:
    source_feature_id: str
    element_type: OsmElementType
    osm_id: int
    target_layer: OsmMappedLayer
    internal_class: OsmInternalClass
    canonical_attributes: Mapping[str, object]
    source_tags: Mapping[str, str]
    geometry: BaseGeometry
    rule_id: str
    mapping_version: str


@dataclass(frozen=True, slots=True)
class MappedOsmBatch:
    source_category: OsmFeatureCategory
    target_layer: OsmMappedLayer
    source_layer: str
    start_feature: int
    features: tuple[MappedOsmFeature, ...]
    mapping_version: str
    crs: str = "EPSG:4326"


class OsmTagMapper:
    """Apply a versioned declarative mapping ruleset to T10 OSM batches."""

    def __init__(self, ruleset: OsmMappingRuleset | None = None) -> None:
        self._ruleset = ruleset or DEFAULT_OSM_MAPPING_RULESET

    @property
    def ruleset_version(self) -> str:
        return self._ruleset.version

    def map_batch(self, batch: OsmFeatureBatch) -> MappedOsmBatch:
        if not isinstance(batch.category, OsmFeatureCategory):
            raise OsmMappingError("OSM batch category is invalid")
        _require_wgs84(batch.crs)
        category_mapping = self._ruleset.for_category(batch.category)
        mapped = tuple(
            self.map_feature(category=batch.category, feature=feature)
            for feature in batch.features
        )
        return MappedOsmBatch(
            source_category=batch.category,
            target_layer=category_mapping.layer,
            source_layer=batch.source_layer,
            start_feature=batch.start_feature,
            features=mapped,
            mapping_version=self._ruleset.version,
        )

    def map_feature(
        self,
        *,
        category: OsmFeatureCategory,
        feature: OsmFeature,
    ) -> MappedOsmFeature:
        if not isinstance(category, OsmFeatureCategory):
            raise TypeError("category must be OsmFeatureCategory")
        if not isinstance(feature.geometry, BaseGeometry) or feature.geometry.is_empty:
            raise OsmMappingError("OSM mapping requires a non-empty geometry")
        if not feature.source_feature_id.strip():
            raise OsmMappingError("OSM source_feature_id must not be blank")

        category_mapping = self._ruleset.for_category(category)
        mapped_value, rule_id = category_mapping.match(feature.tags)
        internal_class = _coerce_internal_class(category, mapped_value)
        attributes = _canonical_attributes(
            category=category,
            feature=feature,
            internal_class=internal_class,
            class_column=category_mapping.class_column,
            mapping_version=self._ruleset.version,
            rule_id=rule_id,
        )
        return MappedOsmFeature(
            source_feature_id=feature.source_feature_id,
            element_type=feature.element_type,
            osm_id=feature.osm_id,
            target_layer=category_mapping.layer,
            internal_class=internal_class,
            canonical_attributes=MappingProxyType(attributes),
            source_tags=MappingProxyType(dict(feature.tags)),
            geometry=feature.geometry,
            rule_id=rule_id,
            mapping_version=self._ruleset.version,
        )


def _canonical_attributes(
    *,
    category: OsmFeatureCategory,
    feature: OsmFeature,
    internal_class: OsmInternalClass,
    class_column: str,
    mapping_version: str,
    rule_id: str,
) -> dict[str, object]:
    tags = feature.tags
    attributes: dict[str, object] = {
        class_column: internal_class.value,
        "attributes_json": {
            "osm_tags": dict(tags),
            "osm_mapping_version": mapping_version,
            "osm_mapping_rule": rule_id,
            "osm_element_type": feature.element_type.value,
            "osm_id": feature.osm_id,
        },
    }
    if category is OsmFeatureCategory.ROADS:
        attributes.update(
            {
                "name": _optional_text(tags.get("name")),
                "lanes": _positive_int(tags.get("lanes")),
                "max_speed_kph": _maxspeed_kph(tags.get("maxspeed")),
                "one_way": _oneway(tags.get("oneway")),
            }
        )
    elif category is OsmFeatureCategory.BUILDINGS:
        attributes.update(
            {
                "name": _optional_text(tags.get("name")),
                "levels": _positive_int(tags.get("building:levels")),
                "height_m": _distance_m(tags.get("height")),
            }
        )
    elif category is OsmFeatureCategory.POI:
        attributes.update(
            {
                "name": _optional_text(tags.get("name")),
                "capacity": _nonnegative_float(tags.get("capacity")),
            }
        )
    return attributes


def _coerce_internal_class(
    category: OsmFeatureCategory,
    value: str,
) -> OsmInternalClass:
    if category is OsmFeatureCategory.ROADS:
        return RoadClass(value)
    if category is OsmFeatureCategory.BUILDINGS:
        return BuildingClass(value)
    if category is OsmFeatureCategory.POI:
        return FacilityClass(value)
    if category is OsmFeatureCategory.LANDUSE:
        return LanduseClass(value)
    if category is OsmFeatureCategory.WATER:
        return WaterClass(value)
    raise OsmMappingError(f"unsupported OSM category {category!r}")


def _require_wgs84(value: str) -> None:
    try:
        crs = CRS.from_user_input(value)
        wgs84 = CRS.from_epsg(4326)
    except CRSError as exc:
        raise OsmMappingError("OSM mapping batch CRS is invalid") from exc
    if crs != wgs84:
        raise OsmMappingError(f"OSM mapping requires EPSG:4326 input, got {crs.to_string()}")


def _normalize_tag_value(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("OSM mapping rule values must be strings")
    normalized = value.strip().casefold()
    if not normalized:
        raise ValueError("OSM mapping rule values must not be blank")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _positive_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if not text or not re.fullmatch(r"\d+", text):
        return None
    parsed = int(text)
    return parsed if parsed > 0 else None


def _nonnegative_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value.strip())
    except ValueError:
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _distance_m(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(m|meter|meters|metre|metres|ft|feet|')?\s*",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    parsed = float(match.group(1))
    unit = (match.group(2) or "m").casefold()
    if unit in {"ft", "feet", "'"}:
        parsed *= 0.3048
    return parsed if parsed > 0 and math.isfinite(parsed) else None


def _maxspeed_kph(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(km/?h|kph|mph)?\s*",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    parsed = float(match.group(1))
    unit = (match.group(2) or "km/h").casefold()
    if unit == "mph":
        parsed *= 1.609344
    return parsed if parsed > 0 and math.isfinite(parsed) else None


def _oneway(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().casefold() in {"yes", "true", "1", "-1"}


def _rule(
    rule_id: str,
    key: str,
    values: tuple[str, ...] | None,
    mapped_value: StrEnum,
) -> OsmTagRule:
    return OsmTagRule(
        rule_id=rule_id,
        key=key,
        values=None if values is None else frozenset(values),
        mapped_value=mapped_value.value,
    )


DEFAULT_OSM_MAPPING_RULESET: Final = OsmMappingRuleset(
    version=OSM_MAPPING_RULESET_VERSION,
    categories=(
        OsmCategoryMapping(
            category=OsmFeatureCategory.ROADS,
            rules=(
                _rule(
                    "road.motorway",
                    "highway",
                    ("motorway", "motorway_link"),
                    RoadClass.MOTORWAY,
                ),
                _rule("road.trunk", "highway", ("trunk", "trunk_link"), RoadClass.TRUNK),
                _rule("road.primary", "highway", ("primary", "primary_link"), RoadClass.PRIMARY),
                _rule(
                    "road.secondary",
                    "highway",
                    ("secondary", "secondary_link"),
                    RoadClass.SECONDARY,
                ),
                _rule(
                    "road.tertiary",
                    "highway",
                    ("tertiary", "tertiary_link"),
                    RoadClass.TERTIARY,
                ),
                _rule(
                    "road.local",
                    "highway",
                    ("residential", "living_street", "unclassified"),
                    RoadClass.LOCAL,
                ),
                _rule("road.service", "highway", ("service",), RoadClass.SERVICE),
                _rule("road.track", "highway", ("track",), RoadClass.TRACK),
                _rule(
                    "road.path",
                    "highway",
                    ("path", "footway", "pedestrian", "cycleway", "bridleway", "steps"),
                    RoadClass.PATH,
                ),
            ),
            default_value=RoadClass.OTHER.value,
        ),
        OsmCategoryMapping(
            category=OsmFeatureCategory.BUILDINGS,
            rules=(
                _rule(
                    "building.residential",
                    "building",
                    (
                        "residential",
                        "apartments",
                        "house",
                        "detached",
                        "semidetached_house",
                        "terrace",
                        "dormitory",
                    ),
                    BuildingClass.RESIDENTIAL,
                ),
                _rule(
                    "building.commercial",
                    "building",
                    ("commercial", "office", "hotel"),
                    BuildingClass.COMMERCIAL,
                ),
                _rule(
                    "building.industrial",
                    "building",
                    ("industrial", "warehouse", "manufacture"),
                    BuildingClass.INDUSTRIAL,
                ),
                _rule(
                    "building.retail",
                    "building",
                    ("retail", "supermarket", "kiosk"),
                    BuildingClass.RETAIL,
                ),
                _rule(
                    "building.education",
                    "building",
                    ("school", "college", "university", "kindergarten"),
                    BuildingClass.EDUCATION,
                ),
                _rule(
                    "building.healthcare",
                    "building",
                    ("hospital", "clinic"),
                    BuildingClass.HEALTHCARE,
                ),
                _rule(
                    "building.civic",
                    "building",
                    ("civic", "government", "public"),
                    BuildingClass.CIVIC,
                ),
                _rule(
                    "building.garage",
                    "building",
                    ("garage", "garages", "carport", "parking"),
                    BuildingClass.GARAGE,
                ),
                _rule(
                    "building-part.residential",
                    "building:part",
                    ("residential", "apartments", "house"),
                    BuildingClass.RESIDENTIAL,
                ),
                _rule(
                    "building-part.commercial",
                    "building:part",
                    ("commercial", "office"),
                    BuildingClass.COMMERCIAL,
                ),
                _rule(
                    "building-part.industrial",
                    "building:part",
                    ("industrial", "warehouse"),
                    BuildingClass.INDUSTRIAL,
                ),
                _rule("building-part.retail", "building:part", ("retail",), BuildingClass.RETAIL),
            ),
            default_value=BuildingClass.OTHER.value,
        ),
        OsmCategoryMapping(
            category=OsmFeatureCategory.POI,
            rules=(
                _rule(
                    "facility.education",
                    "amenity",
                    ("school", "college", "university", "kindergarten", "library"),
                    FacilityClass.EDUCATION,
                ),
                _rule(
                    "facility.healthcare",
                    "amenity",
                    ("hospital", "clinic", "doctors", "dentist", "pharmacy"),
                    FacilityClass.HEALTHCARE,
                ),
                _rule("facility.healthcare-tag", "healthcare", None, FacilityClass.HEALTHCARE),
                _rule(
                    "facility.emergency",
                    "amenity",
                    ("fire_station", "police"),
                    FacilityClass.EMERGENCY,
                ),
                _rule("facility.emergency-tag", "emergency", None, FacilityClass.EMERGENCY),
                _rule("facility.transit", "public_transport", None, FacilityClass.TRANSIT),
                _rule(
                    "facility.food",
                    "amenity",
                    ("restaurant", "cafe", "fast_food", "bar", "pub", "food_court"),
                    FacilityClass.FOOD,
                ),
                _rule(
                    "facility.government",
                    "amenity",
                    ("townhall", "courthouse", "post_office"),
                    FacilityClass.GOVERNMENT,
                ),
                _rule(
                    "facility.government-office",
                    "office",
                    ("government",),
                    FacilityClass.GOVERNMENT,
                ),
                _rule("facility.retail", "shop", None, FacilityClass.RETAIL),
                _rule("facility.recreation", "leisure", None, FacilityClass.RECREATION),
                _rule(
                    "facility.culture",
                    "tourism",
                    ("museum", "gallery", "attraction", "artwork"),
                    FacilityClass.CULTURE,
                ),
            ),
            default_value=FacilityClass.OTHER.value,
        ),
        OsmCategoryMapping(
            category=OsmFeatureCategory.LANDUSE,
            rules=(
                _rule("landuse.residential", "landuse", ("residential",), LanduseClass.RESIDENTIAL),
                _rule("landuse.commercial", "landuse", ("commercial",), LanduseClass.COMMERCIAL),
                _rule("landuse.retail", "landuse", ("retail",), LanduseClass.RETAIL),
                _rule(
                    "landuse.industrial",
                    "landuse",
                    ("industrial", "construction"),
                    LanduseClass.INDUSTRIAL,
                ),
                _rule(
                    "landuse.recreation",
                    "landuse",
                    ("recreation_ground", "village_green"),
                    LanduseClass.RECREATION,
                ),
                _rule("landuse.forest", "landuse", ("forest",), LanduseClass.FOREST),
                _rule(
                    "landuse.agriculture",
                    "landuse",
                    ("farmland", "farmyard", "orchard", "vineyard", "greenhouse_horticulture"),
                    LanduseClass.AGRICULTURE,
                ),
                _rule("landuse.grass", "landuse", ("grass", "meadow"), LanduseClass.GRASS),
                _rule("landuse.cemetery", "landuse", ("cemetery",), LanduseClass.CEMETERY),
                _rule("landuse.military", "landuse", ("military",), LanduseClass.MILITARY),
                _rule("landuse.water", "landuse", ("reservoir", "basin"), LanduseClass.WATER),
            ),
            default_value=LanduseClass.OTHER.value,
        ),
        OsmCategoryMapping(
            category=OsmFeatureCategory.WATER,
            rules=(
                _rule("water.river", "waterway", ("river",), WaterClass.RIVER),
                _rule("water.stream", "waterway", ("stream",), WaterClass.STREAM),
                _rule("water.canal", "waterway", ("canal",), WaterClass.CANAL),
                _rule("water.drain", "waterway", ("drain", "ditch"), WaterClass.DRAIN),
                _rule("water.lake", "water", ("lake", "pond"), WaterClass.LAKE),
                _rule("water.reservoir", "water", ("reservoir",), WaterClass.RESERVOIR),
                _rule("water.reservoir-landuse", "landuse", ("reservoir",), WaterClass.RESERVOIR),
                _rule("water.basin", "water", ("basin",), WaterClass.BASIN),
                _rule("water.basin-landuse", "landuse", ("basin",), WaterClass.BASIN),
                _rule("water.wetland", "wetland", None, WaterClass.WETLAND),
                _rule("water.wetland-natural", "natural", ("wetland",), WaterClass.WETLAND),
                _rule(
                    "water.coastal",
                    "natural",
                    ("coastline", "bay", "strait"),
                    WaterClass.COASTAL,
                ),
            ),
            default_value=WaterClass.OTHER.value,
        ),
    ),
)
