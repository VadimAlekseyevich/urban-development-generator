from __future__ import annotations

import argparse
import json
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from types import MappingProxyType

import pyogrio
from geoalchemy2.shape import from_shape
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from backend.app.db.session import SessionLocal
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.project import Project
from backend.app.services.osm_canonical_writer import OsmCanonicalWriter
from backend.app.services.osm_mapping import MappedOsmBatch, OsmMappedLayer, OsmTagMapper
from backend.app.services.osm_pbf_reader import (
    OsmElementType,
    OsmFeature,
    OsmFeatureBatch,
    OsmFeatureCategory,
)
from core.urban_generator.domain import WorkingCRS

GEOFABRIK_URL = (
    "https://download.geofabrik.de/russia/"
    "central-fed-district-latest.osm.pbf"
)
RYAZAN_BBOX = (39.50, 54.50, 39.95, 54.75)
TAG_COLUMNS = (
    "access",
    "aeroway",
    "amenity",
    "barrier",
    "bridge",
    "building",
    "building:levels",
    "building:part",
    "capacity",
    "craft",
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
    "service",
    "shop",
    "sport",
    "surface",
    "tourism",
    "tunnel",
    "type",
    "water",
    "waterway",
    "wetland",
    "width",
)


def _download_if_needed(target: Path) -> None:
    if target.is_file() and target.stat().st_size > 10_000_000:
        print(f"Using cached source: {target}")
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".part")
    print("Downloading Geofabrik Central Federal District PBF...")
    print(GEOFABRIK_URL)

    def report(blocks: int, block_size: int, total: int) -> None:
        downloaded = blocks * block_size
        if total > 0:
            pct = min(downloaded / total * 100.0, 100.0)
            print(
                f"\r  {downloaded / 1024 / 1024:7.1f} / "
                f"{total / 1024 / 1024:7.1f} MiB ({pct:5.1f}%)",
                end="",
                flush=True,
            )
        else:
            print(
                f"\r  {downloaded / 1024 / 1024:7.1f} MiB",
                end="",
                flush=True,
            )

    try:
        urllib.request.urlretrieve(GEOFABRIK_URL, temp, reporthook=report)
        print()
        temp.replace(target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise

    print(f"Saved raw PBF to: {target}")


def _json_tags(value: object) -> dict[str, str]:
    if value is None or not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in decoded.items()
        if item is not None and str(item).strip()
    }


def _tags_from_row(row: dict[str, object]) -> dict[str, str]:
    tags = _json_tags(row.get("other_tags"))
    for key in TAG_COLUMNS:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() != "nan":
            tags[key] = text
    return dict(sorted(tags.items()))


def _polygonal(geometry: BaseGeometry) -> BaseGeometry | None:
    candidate = geometry if geometry.is_valid else make_valid(geometry)
    if candidate.is_empty:
        return None
    if isinstance(candidate, (Polygon, MultiPolygon)):
        return candidate
    if candidate.geom_type == "GeometryCollection":
        polygons: list[Polygon] = []
        for part in candidate.geoms:
            if isinstance(part, Polygon):
                polygons.append(part)
            elif isinstance(part, MultiPolygon):
                polygons.extend(part.geoms)
        if polygons:
            return MultiPolygon(polygons)
    return None


def _linear(geometry: BaseGeometry) -> BaseGeometry | None:
    candidate = geometry if geometry.is_valid else make_valid(geometry)
    if candidate.is_empty:
        return None
    if isinstance(candidate, (LineString, MultiLineString)):
        return candidate
    return None


def _osm_id(row: dict[str, object], *, fallback: int) -> int:
    for key in ("osm_id", "osm_way_id"):
        value = row.get(key)
        if value is None:
            continue
        try:
            return abs(int(str(value)))
        except ValueError:
            continue
    return fallback


def _read_osm_layer(source: Path, *, layer: str) -> list[dict[str, object]]:
    print(f"Reading OSM layer {layer!r} inside Ryazan bbox...")
    frame = pyogrio.read_dataframe(
        source,
        layer=layer,
        bbox=RYAZAN_BBOX,
        use_arrow=False,
        TAGS_FORMAT="JSON",
    )
    geometry_column = frame.geometry.name
    rows: list[dict[str, object]] = []
    for values in frame.itertuples(index=False, name=None):
        row = dict(zip((str(column) for column in frame.columns), values, strict=True))
        row["geometry"] = row.pop(geometry_column)
        rows.append(row)
    print(f"  read {len(rows)} raw features")
    return rows


