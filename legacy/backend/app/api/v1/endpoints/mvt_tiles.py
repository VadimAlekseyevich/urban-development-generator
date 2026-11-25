"""S13-T03 dataset/run-authorized, bounded PostGIS MVT tile delivery."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response

from backend.app.api.dependencies import MvtTileQueryServiceDep
from backend.app.application.mvt_tiles import (
    MVT_DEFAULT_FEATURE_LIMIT,
    MVT_MAX_FEATURE_LIMIT,
    MVT_MEDIA_TYPE,
    MVT_VOLATILE_CACHE_CONTROL,
    MvtTileTooLargeError,
    matches_if_none_match,
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
        304: {"description": "Matching immutable tile ETag; no binary body"},
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
    if_none_match: Annotated[
        str | None, Header(alias="If-None-Match")
    ] = None,
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
        raise HTTPException(
            status_code=404, detail=str(exc),
            headers={"Cache-Control": MVT_VOLATILE_CACHE_CONTROL},
        ) from exc
    except VectorLayerNotReadyError as exc:
        raise HTTPException(
            status_code=409, detail=str(exc),
            headers={"Cache-Control": MVT_VOLATILE_CACHE_CONTROL},
        ) from exc
    except VectorLayerQueryError as exc:
        raise HTTPException(
            status_code=422, detail=str(exc),
            headers={"Cache-Control": MVT_VOLATILE_CACHE_CONTROL},
        ) from exc
    except MvtTileTooLargeError as exc:
        raise HTTPException(
            status_code=413, detail=str(exc),
            headers={"Cache-Control": MVT_VOLATILE_CACHE_CONTROL},
        ) from exc
    headers = {
        "Cache-Control": tile.cache_control,
        "X-MVT-Schema-Version": tile.schema_version,
        "X-MVT-Feature-Limit": str(tile.feature_limit),
        "X-MVT-Candidates": str(tile.candidate_count),
        "X-Features-Truncated": str(tile.truncated).lower(),
    }
    if tile.etag is not None:
        headers["ETag"] = tile.etag
    # Scope and readiness checks + full bounded encoding occur before this
    # conditional comparison. A stale/foreign client ETag cannot bypass auth.
    if matches_if_none_match(if_none_match, tile.etag):
        return Response(status_code=304, headers=headers)
    return Response(content=tile.bytes, media_type=MVT_MEDIA_TYPE, headers=headers)
