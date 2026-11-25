from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry.base import BaseGeometry

OsmPbfSource = str | Path

_OSM_READ_LAYERS: Final = (
    "points",
    "lines",
    "multilinestrings",
    "multipolygons",
)
_DEFAULT_BATCH_SIZE: Final = 4096
_DEFAULT_MAX_FEATURES_PER_LAYER: Final = 2_000_000
_DEFAULT_MAX_TAGS_PER_FEATURE: Final = 128

# This is intentionally a superset of the keys needed to classify T10 source families and
# the obvious raw attributes consumed by the versioned mapping rules introduced in S03-T11.
DEFAULT_RELEVANT_OSM_TAG_KEYS: Final[frozenset[str]] = frozenset(
    {
        "access",
        "admin_level",
        "aeroway",
        "amenity",
        "barrier",
        "brand",
        "bridge",
        "building",
        "building:levels",
        "building:part",
        "capacity",
        "craft",
        "cycleway",
        "emergency",
        "healthcare",
        "height",
        "highway",
        "historic",
        "junction",
        "landuse",
        "lanes",
        "layer",
        "leisure",
        "lit",
        "man_made",
        "maxspeed",
        "military",
        "name",
        "natural",
        "office",
        "oneway",
        "operator",
        "place",
        "public_transport",
        "railway",
        "ref",
        "roof:shape",
        "service",
        "shop",
        "sidewalk",
        "smoothness",
        "sport",
        "surface",
        "tourism",
        "tracktype",
        "tunnel",
        "type",
        "water",
        "waterway",
        "wetland",
        "width",
    }
)

_POI_KEYS: Final[frozenset[str]] = frozenset(
    {
        "amenity",
        "shop",
        "tourism",
        "leisure",
        "office",
        "healthcare",
        "craft",
        "public_transport",
        "emergency",
    }
)
_WATER_NATURAL_VALUES: Final[frozenset[str]] = frozenset(
    {"water", "wetland", "bay", "strait", "coastline"}
)
_WATER_LANDUSE_VALUES: Final[frozenset[str]] = frozenset({"reservoir", "basin"})


class OsmPbfReadError(ValueError):
    """Base error for deterministic bounded OSM PBF reading."""


class OsmPbfLimitError(OsmPbfReadError):
    """Raised when an explicit OSM reader resource bound is exceeded."""

    def __init__(self, *, limit_name: str, limit: int, observed: int) -> None:
        self.limit_name = limit_name
        self.limit = limit
        self.observed = observed
        super().__init__(f"{limit_name} limit exceeded: {observed} > {limit}")


class OsmFeatureCategory(StrEnum):
    ROADS = "roads"
    BUILDINGS = "buildings"
    POI = "poi"
    LANDUSE = "landuse"
    WATER = "water"


class OsmElementType(StrEnum):
    NODE = "node"
    WAY = "way"
    RELATION = "relation"


