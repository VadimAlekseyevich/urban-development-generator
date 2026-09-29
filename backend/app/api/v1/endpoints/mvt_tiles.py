"""S13-T03 dataset/run-authorized, bounded PostGIS MVT tile delivery."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from backend.app.api.dependencies import MvtTileQueryServiceDep
from backend.app.application.mvt_tiles import (
    MVT_DEFAULT_FEATURE_LIMIT,
    MVT_MAX_FEATURE_LIMIT,
    MVT_MEDIA_TYPE,
    MvtTileQueryService,
    MvtTileTooLargeError,
)
from backend.app.application.vector_layers import (
    VectorLayerNotFoundError,
    VectorLayerNotReadyError,
    VectorLayerQueryError,
)

router = APIRouter(tags=["vector-layers"])


@router.get(
    "/projects/{project_id}/vector-layers/{layer_id}/tiles/{z}/{x}/{y}.mvt",
    response_class=Response,
    responses={
        200: {"content": {MVT_MEDIA_TYPE: {}}, "description": "Bounded binary MVT tile"},
        413: {"description": "Encoded tile exceeds the hard byte budget"},
    },
)
def get_vector_layer_mvt(
    project_id: uuid.UUID,
    layer_id: str,
    z: int,
    x: int,
    y: int,
    service: MvtTileQueryServiceDep,
    dataset_version_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    feature_limit: Annotated[
        int, Query(ge=1, le=MVT_MAX_FEATURE_LIMIT)
    ] = MVT_DEFAULT_FEATURE_LIMIT,
) -> Response:
    try:
        tile = service.get_tile(
            project_id=project_id,
            layer_id=layer_id,
            dataset_version_id=dataset_version_id,
            run_id=run_id,
            z=z,
            x=x,
            y=y,
            feature_limit=feature_limit,
        )
    except VectorLayerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VectorLayerNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VectorLayerQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MvtTileTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return Response(
        content=tile.bytes,
        media_type=MVT_MEDIA_TYPE,
        headers={
            # S13-T04 will introduce immutable/version-specific ETags and
            # cache-control. Avoid caching active-run or dataset state here.
            "Cache-Control": "no-store",
            "X-MVT-Schema-Version": tile.schema_version,
            "X-MVT-Feature-Limit": str(tile.feature_limit),
            "X-MVT-Candidates": str(tile.candidate_count),
            "X-Features-Truncated": str(tile.truncated).lower(),
        },
    )
