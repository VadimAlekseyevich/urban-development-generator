from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import BinaryIO, NoReturn

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from backend.app.api.dependencies import ArtifactStoreDep, GeoJsonExportServiceDep
from backend.app.application.geojson_exports import (
    GEOJSON_EXPORT_CONTENT_TYPE,
    GeoJsonExportConflictError,
    GeoJsonExportError,
    GeoJsonExportNotFoundError,
    GeoJsonExportSpec,
)
from backend.app.schemas.geojson_export import GeoJsonExportCreate, GeoJsonExportRead

router = APIRouter(
    prefix="/projects/{project_id}/exports/geojson",
    tags=["exports"],
)

_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


def _raise_export_error(
    exc: GeoJsonExportError | GeoJsonExportNotFoundError | GeoJsonExportConflictError,
) -> NoReturn:
    if isinstance(exc, GeoJsonExportNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, GeoJsonExportConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "",
    response_model=GeoJsonExportRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_geojson_export(
    project_id: uuid.UUID,
    payload: GeoJsonExportCreate,
    service: GeoJsonExportServiceDep,
) -> GeoJsonExportRead:
    try:
        return service.create(
            project_id=project_id,
            spec=GeoJsonExportSpec(
                layer_id=payload.layer_id,
                dataset_version_id=payload.dataset_version_id,
                run_id=payload.run_id,
                bbox=payload.bbox,
                max_features=payload.max_features,
            ),
        )
    except (
        GeoJsonExportError,
        GeoJsonExportNotFoundError,
        GeoJsonExportConflictError,
    ) as exc:
        _raise_export_error(exc)


@router.get("/{job_id}", response_model=GeoJsonExportRead)
def get_geojson_export(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    service: GeoJsonExportServiceDep,
) -> GeoJsonExportRead:
    try:
        return service.get(project_id=project_id, job_id=job_id)
    except (GeoJsonExportNotFoundError, GeoJsonExportConflictError) as exc:
        _raise_export_error(exc)


@router.get("/{job_id}/download")
def download_geojson_export(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    service: GeoJsonExportServiceDep,
    store: ArtifactStoreDep,
) -> StreamingResponse:
    try:
        ref, filename, size_bytes = service.ready_artifact_ref(
            project_id=project_id,
            job_id=job_id,
        )
        source = store.open(ref)
    except (GeoJsonExportNotFoundError, GeoJsonExportConflictError) as exc:
        _raise_export_error(exc)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="export artifact bytes are missing",
        ) from exc

    return StreamingResponse(
        _stream_and_close(source),
        media_type=GEOJSON_EXPORT_CONTENT_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(size_bytes),
        },
    )


def _stream_and_close(source: BinaryIO) -> Iterator[bytes]:
    with source:
        while True:
            chunk = source.read(_DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
