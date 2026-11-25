"""Project-scoped read-only dataset version catalog for the upload workspace."""

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.api.dependencies import DatasetVersionQueryServiceDep
from backend.app.application.dataset_versions import (
    DatasetVersionProjectNotFoundError,
)
from backend.app.schemas.dataset_version import DatasetVersionPageRead

router = APIRouter(tags=["dataset-versions"])


@router.get(
    "/projects/{project_id}/dataset-versions",
    response_model=DatasetVersionPageRead,
)
def list_dataset_versions(
    project_id: uuid.UUID,
    service: DatasetVersionQueryServiceDep,
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> DatasetVersionPageRead:
    try:
        result = service.list_versions(
            project_id=project_id, limit=limit, offset=offset
        )
    except DatasetVersionProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        ) from exc
    return DatasetVersionPageRead.model_validate(result)
