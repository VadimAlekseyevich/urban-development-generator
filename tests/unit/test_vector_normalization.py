import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import GeometryCollection, LineString, Point, Polygon

from backend.app.services.vector_inspection import VectorLayerInspection
from backend.app.services.vector_normalization import (
    EmptyGeometryPolicy,
    GeometryFamily,
    GeometryMismatchPolicy,
    MissingVectorCRSError,
    VectorBatchBackend,
    VectorBatchContractError,
    VectorGeometryRejectedError,
    VectorNormalizationConfig,
    VectorNormalizer,
)
from core.urban_generator.domain import WorkingCRS


class FakeBatchBackend(VectorBatchBackend):
    def __init__(self, frames: list[gpd.GeoDataFrame]) -> None:
        self.frames = list(frames)
        self.calls: list[tuple[str, int, int]] = []

    def read_batch(
        self,
        source: str | Path,
        *,
        layer: str,
        skip_features: int,
        max_features: int,
    ) -> gpd.GeoDataFrame:
        del source
        self.calls.append((layer, skip_features, max_features))
        return self.frames.pop(0)


def _inspection(
    feature_count: int,
    *,
    name: str = "layer",
    crs: str | None = "EPSG:4326",
    geometry_type: str | None = "Point",
) -> VectorLayerInspection:
    return VectorLayerInspection(
        name=name,
        driver="GeoJSON",
        geometry_type=geometry_type,
        crs=crs,
        feature_count=feature_count,
        bbox=None,
        encoding="UTF-8",
        feature_count_forced=False,
        bbox_forced=False,
    )


def test_normalizer_reads_exact_bounded_batches_and_reprojects() -> None:
    backend = FakeBatchBackend(
        [
            gpd.GeoDataFrame(
                {"value": [1, 2]},
                geometry=[Point(37.0, 55.0), Point(37.1, 55.1)],
                crs="EPSG:4326",
            ),
            gpd.GeoDataFrame(
                {"value": [3]},
                geometry=[Point(37.2, 55.2)],
                crs="EPSG:4326",
            ),
        ]
    )

    batches = list(
        VectorNormalizer(backend=backend).iter_normalized_batches(
            "points.geojson",
            inspection=_inspection(3),
            working_crs=WorkingCRS(32637),
            config=VectorNormalizationConfig(
                geometry_family=GeometryFamily.POINT,
                batch_size=2,
            ),
        )
    )

    assert backend.calls == [("layer", 0, 2), ("layer", 2, 1)]
    assert [len(batch.frame) for batch in batches] == [2, 1]
    assert [batch.start_feature for batch in batches] == [0, 2]
    assert all(batch.frame.crs.to_epsg() == 32637 for batch in batches)
    assert all(batch.working_srid == 32637 for batch in batches)


def test_normalizer_repairs_filters_and_reports_diagnostics() -> None:
    invalid_bowtie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    collection = GeometryCollection(
        [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]),
            LineString([(0, 0), (1, 1)]),
        ]
    )
    frame = gpd.GeoDataFrame(
        {"value": [1, 2, 3, 4]},
        geometry=[invalid_bowtie, Point(1, 1), None, collection],
        crs="EPSG:32637",
    )

    batch = list(
        VectorNormalizer(backend=FakeBatchBackend([frame])).iter_normalized_batches(
            "mixed.gpkg",
            inspection=_inspection(4, crs="EPSG:32637", geometry_type="Unknown"),
            working_crs=WorkingCRS(32637),
            config=VectorNormalizationConfig(
                geometry_family=GeometryFamily.POLYGON,
                batch_size=10,
            ),
        )
    )[0]

    assert len(batch.frame) == 2
    assert all(geometry.is_valid for geometry in batch.frame.geometry)
    assert batch.diagnostics.input_features == 4
    assert batch.diagnostics.output_features == 2
    assert batch.diagnostics.repaired_features == 1
    assert batch.diagnostics.dropped_empty_features == 1
    assert batch.diagnostics.dropped_type_features == 1
    assert batch.diagnostics.filtered_collection_features == 1


