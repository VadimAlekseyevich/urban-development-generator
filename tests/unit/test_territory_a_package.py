"""Territory A reproducible package: actual GDAL layers, metric clipping, pinned bytes.

The OSM-like GeoPackage is *synthetic*. Never report it as real Ryazan data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import geopandas as gpd
import pyogrio
import pytest
from pydantic import ValidationError
from pyproj import Transformer
from shapely.geometry import LineString, Polygon, shape
from shapely.ops import transform

from scripts.package_territory_a import (
    SCHEMA,
    TerritoryAManifest,
    TerritoryPackageError,
    _geometry_family,
    prepare_territory_a,
    sha256_file,
)


def _gpkg(tmp_path: Path) -> Path:
    """Create two real OGR layers with OSM-like fields around the Ryazan AOI."""
    to_metric = Transformer.from_crs(4326, 32637, always_xy=True)
    to_wgs = Transformer.from_crs(32637, 4326, always_xy=True)
    x, y = to_metric.transform(39.74, 54.62)

    def wgs(geometry: LineString | Polygon) -> LineString | Polygon:
        return transform(to_wgs.transform, geometry)

    roads = gpd.GeoDataFrame(
        [
            {
                "osm_id": "101", "highway": "residential", "name": "realistic synthetic",
                "geometry": wgs(LineString([(x - 5000, y), (x + 200, y)])),
            },
            {
                "osm_id": "102", "waterway": "stream", "name": "fictional stream",
                "geometry": wgs(LineString([(x - 1000, y + 500), (x + 3500, y + 500)])),
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )
    buildings = gpd.GeoDataFrame(
        [
            {
                "osm_way_id": "201", "building": "yes",
                "geometry": wgs(Polygon([
                    (x + 100, y + 100), (x + 200, y + 100),
                    (x + 200, y + 200), (x + 100, y + 200),
                ])),
            },
            {
                "osm_way_id": "202", "landuse": "residential",
                "geometry": wgs(Polygon([
                    (x - 900, y - 900), (x - 700, y - 900),
                    (x - 700, y - 700), (x - 900, y - 700),
                ])),
            },
            {
                "osm_way_id": "203", "natural": "water",
                "geometry": wgs(Polygon([
                    (x - 200, y - 200), (x - 100, y - 200),
                    (x - 100, y - 100), (x - 200, y - 100),
                ])),
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )
    source = tmp_path / "fixture.gpkg"
    pyogrio.write_dataframe(roads, source, driver="GPKG", layer="lines")
    pyogrio.write_dataframe(buildings, source, driver="GPKG", layer="multipolygons")
    return source


def _manifest(source: Path, **updates: object) -> TerritoryAManifest:
    values: dict[str, object] = {
        "schema_version": SCHEMA, "territory_id": "ryazan-a", "mode": "EXPANSION",
        "source_url": (
            "https://download.geofabrik.de/russia/"
            "central-fed-district-latest.osm.pbf"
        ),
        "source_sha256": sha256_file(source),
        "source_license": "ODbL-1.0", "working_srid": 32637,
        "center_lon": 39.74, "center_lat": 54.62, "side_m": 5000,
        "max_source_features_per_layer": 30, "max_output_features_per_kind": 30,
    }
    values.update(updates)
    return TerritoryAManifest.model_validate(values)


def test_real_gdal_read_clip_and_two_identical_bytewise_packages(tmp_path: Path) -> None:
    source = _gpkg(tmp_path)
    manifest = _manifest(source)
    left, right = tmp_path / "first", tmp_path / "second"

    first = prepare_territory_a(
        manifest=manifest, source=source, output_dir=left, require_osm_pbf=False,
    )
    second = prepare_territory_a(
        manifest=manifest, source=source, output_dir=right, require_osm_pbf=False,
    )
    assert first == second
    assert first["metric_area_m2"] == 25_000_000
    assert first["source_rows_in_bbox"] == {"lines": 2, "multipolygons": 3}
    assert {
        kind: info["feature_count"]
        for kind, info in first["derived_artifacts"].items()
    } == {"roads": 1, "buildings": 1, "landuse": 1, "water": 2}
    for name in (
        "roads.geojson", "buildings.geojson", "landuse.geojson",
        "water.geojson", "boundary.geojson", "package-report.json",
    ):
        assert (left / name).read_bytes() == (right / name).read_bytes()
    for info in first["derived_artifacts"].values():
        assert hashlib.sha256((left / info["path"]).read_bytes()).hexdigest() == (
            info["sha256"]
        )
    roads = json.loads((left / "roads.geojson").read_text())
    assert roads["features"][0]["id"] == "osm:way:101"
    assert roads["features"][0]["properties"]["highway"] == "residential"
    geom = shape(roads["features"][0]["geometry"])
    to_metric = Transformer.from_crs(4326, 32637, always_xy=True)
    projected = transform(to_metric.transform, geom)
    # Original synthetic road crosses AOI: exact metric clipping leaves <= 5 km.
    assert projected.length < 5000
    assert projected.length > 2500
    assert projected.is_valid
    boundary = json.loads((left / "boundary.geojson").read_text())
    metric_boundary = transform(
        to_metric.transform, shape(boundary["features"][0]["geometry"])
    )
    assert metric_boundary.area == pytest.approx(25_000_000, rel=1e-9)


def test_fail_closed_on_wrong_digest_existing_output_and_wrong_source_type(
    tmp_path: Path,
) -> None:
    source = _gpkg(tmp_path)
    target = tmp_path / "output"
    with pytest.raises(TerritoryPackageError, match="source_sha256 mismatch"):
        prepare_territory_a(
            manifest=_manifest(source, source_sha256="1" * 64),
            source=source, output_dir=target, require_osm_pbf=False,
        )
    assert not target.exists()
    with pytest.raises(TerritoryPackageError, match="osm.pbf"):
        prepare_territory_a(manifest=_manifest(source), source=source, output_dir=target)
    assert not target.exists()
    target.mkdir()
    (target / "retain.txt").write_text("never overwrite", encoding="utf-8")
    with pytest.raises(TerritoryPackageError, match="already exists"):
        prepare_territory_a(
            manifest=_manifest(source), source=source,
            output_dir=target, require_osm_pbf=False,
        )
    assert (target / "retain.txt").read_text(encoding="utf-8") == "never overwrite"


@pytest.mark.parametrize("value", ["0" * 64, "REPLACE_ME", "ABC" * 21 + "F"])
def test_manifest_rejects_unpinned_or_wrong_hash(value: str, tmp_path: Path) -> None:
    source = _gpkg(tmp_path)
    with pytest.raises(ValidationError, match="source_sha256"):
        _manifest(source, source_sha256=value)


def test_strict_limits_refuse_partial_package(tmp_path: Path) -> None:
    source = _gpkg(tmp_path)
    target = tmp_path / "partial"
    with pytest.raises(TerritoryPackageError, match="max_source_features_per_layer"):
        prepare_territory_a(
            manifest=_manifest(source, max_source_features_per_layer=1),
            source=source, output_dir=target, require_osm_pbf=False,
        )
    assert not target.exists()
    with pytest.raises(TerritoryPackageError, match="max_output_features_per_kind"):
        prepare_territory_a(
            manifest=_manifest(source, max_output_features_per_kind=1),
            source=source, output_dir=target, require_osm_pbf=False,
        )
    assert not target.exists()


def test_geometry_collection_keeps_only_correct_family() -> None:
    from shapely.geometry import GeometryCollection, Point

    mixed = GeometryCollection([Point(1, 2), LineString([(0, 0), (1, 0)])])
    assert _geometry_family(mixed, polygonal=True) is None
    linear = _geometry_family(mixed, polygonal=False)
    assert linear is not None and linear.length == 1
