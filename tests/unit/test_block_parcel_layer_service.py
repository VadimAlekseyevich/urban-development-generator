import uuid

import pytest

from backend.app.application.block_parcel_layers import (
    BlockParcelFeature,
    BlockParcelLayerProjectNotFoundError,
    BlockParcelLayerQueryError,
    BlockParcelLayerQueryService,
    BlockParcelLayerRunNotFoundError,
    BlockParcelRunContext,
    BlockParcelRunSummary,
)
from backend.app.application.source_layers import SourceLayerBbox


class FakeBlockParcelRepository:
    def __init__(self) -> None:
        self.project_id = uuid.uuid4()
        self.run_id = uuid.uuid4()
        self.last_block_limit: int | None = None

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        return project_id == self.project_id

    def list_runs(self, *, project_id: uuid.UUID) -> list[BlockParcelRunSummary]:
        return []

    def get_run_context(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> BlockParcelRunContext | None:
        if project_id != self.project_id or run_id != self.run_id:
            return None
        return BlockParcelRunContext(
            project_id=project_id,
            run_id=run_id,
            working_srid=3857,
            status="running",
        )

    def list_blocks(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[BlockParcelFeature]:
        self.last_block_limit = limit
        return [
            BlockParcelFeature(
                id=uuid.uuid4(),
                geometry={"type": "Polygon", "coordinates": []},
                properties={"block_key": f"block:{index}"},
            )
            for index in range(3)
        ]

    def list_parcels(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[BlockParcelFeature]:
        return []


def test_block_query_uses_lookahead_for_truncation() -> None:
    repository = FakeBlockParcelRepository()
    service = BlockParcelLayerQueryService(repository)

    result = service.get_blocks(
        project_id=repository.project_id,
        run_id=repository.run_id,
        bbox_text="-0.2,51.4,0.0,51.6",
        limit=2,
    )

    assert repository.last_block_limit == 3
    assert result.truncated is True
    assert len(result.features) == 2
    assert result.geojson_crs == "EPSG:4326"
    assert result.working_srid == 3857


def test_block_parcel_service_enforces_project_run_and_bbox_contract() -> None:
    repository = FakeBlockParcelRepository()
    service = BlockParcelLayerQueryService(repository)

    with pytest.raises(BlockParcelLayerProjectNotFoundError):
        service.list_runs(project_id=uuid.uuid4())

    with pytest.raises(BlockParcelLayerRunNotFoundError):
        service.get_parcels(
            project_id=repository.project_id,
            run_id=uuid.uuid4(),
            bbox_text="-0.2,51.4,0.0,51.6",
        )

    with pytest.raises(BlockParcelLayerQueryError):
        service.get_blocks(
            project_id=repository.project_id,
            run_id=repository.run_id,
            bbox_text="bad",
        )