@dataclass(frozen=True, slots=True)
class OsmPbfReadLimits:
    batch_size: int = _DEFAULT_BATCH_SIZE
    max_features_per_layer: int = _DEFAULT_MAX_FEATURES_PER_LAYER
    max_tags_per_feature: int = _DEFAULT_MAX_TAGS_PER_FEATURE

    def __post_init__(self) -> None:
        for name in ("batch_size", "max_features_per_layer", "max_tags_per_feature"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class OsmFeature:
    """One reconstructed OSM feature with selected raw tags in WGS84 coordinates."""

    source_feature_id: str
    element_type: OsmElementType
    osm_id: int
    tags: Mapping[str, str]
    geometry: BaseGeometry


@dataclass(frozen=True, slots=True)
class OsmFeatureBatch:
    """Bounded category projection from one GDAL OSM source-layer chunk."""

    category: OsmFeatureCategory
    source_layer: str
    start_feature: int
    features: tuple[OsmFeature, ...]
    crs: str = "EPSG:4326"


class OsmPbfBackend(Protocol):
    """Adapter seam around the GDAL OSM driver for deterministic tests."""

    def list_layers(self, source: OsmPbfSource) -> tuple[tuple[str, str | None], ...]: ...

    def feature_count(self, source: OsmPbfSource, *, layer: str) -> int: ...

    def read_chunk(
        self,
        source: OsmPbfSource,
        *,
        layer: str,
        offset: int,
        limit: int,
    ) -> gpd.GeoDataFrame: ...


class PyogrioOsmPbfBackend:
    """Read reconstructed OSM geometries through the existing Pyogrio/GDAL stack."""

    def list_layers(self, source: OsmPbfSource) -> tuple[tuple[str, str | None], ...]:
        rows = pyogrio.list_layers(source)
        result: list[tuple[str, str | None]] = []
        for row in rows:
            name = str(row[0])
            geometry_type = None if row[1] is None else str(row[1])
            result.append((name, geometry_type))
        return tuple(result)

    def feature_count(self, source: OsmPbfSource, *, layer: str) -> int:
        info = pyogrio.read_info(source, layer=layer, force_feature_count=True)
        raw = info.get("features")
        if isinstance(raw, bool):
            raise OsmPbfReadError(f"OSM layer {layer!r} returned invalid feature count")
        try:
            count = int(raw)
        except (TypeError, ValueError) as exc:
            raise OsmPbfReadError(f"OSM layer {layer!r} returned invalid feature count") from exc
        if count < 0:
            raise OsmPbfReadError(f"OSM layer {layer!r} feature count is unavailable")
        return count

    def read_chunk(
        self,
        source: OsmPbfSource,
        *,
        layer: str,
        offset: int,
        limit: int,
    ) -> gpd.GeoDataFrame:
        frame = pyogrio.read_dataframe(
            source,
            layer=layer,
            skip_features=offset,
            max_features=limit,
            use_arrow=False,
            TAGS_FORMAT="JSON",
        )
        if not isinstance(frame, gpd.GeoDataFrame):
            raise OsmPbfReadError(f"OSM layer {layer!r} is not spatial")
        return frame


class OsmPbfReader:
    """Extract bounded category batches from an OSM PBF without internal enum mapping.

    GDAL reconstructs node/way/relation geometries in EPSG:4326. This service only identifies
    broad source families by raw tag presence/value and preserves a configured tag-key superset.
    Value-to-internal-enum mapping belongs to S03-T11.
    """

    def __init__(
        self,
        *,
        backend: OsmPbfBackend | None = None,
        limits: OsmPbfReadLimits | None = None,
        relevant_tag_keys: Iterable[str] = DEFAULT_RELEVANT_OSM_TAG_KEYS,
    ) -> None:
        self._backend = backend or PyogrioOsmPbfBackend()
        self._limits = limits or OsmPbfReadLimits()
        normalized_keys = frozenset(_normalize_tag_key(key) for key in relevant_tag_keys)
        required = self._classification_tag_keys()
        missing = required.difference(normalized_keys)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"relevant_tag_keys must preserve classification keys: {names}")
        self._relevant_tag_keys = normalized_keys

    def iter_batches(
        self,
        source: OsmPbfSource,
        *,
        categories: Iterable[OsmFeatureCategory] | None = None,
    ) -> Iterator[OsmFeatureBatch]:
        selected = self._normalize_categories(categories)
        available = self._available_layers(source)

        for layer in _OSM_READ_LAYERS:
            if layer not in available:
                continue
            feature_count = self._feature_count(source, layer=layer)
            if feature_count > self._limits.max_features_per_layer:
                raise OsmPbfLimitError(
                    limit_name=f"features in OSM layer {layer}",
                    limit=self._limits.max_features_per_layer,
                    observed=feature_count,
                )

            for offset in range(0, feature_count, self._limits.batch_size):
                limit = min(self._limits.batch_size, feature_count - offset)
                frame = self._read_chunk(source, layer=layer, offset=offset, limit=limit)
                self._validate_chunk(
                    frame,
                    layer=layer,
                    requested_limit=limit,
                    expected_features=limit,
                )

                projected = self._project_chunk(
                    frame,
                    layer=layer,
                    selected=selected,
                )
                for category in OsmFeatureCategory:
                    features = projected.get(category)
                    if features:
                        yield OsmFeatureBatch(
                            category=category,
                            source_layer=layer,
                            start_feature=offset,
                            features=tuple(features),
                        )

    def _available_layers(self, source: OsmPbfSource) -> frozenset[str]:
        try:
            rows = self._backend.list_layers(source)
        except OsmPbfReadError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise OsmPbfReadError("unable to list OSM PBF layers") from exc

        names = [name for name, _geometry_type in rows]
        if len(names) != len(set(names)):
            raise OsmPbfReadError("GDAL returned duplicate OSM layer names")
        available = frozenset(names)
        if not available.intersection(_OSM_READ_LAYERS):
            raise OsmPbfReadError("datasource does not expose GDAL OSM spatial layers")
        return available

    def _feature_count(self, source: OsmPbfSource, *, layer: str) -> int:
        try:
            count = self._backend.feature_count(source, layer=layer)
        except OsmPbfReadError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise OsmPbfReadError(f"unable to count OSM layer {layer!r}") from exc
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise OsmPbfReadError(f"OSM backend returned invalid feature count for {layer!r}")
        return count

    def _read_chunk(
        self,
        source: OsmPbfSource,
        *,
        layer: str,
        offset: int,
        limit: int,
    ) -> gpd.GeoDataFrame:
        try:
            return self._backend.read_chunk(
                source,
                layer=layer,
                offset=offset,
                limit=limit,
            )
        except OsmPbfReadError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise OsmPbfReadError(
                f"unable to read OSM layer {layer!r} at feature offset {offset}"
            ) from exc

    @staticmethod
    def _validate_chunk(
        frame: gpd.GeoDataFrame,
        *,
        layer: str,
        requested_limit: int,
        expected_features: int,
    ) -> None:
        if len(frame) > requested_limit:
            raise OsmPbfReadError("OSM backend returned more features than the requested chunk")
        if len(frame) != expected_features:
            raise OsmPbfReadError(
                f"OSM layer {layer!r} returned {len(frame)} features; expected {expected_features}"
            )
        if frame.geometry.name not in frame.columns:
            raise OsmPbfReadError(f"OSM layer {layer!r} has no active geometry column")
        if frame.crs is None:
            raise OsmPbfReadError(f"OSM layer {layer!r} has no CRS")
        try:
            crs = CRS.from_user_input(frame.crs)
            wgs84 = CRS.from_epsg(4326)
        except CRSError as exc:
            raise OsmPbfReadError(f"OSM layer {layer!r} has invalid CRS") from exc
        if crs != wgs84:
            raise OsmPbfReadError(
                f"OSM layer {layer!r} must be EPSG:4326 before mapping; got {crs.to_string()}"
            )

    def _project_chunk(
        self,
        frame: gpd.GeoDataFrame,
        *,
        layer: str,
        selected: frozenset[OsmFeatureCategory],
    ) -> dict[OsmFeatureCategory, list[OsmFeature]]:
        output: dict[OsmFeatureCategory, list[OsmFeature]] = {
            category: [] for category in selected
        }
        geometry_column = frame.geometry.name
        columns = tuple(str(column) for column in frame.columns)
        for values in frame.itertuples(index=False, name=None):
            row = dict(zip(columns, values, strict=True))
            geometry = row.get(geometry_column)
            if geometry is None or _is_missing_scalar(geometry):
                continue
            if not isinstance(geometry, BaseGeometry):
                raise OsmPbfReadError(f"OSM layer {layer!r} contains non-geometry data")
            if geometry.is_empty:
                continue

            element_type, osm_id = _osm_identity(row, layer=layer)
            tags = self._extract_tags(row)
            categories = _classify_tags(tags)
            if not categories.intersection(selected):
                continue
            feature = OsmFeature(
                source_feature_id=f"osm:{element_type.value}:{osm_id}",
                element_type=element_type,
                osm_id=osm_id,
                tags=MappingProxyType(tags),
                geometry=geometry,
            )
            for category in categories:
                if category in selected:
                    output[category].append(feature)
        return output

    def _extract_tags(self, row: Mapping[str, object]) -> dict[str, str]:
        tags: dict[str, str] = {}
        for key in self._relevant_tag_keys:
            value = _string_scalar(row.get(key))
            if value is not None:
                tags[key] = value

        for packed_name in ("all_tags", "other_tags"):
            packed = row.get(packed_name)
            if packed is None or _is_missing_scalar(packed):
                continue
            if isinstance(packed, Mapping):
                decoded = dict(packed)
            elif isinstance(packed, str):
                try:
                    raw_decoded = json.loads(packed)
                except json.JSONDecodeError as exc:
                    raise OsmPbfReadError(f"{packed_name} contains invalid JSON") from exc
                if not isinstance(raw_decoded, dict):
                    raise OsmPbfReadError(f"{packed_name} JSON must be an object")
                decoded = raw_decoded
            else:
                raise OsmPbfReadError(f"{packed_name} must be a tag mapping or JSON text")
            for raw_key, raw_value in decoded.items():
                key = str(raw_key)
                if key not in self._relevant_tag_keys or key in tags:
                    continue
                value = _string_scalar(raw_value)
                if value is not None:
                    tags[key] = value

        if len(tags) > self._limits.max_tags_per_feature:
            raise OsmPbfLimitError(
                limit_name="relevant OSM tags per feature",
                limit=self._limits.max_tags_per_feature,
                observed=len(tags),
            )
        return dict(sorted(tags.items()))

    @staticmethod
    def _normalize_categories(
        categories: Iterable[OsmFeatureCategory] | None,
    ) -> frozenset[OsmFeatureCategory]:
        if categories is None:
            return frozenset(OsmFeatureCategory)
        selected = frozenset(categories)
        if not selected:
            raise ValueError("categories must not be empty")
        if any(not isinstance(category, OsmFeatureCategory) for category in selected):
            raise TypeError("categories must contain OsmFeatureCategory values")
        return selected

    @staticmethod
    def _classification_tag_keys() -> frozenset[str]:
        return frozenset(
            {
                "highway",
                "building",
                "building:part",
                "landuse",
                "natural",
                "water",
                "waterway",
                "wetland",
                *_POI_KEYS,
            }
        )


