"""Synthetic GDAL integration for the SHA-locked Ryazan reference input package.

No live Geofabrik download, network call, PostgreSQL mutation or fake
real-world acceptance metrics are permitted in these tests.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import geopandas as gpd
import pyogrio
import pytest
from pydantic import ValidationError
from pyproj import Transformer
from shapely.geometry import LineString, Polygon, shape
from shapely.ops import transform

from scripts.package_territory_a import (
    GEOFABRIK_URL,
    SCHEMA,
    TerritoryAManifest,
    TerritoryPackageError,
    prepare_territory_a,
    sha256_file,
)


def _fixture_source(tmp_path: Path) -> Path:
    """Use the real OGR reader against a two-layer synthetic GeoPackage."""
    source = tmp_path / "input.gpkg"
    lines = gpd.GeoDataFrame(
        [
            {
                "osm_id": 101,
                "highway": "residential",
                "waterway": None,
                "geometry": LineString([(39.70, 54.62), (39.77, 54.62)]),
            },
            {
                "osm_id": 102,
                "highway": None,
                "waterway": "stream",
                "geometry": LineString([(39.72, 54.615), (39.75, 54.615)]),
            },
            {
                "osm_id": 103,
                "highway": "service",
                "waterway": None,
                "geometry": LineString([(39.85, 54.68), (39.88, 54.70)]),
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )
    polygons = gpd.GeoDataFrame(
        [
            {
                "osm_id": None,
                "osm_way_id": 201,
                "building": "yes",
                "landuse": None,
                "natural": None,
                "geometry": Polygon([
                    (39.739, 54.618), (39.746, 54.618),
                    (39.746, 54.624), (39.739, 54.624),
                ]),
            },
            {
                "osm_id": None,
                "osm_way_id": 202,
                "building": None,
                "landuse": "residential",
                "natural": None,
                "geometry": Polygon([
                    (39.735, 54.615), (39.742, 54.615),
                    (39.742, 54.622), (39.735, 54.622),
                ]),
            },
            {
                "osm_id": None,
                "osm_way_id": 203,
                "building": None,
                "landuse": None,
                "natural": "water",
                "geometry": Polygon([
                    (39.742, 54.619), (39.751, 54.619),
                    (39.751, 54.626), (39.742, 54.626),
                ]),
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )
    pyogrio.write_dataframe(lines, source, layer="lines", driver="GPKG")
    pyogrio.write_dataframe(
        polygons, source, layer="multipolygons", driver="GPKG", append=True
    )
    return source


def _manifest(source: Path, **overrides: object) -> TerritoryAManifest:
    data: dict[str, object] = {
        "schema_version": SCHEMA,
        "territory_id": "ryazan-a",
        "mode": "EXPANSION",
        "source_url": GEOFABRIK_URL,
        "source_sha256": sha256_file(source),
        "source_license": "ODbL-1.0",
        "working_srid": 32637,
        "center_lon": 39.74,
        "center_lat": 54.62,
        "side_m": 5000,
        "max_source_features_per_layer": 10,
        "max_output_features_per_kind": 10,
    }
    return TerritoryAManifest.model_validate({**data, **overrides})


def test_real_gdal_preprocess_is_byte_deterministic_and_clipped(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path)
    spec = _manifest(source)
    output1 = tmp_path / "first"
    output2 = tmp_path / "second"

    first = prepare_territory_a(
        manifest=spec, source=source, output_dir=output1, require_osm_pbf=False
    )
    second = prepare_territory_a(
        manifest=spec, source=source, output_dir=output2, require_osm_pbf=False
    )
    assert first == second
    assert first["metric_crs"] == "EPSG:32637"
    assert first["metric_area_m2"] == 25_000_000
    assert first["source"]["sha256"] == sha256_file(source)
    assert first["source_rows_in_bbox"] == {"lines": 2, "multipolygons": 3}

    expected = {
        "roads": "osm:way:101",
        "buildings": "osm:way:201",
        "landuse": "osm:way:202",
        "water": "osm:way:203",
    }
    to_metric = Transformer.from_crs(4326, 32637, always_xy=True)
    boundary = json.loads((output1 / "boundary.geojson").read_text())
    boundary_geom = shape(boundary["features"][0]["geometry"])
    metric_boundary = transform(to_metric.transform, boundary_geom)
    assert abs(metric_boundary.area - 25_000_000) < 0.5

    assert sorted(p.name for p in output1.iterdir()) == [
        "boundary.geojson",
        "buildings.geojson",
        "landuse.geojson",
        "package-report.json",
        "roads.geojson",
        "water.geojson",
    ]
    for kind, source_id in expected.items():
        collection = json.loads((output1 / f"{kind}.geojson").read_text())
        assert collection["type"] == "FeatureCollection"
        assert len(collection["features"]) == 1
        feature = collection["features"][0]
        assert feature["id"] == source_id
        assert feature["properties"]["source_feature_id"] == source_id
        geom = shape(feature["geometry"])
        assert geom.is_valid and not geom.is_empty
        assert geom.geom_type in (
            {"LineString", "MultiLineString"} if kind == "roads"
            else {"Polygon", "MultiPolygon"}
        )
        assert metric_boundary.buffer(0.001).covers(
            transform(to_metric.transform, geom)
        )
        record = first["derived_artifacts"][kind]
        assert record["feature_count"] == 1
        assert record["source_format"] == "geojson"
        assert record["sha256"] == sha256_file(output1 / f"{kind}.geojson")

    # Linear waterway stream in input is intentionally not mixed with
    # water polygons: canonical ingest requires a single geometry family.
    water = json.loads((output1 / "water.geojson").read_text())
    assert all(
        feature["geometry"]["type"] in ("Polygon", "MultiPolygon")
        for feature in water["features"]
    )
    for filename in (p.name for p in output1.iterdir()):
        assert (output1 / filename).read_bytes() == (output2 / filename).read_bytes()
    assert json.loads((output1 / "package-report.json").read_text()) == first


def test_checksum_mismatch_refuses_output_before_any_gdal_work(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path)
    manifest = _manifest(source, source_sha256="a" * 64)
    output = tmp_path / "refused"
    with pytest.raises(TerritoryPackageError, match="source_sha256 mismatch"):
        prepare_territory_a(
            manifest=manifest, source=source, output_dir=output,
            require_osm_pbf=False,
        )
    assert not output.exists()


def test_bad_source_path_and_output_overwrite_are_refused(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path)
    spec = _manifest(source)
    with pytest.raises(TerritoryPackageError, match="must be a local"):
        prepare_territory_a(manifest=spec, source=source, output_dir=tmp_path / "pbf")
    destination = tmp_path / "exists"
    destination.mkdir()
    (destination / "untouched").write_bytes(b"sentinel")
    with pytest.raises(TerritoryPackageError, match="already exists"):
        prepare_territory_a(
            manifest=spec, source=source, output_dir=destination, require_osm_pbf=False
        )
    assert (destination / "untouched").read_bytes() == b"sentinel"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_sha256", "REPLACE_ME"),
        ("source_sha256", "0" * 64),
        ("working_srid", 4326),
        ("mode", "FROM_SCRATCH"),
        ("side_m", 10000),
        ("center_lon", 0.0),
        ("max_output_features_per_kind", 0),
        ("max_source_features_per_layer", 200_000),
    ],
)
def test_manifest_rejects_unpinned_or_unsafe_inputs(
    tmp_path: Path, field: str, value: object
) -> None:
    source = _fixture_source(tmp_path)
    with pytest.raises(ValidationError):
        _manifest(source, **{field: value})


def test_feature_caps_are_fail_closed_not_truncated(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path)
    spec = _manifest(source, max_source_features_per_layer=1)
    output = tmp_path / "capped"
    with pytest.raises(TerritoryPackageError, match="exceeds max_source_features"):
        prepare_territory_a(
            manifest=spec, source=source, output_dir=output,
            require_osm_pbf=False,
        )
    assert not output.exists()


def test_rejects_missing_canonical_osm_layers(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path)
    # A new GeoPackage has the right CRS but not OSM's required layer names.
    replacement = tmp_path / "wrong.gpkg"
    pyogrio.write_dataframe(
        gpd.GeoDataFrame(
            [{"id": uuid.uuid4().hex, "geometry": LineString([(39.7, 54.6), (39.8, 54.7)])}],
            geometry="geometry", crs="EPSG:4326",
        ),
        replacement,
        layer="wrong",
        driver="GPKG",
    )
    spec = _manifest(replacement)
    out = tmp_path / "missing"
    with pytest.raises(TerritoryPackageError, match="lines and multipolygons"):
        prepare_territory_a(
            manifest=spec, source=replacement, output_dir=out,
            require_osm_pbf=False,
        )
    assert not out.exists()
