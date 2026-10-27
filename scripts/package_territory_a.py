"""Bounded, offline and SHA-pinned Territory A OSM preprocessing.

Only creates deterministic GeoJSON *input* files for the existing HTTP
upload -> DatasetVersion -> canonical ingest pipeline; never writes PostGIS.
The input digest pins the exact Geofabrik PBF (not a mutable -latest URL).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import pyogrio
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon
from shapely.geometry import Polygon, box, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from backend.app.services.osm_pbf_reader import (
    OsmFeatureCategory,
    OsmPbfReader,
    _classify_tags,
    _osm_identity,
)

SCHEMA = "territory-a-expansion-osm-v1"
GEOFABRIK_URL = "https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf"
LAYERS = ("lines", "multipolygons")
DATASET_KINDS = ("roads", "buildings", "landuse", "water")
KIND_CATEGORIES = {
    "roads": OsmFeatureCategory.ROADS,
    "buildings": OsmFeatureCategory.BUILDINGS,
    "landuse": OsmFeatureCategory.LANDUSE,
    "water": OsmFeatureCategory.WATER,
}
KIND_SOURCES = {
    "roads": ("lines",),
    "buildings": ("multipolygons",),
    "landuse": ("multipolygons",),
    "water": ("lines", "multipolygons"),
}
READ_CHUNK = 512


class TerritoryPackageError(ValueError):
    """A pinned source, spatial boundary or package invariant was violated."""


class TerritoryAManifest(BaseModel):
    """Conservative concrete Ryazan expansion reference-input contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["territory-a-expansion-osm-v1"]
    territory_id: Literal["ryazan-a"]
    mode: Literal["EXPANSION"]
    source_url: Literal[
        "https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf"
    ]
    source_sha256: str
    source_license: Literal["ODbL-1.0"]
    working_srid: Literal[32637]
    center_lon: float
    center_lat: float
    side_m: Literal[5000]
    max_source_features_per_layer: int = Field(ge=1, le=100_000)
    max_output_features_per_kind: int = Field(ge=1, le=100_000)

    @model_validator(mode="after")
    def validate_pinning(self) -> TerritoryAManifest:
        digest = self.source_sha256
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or set(digest) == {"0"}
        ):
            raise ValueError(
                "source_sha256 must be the real 64-character lowercase PBF digest "
                "(replace the manifest placeholder after downloading)"
            )
        if not (
            math.isfinite(self.center_lon)
            and math.isfinite(self.center_lat)
            and 39.5 <= self.center_lon <= 39.95
            and 54.5 <= self.center_lat <= 54.75
        ):
            raise ValueError("Territory A center must be inside the Ryazan reference bounds")
        return self


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    """Output is byte-stable for the same pinned source and toolchain."""
    return (
        json.dumps(
            value, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        ) + "\n"
    ).encode("utf-8")


def _square(manifest: TerritoryAManifest) -> tuple[Polygon, Polygon, tuple[float, ...]]:
    to_metric = Transformer.from_crs(4326, manifest.working_srid, always_xy=True)
    from_metric = Transformer.from_crs(manifest.working_srid, 4326, always_xy=True)
    center_x, center_y = to_metric.transform(manifest.center_lon, manifest.center_lat)
    half = manifest.side_m / 2
    metric = box(center_x - half, center_y - half, center_x + half, center_y + half)
    wgs84 = transform(from_metric.transform, metric)
    if not isinstance(wgs84, Polygon) or not wgs84.is_valid:
        raise TerritoryPackageError("unable to construct valid metric territory boundary")
    return metric, wgs84, wgs84.bounds


def _geometry_family(geometry: BaseGeometry, *, polygonal: bool) -> BaseGeometry | None:
    if geometry.is_empty:
        return None
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    accepted = (Polygon, MultiPolygon) if polygonal else (LineString, MultiLineString)
    if isinstance(geometry, accepted):
        return geometry if geometry.is_valid and not geometry.is_empty else None
    if isinstance(geometry, GeometryCollection):
        parts: list[BaseGeometry] = []
        for part in geometry.geoms:
            found = _geometry_family(part, polygonal=polygonal)
            if found is None:
                continue
            if isinstance(found, (MultiPolygon, MultiLineString)):
                parts.extend(found.geoms)
            else:
                parts.append(found)
        if parts:
            return MultiPolygon(parts) if polygonal else MultiLineString(parts)
    return None