def _classify_tags(tags: Mapping[str, str]) -> frozenset[OsmFeatureCategory]:
    categories: set[OsmFeatureCategory] = set()
    if tags.get("highway"):
        categories.add(OsmFeatureCategory.ROADS)
    if tags.get("building") or tags.get("building:part"):
        categories.add(OsmFeatureCategory.BUILDINGS)
    if any(tags.get(key) for key in _POI_KEYS):
        categories.add(OsmFeatureCategory.POI)
    if tags.get("landuse"):
        categories.add(OsmFeatureCategory.LANDUSE)
    if (
        tags.get("waterway")
        or tags.get("water")
        or tags.get("wetland")
        or tags.get("natural") in _WATER_NATURAL_VALUES
        or tags.get("landuse") in _WATER_LANDUSE_VALUES
    ):
        categories.add(OsmFeatureCategory.WATER)
    return frozenset(categories)


def _osm_identity(
    row: Mapping[str, object],
    *,
    layer: str,
) -> tuple[OsmElementType, int]:
    if layer == "points":
        return OsmElementType.NODE, _required_osm_id(row.get("osm_id"), field="osm_id")
    if layer == "lines":
        return OsmElementType.WAY, _required_osm_id(row.get("osm_id"), field="osm_id")
    if layer == "multilinestrings":
        return OsmElementType.RELATION, _required_osm_id(row.get("osm_id"), field="osm_id")
    if layer == "multipolygons":
        way_id = _optional_osm_id(row.get("osm_way_id"), field="osm_way_id")
        relation_id = _optional_osm_id(row.get("osm_id"), field="osm_id")
        if way_id is not None and relation_id is None:
            return OsmElementType.WAY, way_id
        if relation_id is not None and way_id is None:
            return OsmElementType.RELATION, relation_id
        raise OsmPbfReadError(
            "OSM multipolygon feature must identify exactly one way or relation id"
        )
    raise OsmPbfReadError(f"unsupported GDAL OSM layer: {layer!r}")


