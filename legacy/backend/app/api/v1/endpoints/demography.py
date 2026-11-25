import uuid

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.api.dependencies import DemographyLayerQueryServiceDep
from backend.app.application.demography_layers import (
    DEFAULT_DEMOGRAPHY_LAYER_LIMIT,
    MAX_DEMOGRAPHY_LAYER_LIMIT,
    DemographyLayerProjectNotFoundError,
    DemographyLayerQueryError,
    DemographyLayerRunNotFoundError,
)
from backend.app.schemas.demography_layer import (
    DemographyGeoJSONResponse,
    DemographyMetricsResponse,
    DemographyRunSummaryResponse,
)

router = APIRouter(tags=["demography"])


@router.get(
    "/projects/{project_id}/demography-runs",
    response_model=list[DemographyRunSummaryResponse],
)
def list_demography_runs(
    project_id: uuid.UUID,
    service: DemographyLayerQueryServiceDep,
) -> list[DemographyRunSummaryResponse]:
    try:
        runs = service.list_runs(project_id=project_id)
    except DemographyLayerProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return [DemographyRunSummaryResponse.model_validate(run) for run in runs]


@router.get(
    "/projects/{project_id}/demography-runs/{run_id}/metrics",
    response_model=DemographyMetricsResponse,
)
def get_demography_metrics(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: DemographyLayerQueryServiceDep,
) -> DemographyMetricsResponse:
    try:
        result = service.get_metrics(project_id=project_id, run_id=run_id)
    except DemographyLayerRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return DemographyMetricsResponse.model_validate(result)


@router.get(
    "/projects/{project_id}/demography-runs/{run_id}/blocks/geojson",
    response_model=DemographyGeoJSONResponse,
)
def get_demography_blocks_geojson(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: DemographyLayerQueryServiceDep,
    bbox: str = Query(..., description="west,south,east,north in EPSG:4326"),
    limit: int = Query(
        DEFAULT_DEMOGRAPHY_LAYER_LIMIT,
        ge=1,
        le=MAX_DEMOGRAPHY_LAYER_LIMIT,
    ),
) -> DemographyGeoJSONResponse:
    try:
        result = service.get_blocks(
            project_id=project_id,
            run_id=run_id,
            bbox_text=bbox,
            limit=limit,
        )
    except DemographyLayerRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except DemographyLayerQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return DemographyGeoJSONResponse.model_validate(result)
