import uuid
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.api.dependencies import BlockParcelLayerQueryServiceDep
from backend.app.application.block_parcel_layers import (
    DEFAULT_BLOCK_PARCEL_LAYER_LIMIT,
    MAX_BLOCK_PARCEL_LAYER_LIMIT,
    BlockParcelLayerProjectNotFoundError,
    BlockParcelLayerQueryError,
    BlockParcelLayerRunNotFoundError,
    BlockParcelQueryResult,
)
from backend.app.schemas.block_parcel_layer import (
    BlockParcelGeoJSONResponse,
    BlockParcelRunSummaryResponse,
)

router = APIRouter(tags=["blocks-parcels"])


@router.get(
    "/projects/{project_id}/block-runs",
    response_model=list[BlockParcelRunSummaryResponse],
)
def list_block_runs(
    project_id: uuid.UUID,
    service: BlockParcelLayerQueryServiceDep,
) -> list[BlockParcelRunSummaryResponse]:
    try:
        runs = service.list_runs(project_id=project_id)
    except BlockParcelLayerProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return [BlockParcelRunSummaryResponse.model_validate(run) for run in runs]


@router.get(
    "/projects/{project_id}/block-runs/{run_id}/blocks/geojson",
    response_model=BlockParcelGeoJSONResponse,
)
def get_blocks_geojson(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: BlockParcelLayerQueryServiceDep,
    bbox: str = Query(..., description="west,south,east,north in EPSG:4326"),
    limit: int = Query(
        DEFAULT_BLOCK_PARCEL_LAYER_LIMIT,
        ge=1,
        le=MAX_BLOCK_PARCEL_LAYER_LIMIT,
    ),
) -> BlockParcelGeoJSONResponse:
    return _geojson_response(
        lambda: service.get_blocks(
            project_id=project_id,
            run_id=run_id,
            bbox_text=bbox,
            limit=limit,
        )
    )


@router.get(
    "/projects/{project_id}/block-runs/{run_id}/parcels/geojson",
    response_model=BlockParcelGeoJSONResponse,
)
def get_parcels_geojson(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: BlockParcelLayerQueryServiceDep,
    bbox: str = Query(..., description="west,south,east,north in EPSG:4326"),
    limit: int = Query(
        DEFAULT_BLOCK_PARCEL_LAYER_LIMIT,
        ge=1,
        le=MAX_BLOCK_PARCEL_LAYER_LIMIT,
    ),
) -> BlockParcelGeoJSONResponse:
    return _geojson_response(
        lambda: service.get_parcels(
            project_id=project_id,
            run_id=run_id,
            bbox_text=bbox,
            limit=limit,
        )
    )


def _geojson_response(
    loader: Callable[[], BlockParcelQueryResult],
) -> BlockParcelGeoJSONResponse:
    try:
        result = loader()
    except BlockParcelLayerRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except BlockParcelLayerQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return BlockParcelGeoJSONResponse.model_validate(result)
