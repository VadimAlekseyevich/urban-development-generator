import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from backend.app.api.dependencies import ZoningLayerQueryServiceDep
from backend.app.application.zoning_layers import (
    DEFAULT_ZONING_LAYER_LIMIT,
    MAX_ZONING_LAYER_LIMIT,
    GeneratedZoneQueryResult,
    ZoningLayerQueryError,
    ZoningProjectNotFoundError,
    ZoningRunNotFoundError,
    ZoningRunSummary,
)
from backend.app.schemas.zoning_layer import (
    GeneratedZoneGeoJSONResponse,
    ZoningRunSummaryResponse,
)

router = APIRouter(tags=["zoning"])


@router.get(
    "/projects/{project_id}/zoning-runs",
    response_model=list[ZoningRunSummaryResponse],
)
def list_zoning_runs(
    project_id: uuid.UUID,
    service: ZoningLayerQueryServiceDep,
) -> tuple[ZoningRunSummary, ...]:
    try:
        return service.list_runs(project_id=project_id)
    except ZoningProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc


@router.get(
    "/projects/{project_id}/zoning-runs/{run_id}/zones/geojson",
    response_model=GeneratedZoneGeoJSONResponse,
)
def get_generated_zones_geojson(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: ZoningLayerQueryServiceDep,
    bbox: Annotated[
        str,
        Query(
            description=(
                "Viewport west,south,east,north in EPSG:4326; "
                "antimeridian-crossing boxes are not supported."
            )
        ),
    ],
    limit: Annotated[int, Query(ge=1, le=MAX_ZONING_LAYER_LIMIT)] = (
        DEFAULT_ZONING_LAYER_LIMIT
    ),
) -> GeneratedZoneQueryResult:
    try:
        return service.get_generated_zones(
            project_id=project_id,
            run_id=run_id,
            bbox_text=bbox,
            limit=limit,
        )
    except ZoningRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Zoning run not found") from exc
    except ZoningLayerQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
