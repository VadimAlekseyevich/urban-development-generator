import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from backend.app.services.vector_inspection import (
    IncompleteVectorMetadataError,
    VectorInspectionError,
    VectorInspector,
    VectorLayerLimitError,
    VectorMetadataBackend,
    VectorSource,
)


class FakeMetadataBackend(VectorMetadataBackend):
    def __init__(
        self,
        *,
        layers: tuple[tuple[str, str | None], ...],
        responses: dict[str, list[Mapping[str, object]]],
    ) -> None:
        self.layers = layers
        self.responses = {name: list(items) for name, items in responses.items()}
        self.calls: list[tuple[str, bool, bool]] = []

    def list_layers(self, source: VectorSource) -> tuple[tuple[str, str | None], ...]:
        del source
        return self.layers

    def read_info(
        self,
        source: VectorSource,
        *,
        layer: str,
        force_feature_count: bool,
        force_total_bounds: bool,
    ) -> Mapping[str, object]:
        del source
        self.calls.append((layer, force_feature_count, force_total_bounds))
        responses = self.responses[layer]
        if len(responses) > 1:
            return responses.pop(0)
        return responses[0]


def _info(
    *,
    name: str,
    geometry_type: str | None,
    features: int,
    total_bounds: tuple[float, float, float, float] | None,
    driver: str = "GPKG",
    crs: str | None = "EPSG:32637",
) -> Mapping[str, object]:
    return {
        "layer_name": name,
        "driver": driver,
        "geometry_type": geometry_type,
        "crs": crs,
        "features": features,
        "total_bounds": total_bounds,
        "encoding": "UTF-8",
    }


def test_inspector_uses_driver_metadata_without_forcing() -> None:
    backend = FakeMetadataBackend(
        layers=(("roads", "LineString"), ("buildings", "Polygon")),
        responses={
            "roads": [
                _info(
                    name="roads",
                    geometry_type="LineString",
                    features=3,
                    total_bounds=(10.0, 20.0, 30.0, 40.0),
                )
            ],
            "buildings": [
                _info(
                    name="buildings",
                    geometry_type="Polygon",
                    features=2,
                    total_bounds=(12.0, 22.0, 28.0, 38.0),
                )
            ],
        },
    )

    inspection = VectorInspector(backend=backend).inspect(Path("demo.gpkg"))

    assert inspection.driver == "GPKG"
    assert [layer.name for layer in inspection.layers] == ["roads", "buildings"]
    assert inspection.layers[0].feature_count == 3
    assert inspection.layers[0].bbox == (10.0, 20.0, 30.0, 40.0)
    assert inspection.layers[0].crs == "EPSG:32637"
    assert inspection.layers[0].feature_count_forced is False
    assert inspection.layers[0].bbox_forced is False
    assert backend.calls == [
        ("roads", False, False),
        ("buildings", False, False),
    ]


def test_inspector_forces_only_missing_count_and_bounds() -> None:
    backend = FakeMetadataBackend(
        layers=(("roads", "LineString"),),
        responses={
            "roads": [
                _info(
                    name="roads",
                    geometry_type="LineString",
                    features=-1,
                    total_bounds=None,
                ),
                _info(
                    name="roads",
                    geometry_type="LineString",
                    features=17,
                    total_bounds=(1.0, 2.0, 3.0, 4.0),
                ),
            ]
        },
    )

    inspection = VectorInspector(backend=backend).inspect("roads.gpkg")
    layer = inspection.layers[0]

    assert layer.feature_count == 17
    assert layer.bbox == (1.0, 2.0, 3.0, 4.0)
    assert layer.feature_count_forced is True
    assert layer.bbox_forced is True
    assert backend.calls == [
        ("roads", False, False),
        ("roads", True, True),
    ]


def test_inspector_bounds_potentially_expensive_layer_fallbacks() -> None:
    backend = FakeMetadataBackend(
        layers=(("a", "Point"), ("b", "Point"), ("c", "Point")),
        responses={},
    )

    with pytest.raises(VectorLayerLimitError) as exc_info:
        VectorInspector(backend=backend, max_layers=2).inspect("many.gpkg")

    assert exc_info.value.max_layers == 2
    assert exc_info.value.observed_layers == 3
    assert backend.calls == []


def test_inspector_rejects_metadata_that_remains_incomplete() -> None:
    backend = FakeMetadataBackend(
        layers=(("roads", "LineString"),),
        responses={
            "roads": [
                _info(
                    name="roads",
                    geometry_type="LineString",
                    features=-1,
                    total_bounds=None,
                )
            ]
        },
    )

    with pytest.raises(IncompleteVectorMetadataError, match="feature count"):
        VectorInspector(backend=backend).inspect("roads.gpkg")

    assert backend.calls == [
        ("roads", False, False),
        ("roads", True, True),
    ]


def test_inspector_accepts_empty_spatial_layer_without_bbox() -> None:
    backend = FakeMetadataBackend(
        layers=(("empty", "Polygon"),),
        responses={
            "empty": [
                _info(
                    name="empty",
                    geometry_type="Polygon",
                    features=0,
                    total_bounds=None,
                )
            ]
        },
    )

    layer = VectorInspector(backend=backend).inspect("empty.gpkg").layers[0]

    assert layer.feature_count == 0
    assert layer.bbox is None
    assert layer.bbox_forced is False


def test_inspector_rejects_inconsistent_bbox_ordering() -> None:
    backend = FakeMetadataBackend(
        layers=(("roads", "LineString"),),
        responses={
            "roads": [
                _info(
                    name="roads",
                    geometry_type="LineString",
                    features=1,
                    total_bounds=(5.0, 0.0, 1.0, 2.0),
                )
            ]
        },
    )

    with pytest.raises(VectorInspectionError, match="bbox ordering"):
        VectorInspector(backend=backend).inspect("roads.gpkg")


def test_pyogrio_backend_inspects_real_geojson_without_loading_features(tmp_path: Path) -> None:
    path = tmp_path / "points.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "a"},
                        "geometry": {"type": "Point", "coordinates": [10.0, 20.0]},
                    },
                    {
                        "type": "Feature",
                        "properties": {"name": "b"},
                        "geometry": {"type": "Point", "coordinates": [30.0, 40.0]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    inspection = VectorInspector(max_layers=4).inspect(path)
    layer = inspection.layers[0]

    assert inspection.driver == "GeoJSON"
    assert len(inspection.layers) == 1
    assert layer.geometry_type == "Point"
    assert layer.crs is not None
    assert "4326" in layer.crs
    assert layer.feature_count == 2
    assert layer.bbox == pytest.approx((10.0, 20.0, 30.0, 40.0))
