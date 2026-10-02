from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import BinaryIO, NoReturn

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from backend.app.api.dependencies import ArtifactStoreDep, GeoPackageExportServiceDep
from backend.app.application.geopackage_exports import (
    GEOPACKAGE_EXPORT_CONTENT_TYPE,
    GeoPackageExportConflictError,
    GeoPackageExportError,
    GeoPackageExportNotFoundError,
    GeoPackageExportSpec,
)
from backend.app.schemas.geopackage_export import (
    GeoPackageExportCreate,
    GeoPackageExportRead,
)

router = APIRouter(
    prefix="/projects/{project_id}/exports/geopackage",
    tags=["exports"],
)

_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


def _raise_export_error(
    exc: GeoPackageExportError
    | GeoPackageExportNotFoundError
    | GeoPackageExportConflictError,
) -> NoReturn:
    if isinstance(exc, GeoPackageExportNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, GeoPackageExportConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "",
    response_model=GeoPackageExportRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_geopackage_export(
    project_id: uuid.UUID,
    payload: GeoPackageExportCreate,
    service: GeoPackageExportServiceDep,
) -> GeoPackageExportRead:
    try:
        snapshot = service.create(
            project_id=project_id,
            spec=GeoPackageExportSpec(
                layer_ids=payload.layer_ids,
                dataset_version_id=payload.dataset_version_id,
                run_id=payload.run_id,
                bbox=payload.bbox,
                max_features_per_layer=payload.max_features_per_layer,
                max_total_features=payload.max_total_features,
            ),
        )
        return GeoPackageExportRead.model_validate(snapshot)
    except (
        GeoPackageExportError,
        GeoPackageExportNotFoundError,
        GeoPackageExportConflictError,
    ) as exc:
        _raise_export_error(exc)


@router.get("/{job_id}", response_model=GeoPackageExportRead)
def get_geopackage_export(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    service: GeoPackageExportServiceDep,
) -> GeoPackageExportRead:
    try:
        return GeoPackageExportRead.model_validate(
            service.get(project_id=project_id, job_id=job_id)
        )
    except (
        GeoPackageExportNotFoundError,
        GeoPackageExportConflictError,
    ) as exc:
        _raise_export_error(exc)


@router.get("/{job_id}/download")
def download_geopackage_export(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    service: GeoPackageExportServiceDep,
    store: ArtifactStoreDep,
) -> StreamingResponse:
    try:
        ref, filename, size_bytes = service.ready_artifact_ref(
            project_id=project_id,
            job_id=job_id,
        )
        source = store.open(ref)
    except (
        GeoPackageExportNotFoundError,
        GeoPackageExportConflictError,
    ) as exc:
        _raise_export_error(exc)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="export artifact bytes are missing",
        ) from exc

    return StreamingResponse(
        _stream_and_close(source),
        media_type=GEOPACKAGE_EXPORT_CONTENT_TYPE,
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