def test_normalizer_can_reject_empty_geometry() -> None:
    frame = gpd.GeoDataFrame({"value": [1]}, geometry=[None], crs="EPSG:32637")

    with pytest.raises(VectorGeometryRejectedError, match="null/empty"):
        list(
            VectorNormalizer(backend=FakeBatchBackend([frame])).iter_normalized_batches(
                "points.gpkg",
                inspection=_inspection(1, crs="EPSG:32637"),
                working_crs=WorkingCRS(32637),
                config=VectorNormalizationConfig(
                    geometry_family=GeometryFamily.POINT,
                    empty_geometry_policy=EmptyGeometryPolicy.REJECT,
                ),
            )
        )


def test_normalizer_can_reject_geometry_family_mismatch() -> None:
    frame = gpd.GeoDataFrame(
        {"value": [1]},
        geometry=[Point(1, 1)],
        crs="EPSG:32637",
    )

    with pytest.raises(VectorGeometryRejectedError, match="incompatible"):
        list(
            VectorNormalizer(backend=FakeBatchBackend([frame])).iter_normalized_batches(
                "mixed.gpkg",
                inspection=_inspection(1, crs="EPSG:32637"),
                working_crs=WorkingCRS(32637),
                config=VectorNormalizationConfig(
                    geometry_family=GeometryFamily.POLYGON,
                    mismatch_policy=GeometryMismatchPolicy.REJECT,
                ),
            )
        )


def test_normalizer_requires_explicit_horizontal_source_crs_before_read() -> None:
    for crs in (None, "EPSG:4978"):
        backend = FakeBatchBackend([])
        with pytest.raises(MissingVectorCRSError):
            list(
                VectorNormalizer(backend=backend).iter_normalized_batches(
                    "unknown.gpkg",
                    inspection=_inspection(1, crs=crs),
                    working_crs=WorkingCRS(32637),
                    config=VectorNormalizationConfig(geometry_family=GeometryFamily.POINT),
                )
            )
        assert backend.calls == []


def test_normalizer_rejects_crs_change_between_inspection_and_batch() -> None:
    frame = gpd.GeoDataFrame(
        {"value": [1]},
        geometry=[Point(1, 1)],
        crs="EPSG:3857",
    )

    with pytest.raises(VectorBatchContractError, match="CRS changed"):
        list(
            VectorNormalizer(backend=FakeBatchBackend([frame])).iter_normalized_batches(
                "points.gpkg",
                inspection=_inspection(1, crs="EPSG:4326"),
                working_crs=WorkingCRS(32637),
                config=VectorNormalizationConfig(geometry_family=GeometryFamily.POINT),
            )
        )


def test_normalizer_rejects_short_non_final_batch() -> None:
    frame = gpd.GeoDataFrame(
        {"value": [1, 2]},
        geometry=[Point(1, 1), Point(2, 2)],
        crs="EPSG:4326",
    )

    with pytest.raises(VectorBatchContractError, match="short non-final batch"):
        list(
            VectorNormalizer(backend=FakeBatchBackend([frame])).iter_normalized_batches(
                "points.geojson",
                inspection=_inspection(3),
                working_crs=WorkingCRS(32637),
                config=VectorNormalizationConfig(
                    geometry_family=GeometryFamily.POINT,
                    batch_size=3,
                ),
            )
        )


def test_pyogrio_backend_normalizes_real_geojson_in_bounded_batches(tmp_path: Path) -> None:
    path = tmp_path / "points.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "a"},
                        "geometry": {
                            "type": "Point",
                            "coordinates": [37.62, 55.75],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {"name": "b"},
                        "geometry": {
                            "type": "Point",
                            "coordinates": [37.63, 55.76],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    batches = list(
        VectorNormalizer().iter_normalized_batches(
            path,
            inspection=_inspection(2, name="points"),
            working_crs=WorkingCRS(32637),
            config=VectorNormalizationConfig(
                geometry_family=GeometryFamily.POINT,
                batch_size=1,
            ),
        )
    )

    assert len(batches) == 2
    assert [batch.diagnostics.input_features for batch in batches] == [1, 1]
    assert [batch.diagnostics.output_features for batch in batches] == [1, 1]
    assert all(batch.frame.crs.to_epsg() == 32637 for batch in batches)
    assert all(batch.frame.geometry.iloc[0].is_valid for batch in batches)


@pytest.mark.parametrize("batch_size", [0, -1, True])
def test_normalization_config_rejects_invalid_batch_size(batch_size: int) -> None:
    with pytest.raises(ValueError, match="batch_size"):
        VectorNormalizationConfig(
            geometry_family=GeometryFamily.POINT,
            batch_size=batch_size,
        )
