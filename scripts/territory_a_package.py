"""Prepare immutable, ingest-compatible Territory A GeoJSON from a pinned OSM extract."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import geopandas as gpd
import pyogrio
from pyproj import CRS
from shapely import make_valid, set_precision
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
    mapping,
)
from shapely.geometry.base import BaseGeometry

from backend.app.services.osm_mapping import OsmTagMapper
from backend.app.services.osm_pbf_reader import (
    DEFAULT_RELEVANT_OSM_TAG_KEYS,
    OsmElementType,
    OsmFeature,
    OsmFeatureCategory,
)

PACKAGE_VERSION = "territory-a-osm-v1"
LAYERS = ("roads", "buildings", "landuse", "water", "facilities")
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
ReadOsmLayer = Callable[..., gpd.GeoDataFrame]


@dataclass(frozen=True)
class TerritorySpec:
    territory_id: str
    source_url: str
    bbox: tuple[float, float, float, float]
    working_srid: int
    max_features_per_layer: int


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_spec(path: Path) -> TerritorySpec:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version", "territory_id", "mode", "source_url", "bbox_wgs84",
        "working_srid", "max_features_per_layer",
    }:
        raise ValueError("territory manifest has missing/unknown fields")
    if raw["schema_version"] != 1 or raw["mode"] != "expansion":
        raise ValueError("Territory A requires schema v1 and expansion mode")
    territory_id, url = raw["territory_id"], raw["source_url"]
    if not isinstance(territory_id, str) or not territory_id or not territory_id.isascii():
        raise ValueError("territory_id must be nonempty ASCII")
    if not isinstance(url, str) or not url.startswith("https://download.geofabrik.de/"):
        raise ValueError("source_url must identify a Geofabrik HTTPS extract")
    coords = raw["bbox_wgs84"]
    if (not isinstance(coords, list) or len(coords) != 4 or
            any(isinstance(v, bool) or not isinstance(v, (float, int)) or
                not math.isfinite(v) for v in coords)):
        raise ValueError("bbox_wgs84 must contain four finite numbers")
    west, south, east, north = (float(v) for v in coords)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox_wgs84 bounds or axis order invalid")
    if east - west > 0.25 or north - south > 0.25:
        raise ValueError("bbox_wgs84 exceeds the bounded Territory A envelope")
    srid = raw["working_srid"]
    if type(srid) is not int or srid != 32637 or not CRS.from_epsg(srid).is_projected:
        raise ValueError("Territory A requires metric EPSG:32637")
    limit = raw["max_features_per_layer"]
    if type(limit) is not int or not 1 <= limit <= 100_000:
        raise ValueError("max_features_per_layer must be in [1, 100000]")
    return TerritorySpec(territory_id, url, (west, south, east, north), srid, limit)


def _tags(row: Mapping[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    packed = row.get("other_tags")
    if isinstance(packed, str) and packed:
        decoded = json.loads(packed)
        if not isinstance(decoded, dict):
            raise ValueError("OSM other_tags must be a JSON object")
        for k, v in decoded.items():
            if k in DEFAULT_RELEVANT_OSM_TAG_KEYS and isinstance(v, str) and v.strip():
                result[k] = v.strip()
    for key in sorted(DEFAULT_RELEVANT_OSM_TAG_KEYS):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return dict(sorted(result.items()))


def _categories(tags: Mapping[str, str], source_layer: str) -> tuple[OsmFeatureCategory, ...]:
    found: list[OsmFeatureCategory] = []
    if source_layer == "lines":
        if tags.get("highway"):
            found.append(OsmFeatureCategory.ROADS)
        if tags.get("waterway"):
            found.append(OsmFeatureCategory.WATER)
    elif source_layer == "multipolygons":
        if tags.get("building") or tags.get("building:part"):
            found.append(OsmFeatureCategory.BUILDINGS)
        if tags.get("landuse"):
            found.append(OsmFeatureCategory.LANDUSE)
        if (tags.get("water") or tags.get("wetland") or
                tags.get("natural") in {"water", "wetland", "bay"} or
                tags.get("landuse") in {"reservoir", "basin"}):
            found.append(OsmFeatureCategory.WATER)
    elif source_layer == "points" and any(
        tags.get(key) for key in ("amenity", "healthcare", "emergency", "public_transport")
    ):
        found.append(OsmFeatureCategory.POI)
    return tuple(found)


def _geometry_family(geometry: BaseGeometry, category: OsmFeatureCategory) -> BaseGeometry | None:
    if geometry.is_empty:
        return None
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if isinstance(geometry, GeometryCollection):
        parts = geometry.geoms
    else:
        parts = (geometry,)
    if category is OsmFeatureCategory.POI:
        points = [g for g in parts if isinstance(g, Point) and not g.is_empty]
        return points[0] if len(points) == 1 else None
    if category in (OsmFeatureCategory.ROADS, OsmFeatureCategory.WATER):
        lines = []
        for item in parts:
            if isinstance(item, LineString):
                lines.append(item)
            elif isinstance(item, MultiLineString):
                lines.extend(item.geoms)
        if lines:
            return lines[0] if len(lines) == 1 else MultiLineString(lines)
        if category is OsmFeatureCategory.ROADS:
            return None
    polygons = []
    for item in parts:
        if isinstance(item, Polygon):
            polygons.append(item)
        elif isinstance(item, MultiPolygon):
            polygons.extend(item.geoms)
    if polygons:
        return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    return None


def _osm_identity(row: Mapping[str, object], layer: str) -> tuple[OsmElementType, int]:
    raw_id = row.get("osm_id")
    if raw_id is None or isinstance(raw_id, bool):
        raise ValueError(f"OSM {layer} is missing a stable osm_id")
    try:
        parsed = int(str(raw_id))
    except ValueError as exc:
        raise ValueError(f"OSM {layer} contains an invalid osm_id") from exc
    if parsed == 0:
        raise ValueError(f"OSM {layer} contains zero osm_id")
    element_type = (
        OsmElementType.RELATION if layer == "multipolygons" and parsed < 0
        else OsmElementType.NODE if layer == "points" else OsmElementType.WAY
    )
    return element_type, abs(parsed)


def _collect(
    frame: gpd.GeoDataFrame,
    *,
    source_layer: str,
    bounds: BaseGeometry,
    output: dict[str, dict[str, dict[str, object]]],
    mapper: OsmTagMapper,
) -> None:
    if frame.crs is None or CRS.from_user_input(frame.crs) != CRS.from_epsg(4326):
        raise ValueError(f"OSM {source_layer} must declare EPSG:4326")
    if "osm_id" not in frame.columns:
        raise ValueError(f"OSM {source_layer} must provide stable osm_id")
    for row in frame.to_dict("records"):
        geometry = row.get(frame.geometry.name)
        if not isinstance(geometry, BaseGeometry) or geometry.is_empty:
            continue
        tags = _tags(row)
        categories = _categories(tags, source_layer)
        if not categories:
            continue
        element_type, osm_id = _osm_identity(row, source_layer)
        source_id = f"osm:{element_type.value}:{osm_id}"
        valid_geometry = geometry if geometry.is_valid else make_valid(geometry)
        clipped = valid_geometry.intersection(bounds)
        for category in categories:
            typed = _geometry_family(clipped, category)
            if typed is None or typed.is_empty:
                continue
            typed = set_precision(typed, grid_size=1e-7)
            if typed.is_empty or not typed.is_valid:
                continue
            feature = OsmFeature(
                source_feature_id=source_id, element_type=element_type, osm_id=osm_id,
                tags=MappingProxyType(tags), geometry=typed,
            )
            mapped = mapper.map_feature(category=category, feature=feature)
            layer_name = mapped.target_layer.value
            if source_id in output[layer_name]:
                raise ValueError(f"duplicate OSM source identity: {layer_name}/{source_id}")
            output[layer_name][source_id] = {
                "type": "Feature", "id": source_id,
                "properties": {
                    "source_feature_id": source_id, **dict(mapped.canonical_attributes),
                },
                "geometry": mapping(typed),
            }


def prepare(
    spec: TerritorySpec,
    *,
    source: Path,
    expected_sha256: str,
    output_dir: Path,
    read_layer: ReadOsmLayer = pyogrio.read_dataframe,
) -> dict[str, object]:
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise ValueError("--source-sha256 requires 64 lowercase hexadecimal characters")
    if not source.is_file() or source.suffix.lower() != ".pbf":
        raise ValueError("--source must identify an existing .osm.pbf file")
    actual_sha256 = _sha256(source)
    if actual_sha256 != expected_sha256:
        raise ValueError("source PBF sha256 mismatch; refusing to create a package")
    if output_dir.exists():
        raise FileExistsError("output directory already exists (immutable package)")
    mapper = OsmTagMapper()
    output: dict[str, dict[str, dict[str, object]]] = {layer: {} for layer in LAYERS}
    bounds = box(*spec.bbox)
    for source_layer in ("lines", "multipolygons", "points"):
        frame = read_layer(
            source, layer=source_layer, bbox=spec.bbox,
            max_features=spec.max_features_per_layer + 1,
            use_arrow=False, TAGS_FORMAT="JSON",
        )
        if len(frame) > spec.max_features_per_layer:
            raise ValueError(f"OSM {source_layer} exceeded feature budget")
        _collect(frame, source_layer=source_layer, bounds=bounds, output=output, mapper=mapper)
    if not output["roads"] or not output["buildings"]:
        raise ValueError("Territory A requires real roads and buildings inside bbox")
    manifest: dict[str, object] = {
        "schema_version": 1, "territory_id": spec.territory_id, "mode": "expansion",
        "source_url": spec.source_url, "source_sha256": actual_sha256,
        "bbox_wgs84": list(spec.bbox), "working_srid": spec.working_srid,
        "preprocessor_version": PACKAGE_VERSION, "osm_mapping_version": mapper.ruleset_version,
        "layers": {},
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        layer_details: dict[str, dict[str, object]] = {}
        for layer in LAYERS:
            if not output[layer]:
                continue
            contents = _json_bytes({
                "type": "FeatureCollection", "name": layer,
                "features": [output[layer][key] for key in sorted(output[layer])],
            })
            if len(contents) > MAX_OUTPUT_BYTES:
                raise ValueError(f"GeoJSON {layer} exceeded output size budget")
            filename = f"{layer}.geojson"
            (temp_dir / filename).write_bytes(contents)
            layer_details[layer] = {
                "path": filename, "dataset_kind": layer, "source_format": "geojson",
                "feature_count": len(output[layer]), "sha256": hashlib.sha256(contents).hexdigest(),
            }
        manifest["layers"] = layer_details
        (temp_dir / "package-manifest.json").write_bytes(_json_bytes(manifest))
        temp_dir.rename(output_dir)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=Path(__file__).resolve().parents[1] /
                        "data/experiments/territory_a_ryazan.json")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = read_spec(args.manifest)
    result = prepare(spec, source=args.source, expected_sha256=args.source_sha256,
                     output_dir=args.output)
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
