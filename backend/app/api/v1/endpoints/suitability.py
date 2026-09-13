import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from backend.app.api.dependencies import SuitabilityLayerServiceDep
from backend.app.application.suitability_layers import (
    DEFAULT_PREVIEW_DIMENSION,
    MAX_PREVIEW_DIMENSION,
    MIN_PREVIEW_DIMENSION,
    SuitabilityArtifactContractError,
    SuitabilityArtifactNotFoundError,
    SuitabilityArtifactUnavailableError,
    SuitabilityLayerError,
    SuitabilityLayerMetadata,
)
from backend.app.schemas.suitability_layer import SuitabilityLayerMetadataResponse

router = APIRouter(prefix="/suitability-artifacts", tags=["suitability"])


@router.get("/{artifact_id}", response_model=SuitabilityLayerMetadataResponse)
def get_suitability_artifact_metadata(
    artifact_id: uuid.UUID,
    service: SuitabilityLayerServiceDep,
) -> SuitabilityLayerMetadata:
    try:
        return service.get_metadata(artifact_id=artifact_id)
    except SuitabilityArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Suitability artifact not found") from exc
    except SuitabilityArtifactUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SuitabilityArtifactContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{artifact_id}/preview.png")
def get_suitability_artifact_preview(
    artifact_id: uuid.UUID,
    service: SuitabilityLayerServiceDep,
    max_dimension: Annotated[
        int,
        Query(ge=MIN_PREVIEW_DIMENSION, le=MAX_PREVIEW_DIMENSION),
    ] = DEFAULT_PREVIEW_DIMENSION,
) -> Response:
    try:
        preview = service.render_preview(
            artifact_id=artifact_id,
            max_dimension=max_dimension,
        )
    except SuitabilityArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Suitability artifact not found") from exc
    except SuitabilityArtifactUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SuitabilityArtifactContractError, SuitabilityLayerError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    etag = f'"{preview.source_checksum}-{preview.width}x{preview.height}-v1"'
    return Response(
        content=preview.content,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": etag,
            "X-Image-Width": str(preview.width),
            "X-Image-Height": str(preview.height),
        },
    )