def _rows(
    source: Path,
    *,
    layer: str,
    bbox: tuple[float, ...],
    max_features: int,
) -> list[dict[str, object]]:
    """Read no more than 512 features at once; bound total filtered features."""
    rows: list[dict[str, object]] = []
    offset = 0
    while True:
        kwargs: dict[str, Any] = {
            "layer": layer,
            "bbox": bbox,
            "skip_features": offset,
            "max_features": READ_CHUNK,
            "use_arrow": False,
        }
        if source.name.endswith(".osm.pbf"):
            kwargs["TAGS_FORMAT"] = "JSON"
        frame = pyogrio.read_dataframe(source, **kwargs)
        if not isinstance(frame, gpd.GeoDataFrame) or frame.crs is None:
            raise TerritoryPackageError(f"source OSM layer {layer} has no spatial CRS")
        if CRS.from_user_input(frame.crs) != CRS.from_epsg(4326):
            raise TerritoryPackageError(f"source OSM layer {layer} must use EPSG:4326")
        if len(frame) > READ_CHUNK:
            raise TerritoryPackageError(f"source OSM layer {layer} exceeded read chunk")
        if offset + len(frame) > max_features:
            raise TerritoryPackageError(
                f"source OSM layer {layer} exceeds max_source_features_per_layer"
            )
        columns = tuple(str(column) for column in frame.columns)
        geom_column = frame.geometry.name
        for record in frame.itertuples(index=False, name=None):
            item = dict(zip(columns, record, strict=True))
            item["geometry"] = item.pop(geom_column)
            rows.append(item)
        offset += len(frame)
        if len(frame) < READ_CHUNK:
            break
    return rows


