"""Offline, synthetic GDAL acceptance for the Tula-region FROM_SCRATCH packager."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pyogrio
import pytest
from pydantic import ValidationError
from pyproj import Transformer
from shapely.geometry import LineString, Polygon, shape
from shapely.ops import transform

from scripts.package_territory_a import sha256_file
from scripts.package_territory_b import (
    ARCHIVED_SOURCE_URL,
    SCHEMA,
    TerritoryBManifest,
    TerritoryBPackageError,
    main,
    prepare_territory_b,
)


def _source(tmp_path: Path, *, constraints: bool = True) -> Path:
    """Synthetic GPKG using exactly the two layers expected from GDAL OSM."""
    path = tmp_path / "source.gpkg"
    lines = gpd.GeoDataFrame(
        [
            {
                "osm_id": 101,
                "highway": "residential",
                "waterway": None,
                "geometry": LineString([(37.54, 54.25), (37.66, 54.25)]),
            },
            {
                "osm_id": 102,
                "highway": None,
                "waterway": "stream",
                "geometry": LineString([(37.57, 54.23), (37.64, 54.23)]),
            },
            {
                "osm_id": 103,
                "highway": "primary",
                "waterway": None,
                "geometry": LineString([(37.90, 54.42), (37.95, 54.43)]),
            },
        ],
        geometry="geometry",
        crs="EPSG:4326",
    )
    records = [{
        "osm_id": None,
        "osm_way_id": 201,
        "building": "yes",
        "landuse": None,
        "natural": None,
        "geometry": Polygon([
            (37.596, 54.246), (37.603, 54.246),
            (37.603, 54.253), (37.596, 54.253),
        ]),
    }]
    if constraints:
        records.extend([
            {
                "osm_id": None,
                "osm_way_id": 202,
                "building": None,
                "landuse": "grass",
                "natural": None,
                "geometry": Polygon([
                    (37.58, 54.235), (37.615, 54.235),
                    (37.615, 54.265), (37.58, 54.265),
                ]),
            },
            {
                "osm_id": None,
                "osm_way_id": 203,
                "building": None,
                "landuse": None,
                "natural": "water",
                "geometry": Polygon([
                    (37.635, 54.245), (37.652, 54.245),
                    (37.652, 54.259), (37.635, 54.259),
                ]),
            },
        ])
    multipolygons = gpd.GeoDataFrame(
        records, geometry="geometry", crs="EPSG:4326"
    )
    pyogrio.write_dataframe(lines, path, layer="lines", driver="GPKG")
    pyogrio.write_dataframe(
        multipolygons, path, layer="multipolygons", driver="GPKG", append=True
    )
    return path


def _manifest(source: Path, **changes: object) -> TerritoryBManifest:
    payload: dict[str, object] = {
        "schema_version": SCHEMA,
        "territory_id": "tula-b",
        "mode": "FROM_SCRATCH",
        "source_url": ARCHIVED_SOURCE_URL,
        "source_sha256": sha256_file(source),
        "source_license": "ODbL-1.0",
        "working_srid": 32637,
        "center_lon": 37.60,
        "center_lat": 54.25,
        "side_m": 10000,
        "max_source_features_per_layer": 10,
        "max_output_features_per_kind": 10,
    }
    return TerritoryBManifest.model_validate({**payload, **changes})


def test_b_is_100km2_repeatable_and_audits_excluded_existing_urban(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    manifest = _manifest(source)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first = prepare_territory_b(
        manifest=manifest, source=source, output_dir=first_dir,
        require_osm_pbf=False,
    )
    second = prepare_territory_b(
        manifest=manifest, source=source, output_dir=second_dir,
        require_osm_pbf=False,
    )
    assert first == second
    assert first["schema_version"] == SCHEMA
    assert first["mode"] == "FROM_SCRATCH"
    assert first["projected_area_m2"] == 100_000_000
    assert first["working_crs"] == "EPSG:32637"
    assert first["source"]["sha256"] == sha256_file(source)
    assert first["source_rows_in_bbox"] == {"lines": 2, "multipolygons": 3}
    assert first["observed_but_excluded_fixed_features"] == {
        "buildings": 1, "roads": 1
    }
    assert first["fixed_urban_layers_included"] == []
    assert first["ready_for_ingest_kinds"] == ["landuse", "water"]
    assert sorted(p.name for p in first_dir.iterdir()) == [
        "boundary.geojson",
        "landuse.geojson",
        "package-report.json",
        "water.geojson",
    ]
    assert not (first_dir / "roads.geojson").exists()
    assert not (first_dir / "buildings.geojson").exists()

    boundary = json.loads((first_dir / "boundary.geojson").read_text())
    metric = Transformer.from_crs(4326, 32637, always_xy=True)
    square = transform(metric.transform, shape(boundary["features"][0]["geometry"]))
    assert abs(square.area - 100_000_000) < 0.5

    for kind, feature_id in (("landuse", "osm:way:202"), ("water", "osm:way:203")):
        payload = json.loads((first_dir / f"{kind}.geojson").read_text())
        assert len(payload["features"]) == 1
        feature = payload["features"][0]
        assert feature["id"] == feature_id
        assert feature["properties"]["source_feature_id"] == feature_id
        assert feature["properties"]["osm_element_type"] == "way"
        geom = shape(feature["geometry"])
        assert geom.geom_type in {"Polygon", "MultiPolygon"} and geom.is_valid
        assert square.buffer(0.001).covers(transform(metric.transform, geom))
        artifact = first["derived_artifacts"][kind]
        assert artifact["feature_count"] == 1
        assert artifact["ingest_enabled"] is True
        assert artifact["sha256"] == sha256_file(first_dir / f"{kind}.geojson")

    for name in (p.name for p in first_dir.iterdir()):
        assert (first_dir / name).read_bytes() == (second_dir / name).read_bytes()
    assert json.loads((first_dir / "package-report.json").read_text()) == first


def test_source_must_match_sha_before_writing_any_output(tmp_path: Path) -> None:
    source = _source(tmp_path)
    manifest = _manifest(source, source_sha256="a" * 64)
    target = tmp_path / "bad"
    with pytest.raises(TerritoryBPackageError, match="source_sha256 mismatch"):
        prepare_territory_b(
            manifest=manifest, source=source, output_dir=target,
            require_osm_pbf=False,
        )
    assert not target.exists()


def test_pbf_extension_and_existing_target_are_fail_closed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    manifest = _manifest(source)
    with pytest.raises(TerritoryBPackageError, match="local .osm.pbf"):
        prepare_territory_b(
            manifest=manifest, source=source, output_dir=tmp_path / "invalid"
        )
    target = tmp_path / "existing"
    target.mkdir()
    sentinel = target / "preserve"
    sentinel.write_bytes(b"important")
    with pytest.raises(TerritoryBPackageError, match="refusing overwrite"):
        prepare_territory_b(
            manifest=manifest, source=source, output_dir=target,
            require_osm_pbf=False,
        )
    assert sentinel.read_bytes() == b"important"


def test_missing_constraint_features_refuses_misleading_empty_package(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path, constraints=False)
    with pytest.raises(TerritoryBPackageError, match="no real polygon constraints"):
        prepare_territory_b(
            manifest=_manifest(source), source=source,
            output_dir=tmp_path / "none", require_osm_pbf=False,
        )
    assert not (tmp_path / "none").exists()


def test_feature_budget_is_enforced_before_output(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "capped"
    with pytest.raises(TerritoryBPackageError, match="exceeds max_source_features"):
        prepare_territory_b(
            manifest=_manifest(source, max_source_features_per_layer=1),
            source=source, output_dir=target, require_osm_pbf=False,
        )
    assert not target.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_sha256", "REPLACE_ME"),
        ("source_sha256", "0" * 64),
        ("mode", "EXPANSION"),
        ("territory_id", "ryazan-a"),
        ("source_url", "https://example.com/latest.osm.pbf"),
        ("working_srid", 4326),
        ("side_m", 5000),
        ("center_lat", float("nan")),
        ("center_lon", 0.0),
        ("max_source_features_per_layer", 0),
        ("max_output_features_per_kind", 100_001),
    ],
)
def test_schema_refuses_unsafe_or_unpinned_inputs(
    tmp_path: Path, field: str, value: object
) -> None:
    source = _source(tmp_path)
    with pytest.raises(ValidationError):
        _manifest(source, **{field: value})


def test_verify_only_checks_bytes_without_database_or_gdal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gpkg = _source(tmp_path)
    source = tmp_path / "snapshot.osm.pbf"
    source.write_bytes(gpkg.read_bytes())
    manifest = _manifest(source)
    spec_path = tmp_path / "manifest.json"
    spec_path.write_text(manifest.model_dump_json(), encoding="utf-8")

    main([
        "--manifest", str(spec_path),
        "--source", str(source),
        "--output-dir", str(tmp_path / "not-created"),
        "--verify-only",
    ])
    assert json.loads(capsys.readouterr().out) == {
        "verified": True,
        "source_sha256": sha256_file(source),
        "territory_id": "tula-b",
    }
    assert not (tmp_path / "not-created").exists()


def test_no_pbf_layer_layout_is_rejected(tmp_path: Path) -> None:
    wrong = tmp_path / "wrong.gpkg"
    pyogrio.write_dataframe(
        gpd.GeoDataFrame(
            [{"geometry": LineString([(37.58, 54.24), (37.62, 54.26)])}],
            geometry="geometry", crs="EPSG:4326",
        ),
        wrong, layer="unrelated", driver="GPKG",
    )
    with pytest.raises(TerritoryBPackageError, match="lines and multipolygons"):
        prepare_territory_b(
            manifest=_manifest(wrong), source=wrong,
            output_dir=tmp_path / "failed", require_osm_pbf=False,
        )
