import uuid

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.api.dependencies import BuildingLayerQueryServiceDep
from backend.app.application.building_layers import (
    DEFAULT_BUILDING_LAYER_LIMIT,
    MAX_BUILDING_LAYER_LIMIT,
    BuildingLayerProjectNotFoundError,
    BuildingLayerQueryError,
    BuildingLayerRunNotFoundError,
)
from backend.app.schemas.building_layer import (
    BuildingGeoJSONResponse,
    BuildingRunSummaryResponse,
)

router = APIRouter(tags=["buildings"])


@router.get(
    "/projects/{project_id}/building-runs",
    response_model=list[BuildingRunSummaryResponse],
)
def list_building_runs(
    project_id: uuid.UUID,
    service: BuildingLayerQueryServiceDep,
) -> list[BuildingRunSummaryResponse]:
    try:
        runs = service.list_runs(project_id=project_id)
    except BuildingLayerProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return [BuildingRunSummaryResponse.model_validate(run) for run in runs]


@router.get(
    "/projects/{project_id}/building-runs/{run_id}/buildings/geojson",
    response_model=BuildingGeoJSONResponse,
)
def get_buildings_geojson(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: BuildingLayerQueryServiceDep,
    bbox: str = Query(..., description="west,south,east,north in EPSG:4326"),
    limit: int = Query(
        DEFAULT_BUILDING_LAYER_LIMIT,
        ge=1,
        le=MAX_BUILDING_LAYER_LIMIT,
    ),
) -> BuildingGeoJSONResponse:
    try:
        result = service.get_buildings(
            project_id=project_id,
            run_id=run_id,
            bbox_text=bbox,
            limit=limit,
        )
    except BuildingLayerRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except BuildingLayerQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return BuildingGeoJSONResponse.model_validate(result)
