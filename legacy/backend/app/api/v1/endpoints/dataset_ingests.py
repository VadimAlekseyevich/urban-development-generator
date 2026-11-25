"""Start/read persisted ingest jobs for existing ready uploaded artifacts."""

import uuid

from fastapi import APIRouter, HTTPException, status

from backend.app.db.ingest_submission import (
    IngestSubmissionConflict,
    IngestSubmissionNotFound,
    SqlAlchemyIngestSubmissionService,
)
from backend.app.schemas.dataset_ingest import DatasetIngestCreate, DatasetIngestRead

router = APIRouter(prefix="/projects/{project_id}/ingests", tags=["dataset-ingests"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DatasetIngestRead)
def start_ingest(project_id: uuid.UUID, payload: DatasetIngestCreate) -> DatasetIngestRead:
    try:
        result = SqlAlchemyIngestSubmissionService().create(
            project_id=project_id,
            artifact_id=payload.artifact_id,
            kind=payload.dataset_kind,
            source_format=payload.source_format,
            layer=payload.layer,
        )
    except IngestSubmissionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IngestSubmissionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DatasetIngestRead.model_validate(result, from_attributes=True)


@router.get("/{version_id}", response_model=DatasetIngestRead)
def read_ingest(project_id: uuid.UUID, version_id: uuid.UUID) -> DatasetIngestRead:
    try:
        result = SqlAlchemyIngestSubmissionService().get(
            project_id=project_id, version_id=version_id,
        )
    except IngestSubmissionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IngestSubmissionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DatasetIngestRead.model_validate(result, from_attributes=True)
