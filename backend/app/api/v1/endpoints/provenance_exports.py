"""Download one canonical successful-run provenance manifest as JSON."""

from __future__ import annotations

import uuid
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Response, status

from backend.app.api.dependencies import ProvenanceExportServiceDep
from backend.app.application.provenance_exports import (
    PROVENANCE_EXPORT_MEDIA_TYPE,
    ProvenanceExportDataError,
    ProvenanceExportProjectNotFoundError,
    ProvenanceExportQueryError,
    ProvenanceExportRunNotFoundError,
    ProvenanceExportUnavailableError,
)

router = APIRouter(
    prefix="/projects/{project_id}/exports/provenance",
    tags=["exports"],
)


def _raise_export_error(
    exc: ProvenanceExportQueryError
    | ProvenanceExportProjectNotFoundError
    | ProvenanceExportRunNotFoundError
    | ProvenanceExportUnavailableError
    | ProvenanceExportDataError,
) -> NoReturn:
    if isinstance(
        exc,
        (ProvenanceExportProjectNotFoundError, ProvenanceExportRunNotFoundError),
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ProvenanceExportQueryError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if isinstance(exc, ProvenanceExportUnavailableError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=str(exc),
    ) from exc


@router.get("/{run_id}")
def export_provenance(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: ProvenanceExportServiceDep,
) -> Response:
    try:
        result = service.export(project_id=project_id, run_id=run_id)
    except (
        ProvenanceExportQueryError,
        ProvenanceExportProjectNotFoundError,
        ProvenanceExportRunNotFoundError,
        ProvenanceExportUnavailableError,
        ProvenanceExportDataError,
    ) as exc:
        _raise_export_error(exc)

    return Response(
        content=result.content,
        media_type=PROVENANCE_EXPORT_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "X-Provenance-Schema": result.schema_version,
            "X-Provenance-Checksum": result.checksum,
        },
    )
