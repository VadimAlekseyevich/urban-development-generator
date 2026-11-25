import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from backend.app.api.dependencies import SourceLayerQueryServiceDep
from backend.app.application.source_layers import (
    DEFAULT_SOURCE_LAYER_LIMIT,
    MAX_SOURCE_LAYER_LIMIT,
    ProjectBoundaryFeature,
    SourceLayerDatasetVersionNotFoundError,
    SourceLayerName,
    SourceLayerProjectNotFoundError,
    SourceLayerQueryError,
    SourceLayerQueryResult,
)
from backend.app.schemas.source_layer import (
    ProjectBoundaryGeoJSONResponse,
    SourceLayerGeoJSONResponse,
)

router = APIRouter(tags=["source-layers"])


@router.get(
    "/projects/{project_id}/boundary/geojson",
    response_model=ProjectBoundaryGeoJSONResponse,
)
def get_project_boundary_geojson(
    project_id: uuid.UUID,
    service: SourceLayerQueryServiceDep,
) -> ProjectBoundaryFeature:
    try:
        return service.get_project_boundary(project_id=project_id)
    except SourceLayerProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc


@router.get(
    "/projects/{project_id}/dataset-versions/{dataset_version_id}"
    "/source-layers/{layer}/geojson",
    response_model=SourceLayerGeoJSONResponse,
)
def get_source_layer_geojson(
    project_id: uuid.UUID,
    dataset_version_id: uuid.UUID,
    layer: SourceLayerName,
    service: SourceLayerQueryServiceDep,
    bbox: Annotated[
        str,
        Query(
            description=(
                "Viewport west,south,east,north in EPSG:4326; "
                "antimeridian-crossing boxes are not supported."
            )
        ),
    ],
    limit: Annotated[int, Query(ge=1, le=MAX_SOURCE_LAYER_LIMIT)] = (
        DEFAULT_SOURCE_LAYER_LIMIT
    ),
) -> SourceLayerQueryResult:
    try:
        return service.get_geojson(
            project_id=project_id,
            dataset_version_id=dataset_version_id,
            layer=layer,
            bbox_text=bbox,
            limit=limit,
        )
    except SourceLayerDatasetVersionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Dataset version not found for project",
        ) from exc
    except SourceLayerQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
