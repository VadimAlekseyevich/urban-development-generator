from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

from scripts.territory_a_package import prepare, read_spec


@pytest.fixture
def spec(tmp_path: Path) -> object:
    raw = {
        "schema_version": 1, "territory_id": "ryazan-a-expansion-v1",
        "mode": "expansion", "source_url":
        "https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf",
        "bbox_wgs84": [39.64, 54.60, 39.76, 54.69],
        "working_srid": 32637, "max_features_per_layer": 10,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return read_spec(path)


def frames(reverse: bool = False) -> dict[str, gpd.GeoDataFrame]:
    lines = gpd.GeoDataFrame([
        {"osm_id": "42", "highway": "residential", "name": "Ryazan Test Road",
         "geometry": LineString([(39.63, 54.63), (39.74, 54.63)])},
        {"osm_id": "43", "highway": "secondary",
         "geometry": LineString([(39.69, 54.62), (39.70, 54.68)])},
    ], geometry="geometry", crs="EPSG:4326")
    buildings = gpd.GeoDataFrame([
        {"osm_id": "55", "osm_way_id": None, "building": "yes",
         "geometry": Polygon([(39.70, 54.64), (39.71, 54.64),
                              (39.71, 54.65), (39.70, 54.65)])},
    ], geometry="geometry", crs="EPSG:4326")
    pois = gpd.GeoDataFrame([
        {"osm_id": "88", "amenity": "school", "geometry": Point(39.71, 54.66)},
    ], geometry="geometry", crs="EPSG:4326")
    return {"lines": lines.iloc[::-1].copy() if reverse else lines,
            "multipolygons": buildings, "points": pois}


def _reader(frames_by_layer: dict[str, gpd.GeoDataFrame]):
    def read(_path: Path, **kwargs: object) -> gpd.GeoDataFrame:
        assert kwargs["bbox"] == (39.64, 54.60, 39.76, 54.69)
        assert kwargs["max_features"] == 11
        return frames_by_layer[str(kwargs["layer"])]
    return read


def _source(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "original.osm.pbf"
    source.write_bytes(b"pinned real extract placeholder for mocked read")
    return source, hashlib.sha256(source.read_bytes()).hexdigest()


def test_manifest_rejects_unbounded_or_nonmetric(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    basic = {
        "schema_version": 1, "territory_id": "a", "mode": "expansion",
        "source_url": "https://download.geofabrik.de/test.osm.pbf",
        "bbox_wgs84": [39.64, 54.60, 39.76, 54.69],
        "working_srid": 32637, "max_features_per_layer": 10,
    }
    for field, bad in [("bbox_wgs84", [39.6, 54.6, 40.0, 54.7]),
                       ("working_srid", 4326), ("max_features_per_layer", 0)]:
        path.write_text(json.dumps({**basic, field: bad}), encoding="utf-8")
        with pytest.raises(ValueError):
            read_spec(path)


def test_package_is_permutation_stable_and_clips_and_maps(spec: object, tmp_path: Path) -> None:
    source, digest = _source(tmp_path)
    output_a = tmp_path / "a"
    output_b = tmp_path / "b"
    result_a = prepare(spec, source=source, expected_sha256=digest, output_dir=output_a,
                       read_layer=_reader(frames()))
    result_b = prepare(spec, source=source, expected_sha256=digest, output_dir=output_b,
                       read_layer=_reader(frames(reverse=True)))
    assert result_a == result_b
    assert sorted(x.name for x in output_a.iterdir()) == sorted(x.name for x in output_b.iterdir())
    for filename in (p.name for p in output_a.iterdir()):
        assert (output_a / filename).read_bytes() == (output_b / filename).read_bytes()
    assert result_a["layers"]["roads"]["feature_count"] == 2
    road = json.loads((output_a / "roads.geojson").read_text(encoding="utf-8"))["features"][0]
    assert road["properties"]["source_feature_id"] == "osm:way:42"
    assert road["properties"]["road_class"] == "local"
    assert min(x[0] for x in road["geometry"]["coordinates"]) >= 39.64
    assert result_a["layers"]["facilities"]["feature_count"] == 1


def test_rejects_checksum_mismatch_without_output(spec: object, tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="sha256 mismatch"):
        prepare(spec, source=source, expected_sha256="0" * 64, output_dir=out,
                read_layer=_reader(frames()))
    assert not out.exists()


def test_refuses_duplicate_identity_and_truncated_reads(spec: object, tmp_path: Path) -> None:
    source, digest = _source(tmp_path)
    duplicated = frames()
    duplicated["lines"] = gpd.GeoDataFrame(
        [duplicated["lines"].iloc[0].to_dict()] * 2, geometry="geometry", crs="EPSG:4326"
    )
    with pytest.raises(ValueError, match="duplicate OSM source identity"):
        prepare(spec, source=source, expected_sha256=digest, output_dir=tmp_path / "dup",
                read_layer=_reader(duplicated))
    assert not (tmp_path / "dup").exists()
    excessive = frames()
    excessive["lines"] = gpd.GeoDataFrame(
        [excessive["lines"].iloc[0].to_dict()] * 11, geometry="geometry", crs="EPSG:4326"
    )
    with pytest.raises(ValueError, match="feature budget"):
        prepare(spec, source=source, expected_sha256=digest, output_dir=tmp_path / "many",
                read_layer=_reader(excessive))
    assert not (tmp_path / "many").exists()


def test_source_crs_and_empty_required_layer_fail_closed(spec: object, tmp_path: Path) -> None:
    source, digest = _source(tmp_path)
    invalid = frames()
    invalid["lines"] = invalid["lines"].to_crs(3857)
    with pytest.raises(ValueError, match="EPSG:4326"):
        prepare(spec, source=source, expected_sha256=digest, output_dir=tmp_path / "crs",
                read_layer=_reader(invalid))
    empty = frames()
    empty["multipolygons"] = empty["multipolygons"].iloc[0:0]
    with pytest.raises(ValueError, match="requires real roads and buildings"):
        prepare(spec, source=source, expected_sha256=digest, output_dir=tmp_path / "empty",
                read_layer=_reader(empty))
def test_multipolygon_way_identity_and_immutable_output(spec: object, tmp_path: Path) -> None:
    source, digest = _source(tmp_path)
    from_way = frames()
    from_way["multipolygons"].loc[0, "osm_id"] = None
    from_way["multipolygons"].loc[0, "osm_way_id"] = "77"
    out = tmp_path / "way"
    prepare(spec, source=source, expected_sha256=digest, output_dir=out,
            read_layer=_reader(from_way))
    building = json.loads((out / "buildings.geojson").read_text(encoding="utf-8"))["features"][0]
    assert building["id"] == "osm:way:77"
    with pytest.raises(FileExistsError, match="already exists"):
        prepare(spec, source=source, expected_sha256=digest, output_dir=out,
                read_layer=_reader(from_way))


def test_ambiguous_multipolygon_identity_rejected(spec: object, tmp_path: Path) -> None:
    source, digest = _source(tmp_path)
    ambiguous = frames()
    ambiguous["multipolygons"].loc[0, "osm_way_id"] = "77"
    with pytest.raises(ValueError, match="exactly one way or relation"):
        prepare(spec, source=source, expected_sha256=digest, output_dir=tmp_path / "bad",
                read_layer=_reader(ambiguous))
