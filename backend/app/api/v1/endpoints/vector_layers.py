"""S13-T02 owner-qualified bounded vector API (GeoJSON RFC 7946)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from backend.app.api.dependencies import VectorLayerQueryServiceDep
from backend.app.application.vector_layers import (
    DEFAULT_VECTOR_LIMIT,
    MAX_SIMPLIFY_METRES,
    MAX_VECTOR_LIMIT,
    VectorLayerNotFoundError,
    VectorLayerNotReadyError,
    VectorLayerQueryError,
    VectorPage,
)
from backend.app.schemas.vector_layer import VectorPageResponse

router = APIRouter(tags=["vector-layers"])


@router.get(
    "/projects/{project_id}/vector-layers/{layer_id}/geojson",
    response_model=VectorPageResponse,
)
def get_vector_layer_geojson(
    project_id: uuid.UUID,
    layer_id: str,
    service: VectorLayerQueryServiceDep,
    bbox: Annotated[
        str,
        Query(
            description=(
                "west,south,east,north in EPSG:4326; no antimeridian crossing"
            ),
        ),
    ],
    dataset_version_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_VECTOR_LIMIT)] = DEFAULT_VECTOR_LIMIT,
    after: Annotated[
        uuid.UUID | None,
        Query(description="Exclusive last feature UUID from next_after"),
    ] = None,
    presentation_srid: Annotated[
        int,
        Query(description="GeoJSON RFC 7946 only supports EPSG:4326"),
    ] = 4326,
    simplify_m: Annotated[
        float,
        Query(
            ge=0.0,
            le=MAX_SIMPLIFY_METRES,
            allow_inf_nan=False,
            description="Optional 0–100 metre render-only working-CRS simplification",
        ),
    ] = 0.0,
) -> VectorPage:
    try:
        return service.get_geojson(
            project_id=project_id,
            layer_id=layer_id,
            dataset_version_id=dataset_version_id,
            run_id=run_id,
            bbox_text=bbox,
            limit=limit,
            after=after,
            presentation_srid=presentation_srid,
            simplify_m=simplify_m,
        )
    except VectorLayerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VectorLayerNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VectorLayerQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