def prepare_territory_a(
    *,
    manifest: TerritoryAManifest,
    source: Path,
    output_dir: Path,
    require_osm_pbf: bool = True,
) -> dict[str, object]:
    """Prepare one validated immutable package, refusing all partial overwrites.

    require_osm_pbf=False is a test-only seam for synthetic multi-layer GPKG
    exercising the same GDAL reader, geometry intersection and writer.
    """
    if require_osm_pbf and not source.name.endswith(".osm.pbf"):
        raise TerritoryPackageError("real source must be a local .osm.pbf")
    if not source.is_file() or not source.stat().st_size:
        raise TerritoryPackageError("source file is missing or empty")
    if output_dir.exists():
        raise TerritoryPackageError("output directory already exists; refusing overwrite")
    digest = sha256_file(source)
    if digest != manifest.source_sha256:
        raise TerritoryPackageError("source_sha256 mismatch; source bytes have drifted")
    metric_square, wgs84_square, wgs_bbox = _square(manifest)
    to_metric = Transformer.from_crs(4326, manifest.working_srid, always_xy=True)
    from_metric = Transformer.from_crs(manifest.working_srid, 4326, always_xy=True)

    layer_names = set(str(row[0]) for row in pyogrio.list_layers(source))
    if not set(LAYERS).issubset(layer_names):
        raise TerritoryPackageError("PBF needs lines and multipolygons OSM layers")

    output: dict[str, dict[str, dict[str, object]]] = {
        kind: {} for kind in DATASET_KINDS
    }
    raw_counts: dict[str, int] = {}
    reader = OsmPbfReader()
    for layer in LAYERS:
        source_rows = _rows(
            source, layer=layer, bbox=wgs_bbox,
            max_features=manifest.max_source_features_per_layer,
        )
        raw_counts[layer] = len(source_rows)
        for row in source_rows:
            geom = row.get("geometry")
            if not isinstance(geom, BaseGeometry) or geom.is_empty:
                continue
            tags = reader._extract_tags(row)
            categories = _classify_tags(tags)
            selected = (
                kind for kind in DATASET_KINDS
                if layer in KIND_SOURCES[kind] and KIND_CATEGORIES[kind] in categories
            )
            for kind in selected:
                clip_input = _geometry_family(
                    geom, polygonal=kind in {"buildings", "landuse"}
                    or (kind == "water" and layer == "multipolygons"),
                )
                if clip_input is None:
                    continue
                projected = transform(to_metric.transform, clip_input)
                clipped = _geometry_family(
                    projected.intersection(metric_square),
                    polygonal=kind in {"buildings", "landuse"}
                    or (kind == "water" and layer == "multipolygons"),
                )
                if clipped is None or not clipped.is_valid:
                    continue
                spatial = transform(from_metric.transform, clipped)
                # The exact same canonical OSM identity extraction is used by S03.
                element, osm_id = _osm_identity(row, layer=layer)
                feature_id = f"osm:{element.value}:{osm_id}"
                if feature_id in output[kind]:
                    raise TerritoryPackageError(f"duplicate OSM identity in {kind}")
                output[kind][feature_id] = {
                    "type": "Feature",
                    "id": feature_id,
                    "geometry": mapping(spatial),
                    "properties": {
                        "osm_element_type": element.value,
                        "osm_id": osm_id,
                        "source_feature_id": feature_id,
                        **tags,
                    },
                }
                if len(output[kind]) > manifest.max_output_features_per_kind:
                    raise TerritoryPackageError(
                        f"{kind} exceeds max_output_features_per_kind"
                    )

    if not output["roads"] or not output["buildings"]:
        raise TerritoryPackageError(
            "reference territory must contain real road and building features"
        )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".territory-a-", dir=output_dir.parent
    ) as scratch:
        staging = Path(scratch) / "package"
        staging.mkdir()
        artifacts: dict[str, dict[str, object]] = {}
        for kind in DATASET_KINDS:
            name = f"{kind}.geojson"
            collection = {
                "type": "FeatureCollection",
                "features": [
                    output[kind][key] for key in sorted(output[kind])
                ],
            }
            encoded = _json_bytes(collection)
            (staging / name).write_bytes(encoded)
            artifacts[kind] = {
                "path": name,
                "dataset_kind": kind,
                "source_format": "geojson",
                "feature_count": len(collection["features"]),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "size_bytes": len(encoded),
            }
        boundary_bytes = _json_bytes({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"territory_id": manifest.territory_id},
                "geometry": mapping(wgs84_square),
            }],
        })
        (staging / "boundary.geojson").write_bytes(boundary_bytes)
        report: dict[str, object] = {
            "schema_version": SCHEMA,
            "territory_id": manifest.territory_id,
            "mode": manifest.mode,
            "source": {
                "url": manifest.source_url,
                "sha256": digest,
                "license": manifest.source_license,
            },
            "metric_crs": f"EPSG:{manifest.working_srid}",
            "metric_area_m2": float(metric_square.area),
            "boundary_wgs84_bbox": list(wgs_bbox),
            "boundary_geojson_sha256": hashlib.sha256(boundary_bytes).hexdigest(),
            "source_rows_in_bbox": raw_counts,
            "derived_artifacts": artifacts,
            "preprocessor": SCHEMA,
            "note": (
                "Real OSM source extract; not a completed GIS generation run. "
                "Upload through the canonical application ingest service."
            ),
        }
        (staging / "package-report.json").write_bytes(_json_bytes(report))
        if output_dir.exists():
            raise TerritoryPackageError("output directory appeared during preparation")
        staging.rename(output_dir)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        spec = TerritoryAManifest.model_validate_json(
            args.manifest.read_text(encoding="utf-8")
        )
        if not args.source.is_file():
            raise TerritoryPackageError("source file does not exist")
        if not args.source.name.endswith(".osm.pbf"):
            raise TerritoryPackageError("input source must be .osm.pbf")
        if sha256_file(args.source) != spec.source_sha256:
            raise TerritoryPackageError("source SHA-256 mismatch")
        if args.verify_only:
            print(json.dumps({
                "verified": True, "source_sha256": spec.source_sha256,
                "territory_id": spec.territory_id,
            }, sort_keys=True))
            return
        report = prepare_territory_a(
            manifest=spec, source=args.source, output_dir=args.output_dir,
        )
    except (OSError, ValidationError, TerritoryPackageError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
