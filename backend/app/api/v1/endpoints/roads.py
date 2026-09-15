import uuid

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.api.dependencies import RoadLayerQueryServiceDep
from backend.app.application.road_layers import (
    DEFAULT_ROAD_LAYER_LIMIT,
    MAX_ROAD_LAYER_LIMIT,
    RoadLayerProjectNotFoundError,
    RoadLayerQueryError,
    RoadLayerRunNotFoundError,
)
from backend.app.schemas.road_layer import (
    GeneratedRoadGeoJSONResponse,
    RoadGraphDiagnosticsResponse,
    RoadRunSummaryResponse,
)

router = APIRouter(tags=["roads"])


@router.get(
    "/projects/{project_id}/road-runs",
    response_model=list[RoadRunSummaryResponse],
)
def list_road_runs(
    project_id: uuid.UUID,
    service: RoadLayerQueryServiceDep,
) -> list[RoadRunSummaryResponse]:
    try:
        runs = service.list_runs(project_id=project_id)
    except RoadLayerProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [RoadRunSummaryResponse.model_validate(run) for run in runs]


@router.get(
    "/projects/{project_id}/road-runs/{run_id}/roads/geojson",
    response_model=GeneratedRoadGeoJSONResponse,
)
def get_generated_roads_geojson(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: RoadLayerQueryServiceDep,
    bbox: str = Query(..., description="west,south,east,north in EPSG:4326"),
    limit: int = Query(
        DEFAULT_ROAD_LAYER_LIMIT,
        ge=1,
        le=MAX_ROAD_LAYER_LIMIT,
    ),
) -> GeneratedRoadGeoJSONResponse:
    try:
        result = service.get_generated_roads(
            project_id=project_id,
            run_id=run_id,
            bbox_text=bbox,
            limit=limit,
        )
    except RoadLayerRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RoadLayerQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return GeneratedRoadGeoJSONResponse.model_validate(result)


@router.get(
    "/projects/{project_id}/road-runs/{run_id}/diagnostics",
    response_model=RoadGraphDiagnosticsResponse,
)
def get_road_graph_diagnostics(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: RoadLayerQueryServiceDep,
) -> RoadGraphDiagnosticsResponse:
    try:
        result = service.get_diagnostics(project_id=project_id, run_id=run_id)
    except RoadLayerRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return RoadGraphDiagnosticsResponse.model_validate(result)