def _feature_batch(
    rows: Iterable[dict[str, object]],
    *,
    category: OsmFeatureCategory,
    source_layer: str,
) -> OsmFeatureBatch:
    features: list[OsmFeature] = []
    seen: set[str] = set()

    for position, row in enumerate(rows):
        geometry = row.get("geometry")
        if not isinstance(geometry, BaseGeometry) or geometry.is_empty:
            continue

        tags = _tags_from_row(row)
        if category is OsmFeatureCategory.ROADS:
            if not tags.get("highway"):
                continue
            geometry = _linear(geometry)
            element_type = OsmElementType.WAY
        elif category is OsmFeatureCategory.BUILDINGS:
            if not (tags.get("building") or tags.get("building:part")):
                continue
            geometry = _polygonal(geometry)
            element_type = OsmElementType.RELATION
        elif category is OsmFeatureCategory.LANDUSE:
            if not tags.get("landuse"):
                continue
            geometry = _polygonal(geometry)
            element_type = OsmElementType.RELATION
        elif category is OsmFeatureCategory.WATER:
            is_water = bool(
                tags.get("waterway")
                or tags.get("water")
                or tags.get("wetland")
                or tags.get("natural")
                in {"water", "wetland", "bay", "strait", "coastline"}
                or tags.get("landuse") in {"reservoir", "basin"}
            )
            if not is_water:
                continue
            if source_layer == "lines":
                geometry = _linear(geometry)
                element_type = OsmElementType.WAY
            else:
                geometry = _polygonal(geometry)
                element_type = OsmElementType.RELATION
        else:
            continue

        if geometry is None or geometry.is_empty or not geometry.is_valid:
            continue

        osm_id = _osm_id(row, fallback=position + 1)
        source_feature_id = f"osm:{source_layer}:{category.value}:{osm_id}:{position}"
        if source_feature_id in seen:
            continue
        seen.add(source_feature_id)
        features.append(
            OsmFeature(
                source_feature_id=source_feature_id,
                element_type=element_type,
                osm_id=osm_id,
                tags=MappingProxyType(tags),
                geometry=geometry,
            )
        )

    return OsmFeatureBatch(
        category=category,
        source_layer=source_layer,
        start_feature=0,
        features=tuple(features),
        crs="EPSG:4326",
    )


