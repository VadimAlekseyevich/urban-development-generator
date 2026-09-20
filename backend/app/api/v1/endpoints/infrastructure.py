import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.api.dependencies import InfrastructureLayerQueryServiceDep
from backend.app.application.infrastructure_layers import (
    DEFAULT_INFRASTRUCTURE_LAYER_LIMIT,
    MAX_INFRASTRUCTURE_LAYER_LIMIT,
    InfrastructureLayerProjectNotFoundError,
    InfrastructureLayerQueryError,
    InfrastructureLayerRunNotFoundError,
)
from backend.app.schemas.infrastructure_layer import (
    InfrastructureGeoJSONResponse,
    InfrastructureRunSummaryResponse,
)

router = APIRouter(tags=["infrastructure"])


@router.get(
    "/projects/{project_id}/infrastructure-runs",
    response_model=list[InfrastructureRunSummaryResponse],
)
def list_infrastructure_runs(
    project_id: uuid.UUID,
    service: InfrastructureLayerQueryServiceDep,
) -> list[InfrastructureRunSummaryResponse]:
    try:
        runs = service.list_runs(project_id=project_id)
    except InfrastructureLayerProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return [
        InfrastructureRunSummaryResponse.model_validate(run)
        for run in runs
    ]


@router.get(
    "/projects/{project_id}/infrastructure-runs/{run_id}/facilities/geojson",
    response_model=InfrastructureGeoJSONResponse,
)
def get_infrastructure_geojson(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: InfrastructureLayerQueryServiceDep,
    bbox: str = Query(..., description="west,south,east,north in EPSG:4326"),
    limit: int = Query(
        DEFAULT_INFRASTRUCTURE_LAYER_LIMIT,
        ge=1,
        le=MAX_INFRASTRUCTURE_LAYER_LIMIT,
    ),
    origin: Literal["existing", "generated"] | None = Query(
        default=None,
        description="optional persisted facility origin filter",
    ),
) -> InfrastructureGeoJSONResponse:
    try:
        result = service.get_facilities(
            project_id=project_id,
            run_id=run_id,
            bbox_text=bbox,
            limit=limit,
            origin=origin,
        )
    except InfrastructureLayerRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InfrastructureLayerQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return InfrastructureGeoJSONResponse.model_validate(result)
