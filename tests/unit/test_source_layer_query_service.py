import uuid

import pytest

from backend.app.application.source_layers import (
    MAX_SOURCE_LAYER_LIMIT,
    SourceLayerBbox,
    SourceLayerContext,
    SourceLayerDatasetVersionNotFoundError,
    SourceLayerFeature,
    SourceLayerName,
    SourceLayerQueryError,
    SourceLayerQueryService,
)


class FakeSourceLayerQueryRepository:
    def __init__(
        self,
        *,
        context: SourceLayerContext | None,
        features: list[SourceLayerFeature] | None = None,
    ) -> None:
        self.context = context
        self.features = list(features or [])
        self.requested_limit: int | None = None
        self.requested_bbox: SourceLayerBbox | None = None
        self.requested_layer: SourceLayerName | None = None

    def get_context(
        self,
        *,
        project_id: uuid.UUID,
        dataset_version_id: uuid.UUID,
    ) -> SourceLayerContext | None:
        return self.context

    def list_features(
        self,
        *,
        dataset_version_id: uuid.UUID,
        layer: SourceLayerName,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[SourceLayerFeature]:
        self.requested_limit = limit
        self.requested_bbox = bbox
        self.requested_layer = layer
        return list(self.features[:limit])


def _feature(index: int) -> SourceLayerFeature:
    return SourceLayerFeature(
        id=uuid.uuid4(),
        geometry={"type": "Point", "coordinates": [float(index), float(index)]},
        properties={"source_feature_id": f"f{index}"},
    )


def test_service_requests_one_extra_row_and_marks_truncated() -> None:
    project_id = uuid.uuid4()
    version_id = uuid.uuid4()
    repository = FakeSourceLayerQueryRepository(
        context=SourceLayerContext(
            project_id=project_id,
            dataset_version_id=version_id,
            working_srid=32637,
        ),
        features=[_feature(1), _feature(2), _feature(3)],
    )

    result = SourceLayerQueryService(repository).get_geojson(
        project_id=project_id,
        dataset_version_id=version_id,
        layer=SourceLayerName.ROADS,
        bbox_text="36.7,55.6,37.0,55.9",
        limit=2,
    )

    assert repository.requested_limit == 3
    assert repository.requested_layer is SourceLayerName.ROADS
    assert repository.requested_bbox == SourceLayerBbox(36.7, 55.6, 37.0, 55.9)
    assert result.query_bbox == (36.7, 55.6, 37.0, 55.9)
    assert result.geojson_crs == "EPSG:4326"
    assert result.working_srid == 32637
    assert len(result.features) == 2
    assert result.truncated is True


@pytest.mark.parametrize(
    "bbox",
    [
        "",
        "1,2,3",
        "a,2,3,4",
        "10,0,5,1",
        "0,20,1,10",
        "-181,0,1,1",
        "0,-91,1,1",
        "nan,0,1,1",
    ],
)
def test_bbox_contract_rejects_malformed_or_unsupported_bounds(bbox: str) -> None:
    with pytest.raises(SourceLayerQueryError):
        SourceLayerBbox.parse(bbox)


@pytest.mark.parametrize("limit", [0, MAX_SOURCE_LAYER_LIMIT + 1, True])
def test_service_rejects_invalid_limit(limit: int) -> None:
    context = SourceLayerContext(
        project_id=uuid.uuid4(),
        dataset_version_id=uuid.uuid4(),
        working_srid=3857,
    )
    with pytest.raises(SourceLayerQueryError, match="limit"):
        SourceLayerQueryService(
            FakeSourceLayerQueryRepository(context=context)
        ).get_geojson(
            project_id=context.project_id,
            dataset_version_id=context.dataset_version_id,
            layer=SourceLayerName.WATER,
            bbox_text="-1,50,1,52",
            limit=limit,
        )


def test_service_rejects_dataset_version_outside_project_scope() -> None:
    with pytest.raises(SourceLayerDatasetVersionNotFoundError):
        SourceLayerQueryService(
            FakeSourceLayerQueryRepository(context=None)
        ).get_geojson(
            project_id=uuid.uuid4(),
            dataset_version_id=uuid.uuid4(),
            layer=SourceLayerName.BUILDINGS,
            bbox_text="-1,50,1,52",
            limit=10,
        )