def _required_osm_id(value: object, *, field: str) -> int:
    result = _optional_osm_id(value, field=field)
    if result is None:
        raise OsmPbfReadError(f"OSM feature is missing {field}")
    return result


def _optional_osm_id(value: object, *, field: str) -> int | None:
    if value is None or _is_missing_scalar(value):
        return None
    if isinstance(value, bool):
        raise OsmPbfReadError(f"OSM {field} must be an integer")
    if isinstance(value, int):
        result = value
    else:
        try:
            result = int(str(value))
        except ValueError as exc:
            raise OsmPbfReadError(f"OSM {field} must be an integer") from exc
    if result <= 0:
        raise OsmPbfReadError(f"OSM {field} must be positive")
    return result


def _normalize_tag_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("OSM tag keys must be non-empty strings")
    key = value.strip()
    if len(key) > 255:
        raise ValueError("OSM tag keys must be at most 255 characters")
    return key


def _string_scalar(value: object) -> str | None:
    if value is None or _is_missing_scalar(value):
        return None
    if not isinstance(value, (str, int, float, bool)):
        return str(value)
    text = str(value).strip()
    return text or None


def _is_missing_scalar(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (Mapping, list, tuple, set, frozenset, BaseGeometry)):
        return False
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if isinstance(missing, bool):
        return missing
    item = getattr(missing, "item", None)
    if callable(item):
        try:
            return bool(item())
        except (TypeError, ValueError):
            return False
    return False