def _merge_mapped_batches(
    batches: Iterable[MappedOsmBatch],
    *,
    target_layer: OsmMappedLayer,
    mapping_version: str,
) -> MappedOsmBatch:
    features = []
    source_names = []
    for batch in batches:
        if batch.target_layer is not target_layer:
            raise RuntimeError("target layer mismatch while merging")
        features.extend(batch.features)
        source_names.append(batch.source_layer)

    source_category = {
        OsmMappedLayer.ROADS: OsmFeatureCategory.ROADS,
        OsmMappedLayer.BUILDINGS: OsmFeatureCategory.BUILDINGS,
        OsmMappedLayer.LANDUSE: OsmFeatureCategory.LANDUSE,
        OsmMappedLayer.WATER: OsmFeatureCategory.WATER,
    }[target_layer]
    return MappedOsmBatch(
        source_category=source_category,
        target_layer=target_layer,
        source_layer="+".join(source_names),
        start_feature=0,
        features=tuple(features),
        mapping_version=mapping_version,
        crs="EPSG:4326",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and import real OpenStreetMap data for Ryazan."
    )
    parser.add_argument(
        "--pbf",
        default="/app/storage/imports/central-fed-district-latest.osm.pbf",
    )
    parser.add_argument("--project-name", default="Ryazan — real OSM")
    parser.add_argument(
        "--working-srid",
        type=int,
        default=32637,
        help="Metric CRS for Ryazan; default WGS84 / UTM zone 37N.",
    )
    args = parser.parse_args()

    source = Path(args.pbf)
    _download_if_needed(source)
    lines = _read_osm_layer(source, layer="lines")
    multipolygons = _read_osm_layer(source, layer="multipolygons")

    mapper = OsmTagMapper()
    writer = OsmCanonicalWriter()
    working_crs = WorkingCRS(args.working_srid)

    raw_batches = {
        OsmMappedLayer.ROADS: [
            _feature_batch(
                lines,
                category=OsmFeatureCategory.ROADS,
                source_layer="lines",
            )
        ],
        OsmMappedLayer.BUILDINGS: [
            _feature_batch(
                multipolygons,
                category=OsmFeatureCategory.BUILDINGS,
                source_layer="multipolygons",
            )
        ],
        OsmMappedLayer.LANDUSE: [
            _feature_batch(
                multipolygons,
                category=OsmFeatureCategory.LANDUSE,
                source_layer="multipolygons",
            )
        ],
        OsmMappedLayer.WATER: [
            _feature_batch(
                lines,
                category=OsmFeatureCategory.WATER,
                source_layer="lines",
            ),
            _feature_batch(
                multipolygons,
                category=OsmFeatureCategory.WATER,
                source_layer="multipolygons",
            ),
        ],
    }

    mapped: dict[OsmMappedLayer, MappedOsmBatch] = {}
    for layer, batches in raw_batches.items():
        mapped_batches = [
            mapper.map_batch(batch)
            for batch in batches
            if batch.features
        ]
        mapped[layer] = _merge_mapped_batches(
            mapped_batches,
            target_layer=layer,
            mapping_version=mapper.ruleset_version,
        )
        print(f"{layer.value}: {len(mapped[layer].features)} mapped features")

    with SessionLocal() as session:
        with session.begin():
            project = Project(
                name=args.project_name,
                description=(
                    "Real OpenStreetMap data for Ryazan, imported from "
                    "Geofabrik Central Federal District extract."
                ),
                working_srid=args.working_srid,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()

            dataset = Dataset(project_id=project.id, kind="osm")
            session.add(dataset)
            session.flush()

            version = DatasetVersion(
                dataset_id=dataset.id,
                version=1,
                status="processing",
                checksum_sha256=None,
                source_metadata={
                    "format": "osm_pbf",
                    "source": GEOFABRIK_URL,
                    "bbox": list(RYAZAN_BBOX),
                    "mapping_version": mapper.ruleset_version,
                    "manual_real_data_import": True,
                },
            )
            session.add(version)
            session.flush()
            project_id = project.id
            dataset_version_id = version.id

    for layer in (
        OsmMappedLayer.ROADS,
        OsmMappedLayer.BUILDINGS,
        OsmMappedLayer.LANDUSE,
        OsmMappedLayer.WATER,
    ):
        result = writer.replace_layer(
            dataset_version_id=dataset_version_id,
            target_layer=layer,
            working_crs=working_crs,
            mapping_version=mapper.ruleset_version,
            batches=(mapped[layer],),
        )
        print(f"saved {layer.value}: {result.persistence.inserted_rows} rows")

    west, south, east, north = RYAZAN_BBOX
    wgs84_boundary = box(west, south, east, north)
    transformer = Transformer.from_crs(4326, args.working_srid, always_xy=True)
    projected_boundary = shapely_transform(transformer.transform, wgs84_boundary)
    boundary_multi = MultiPolygon([projected_boundary])

    with SessionLocal() as session:
        with session.begin():
            project = session.get(Project, project_id)
            version = session.get(DatasetVersion, dataset_version_id)
            if project is None or version is None:
                raise RuntimeError("created project/dataset version disappeared")

            project.boundary = from_shape(
                boundary_multi,
                srid=args.working_srid,
                extended=True,
            )
            project.boundary_metadata = {
                "source_srid": 4326,
                "geometry_type": "MULTIPOLYGON",
                "feature_count": 1,
                "source": "Ryazan import bbox used only for map fit",
            }
            version.status = "ready"

    url = (
        "http://localhost:5173/"
        f"?project_id={project_id}"
        f"&dataset_version_id={dataset_version_id}"
    )
    print()
    print("IMPORT COMPLETE")
    print(f"PROJECT_ID={project_id}")
    print(f"DATASET_VERSION_ID={dataset_version_id}")
    print(f"OPEN={url}")
    print()
    print(
        "Note: the project boundary is the import bbox, not the official "
        "administrative boundary. Roads/buildings/landuse/water are real OSM data."
    )


if __name__ == "__main__":
    main()
