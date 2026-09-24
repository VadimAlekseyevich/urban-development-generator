import uuid

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.api.dependencies import ValidationLayerQueryServiceDep
from backend.app.application.validation_layers import (
    DEFAULT_VALIDATION_RUN_LIMIT,
    DEFAULT_VIOLATION_LIMIT,
    MAX_VALIDATION_RUN_LIMIT,
    MAX_VIOLATION_LIMIT,
    ValidationLayerDataError,
    ValidationLayerProjectNotFoundError,
    ValidationLayerQueryError,
    ValidationLayerRunNotFoundError,
)
from backend.app.schemas.validation_layer import (
    ValidationRunListResponse,
    ViolationGeoJSONResponse,
    ViolationListResponse,
)

router = APIRouter(tags=["validation"])


@router.get(
    "/projects/{project_id}/validation-runs",
    response_model=ValidationRunListResponse,
)
def list_validation_runs(
    project_id: uuid.UUID,
    service: ValidationLayerQueryServiceDep,
    limit: int = Query(
        DEFAULT_VALIDATION_RUN_LIMIT,
        ge=1,
        le=MAX_VALIDATION_RUN_LIMIT,
    ),
) -> ValidationRunListResponse:
    try:
        result = service.list_runs(project_id=project_id, limit=limit)
    except ValidationLayerProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValidationLayerQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ValidationLayerDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return ValidationRunListResponse.model_validate(result)


@router.get(
    "/projects/{project_id}/validation-runs/{run_id}/violations",
    response_model=ViolationListResponse,
)
def list_run_violations(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: ValidationLayerQueryServiceDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(
        DEFAULT_VIOLATION_LIMIT,
        ge=1,
        le=MAX_VIOLATION_LIMIT,
    ),
) -> ViolationListResponse:
    try:
        result = service.list_violations(
            project_id=project_id,
            run_id=run_id,
            offset=offset,
            limit=limit,
        )
    except ValidationLayerRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValidationLayerQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ValidationLayerDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return ViolationListResponse.model_validate(result)


@router.get(
    "/projects/{project_id}/validation-runs/{run_id}/violations/geojson",
    response_model=ViolationGeoJSONResponse,
)
def get_run_violations_geojson(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: ValidationLayerQueryServiceDep,
    bbox: str = Query(
        ...,
        description="west,south,east,north in EPSG:4326",
    ),
    limit: int = Query(
        DEFAULT_VIOLATION_LIMIT,
        ge=1,
        le=MAX_VIOLATION_LIMIT,
    ),
) -> ViolationGeoJSONResponse:
    try:
        result = service.get_geojson(
            project_id=project_id,
            run_id=run_id,
            bbox_text=bbox,
            limit=limit,
        )
    except ValidationLayerRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValidationLayerQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ValidationLayerDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return ViolationGeoJSONResponse.model_validate(result)
