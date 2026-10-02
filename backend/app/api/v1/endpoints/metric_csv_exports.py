"""Download persisted canonical raw metrics for one run or a bounded comparison."""

from __future__ import annotations

import uuid
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Response, status

from backend.app.api.dependencies import MetricCsvExportServiceDep
from backend.app.application.metrics_csv_exports import (
    METRIC_CSV_MEDIA_TYPE,
    MetricCsvExportDataError,
    MetricCsvExportProjectNotFoundError,
    MetricCsvExportQueryError,
    MetricCsvExportRunNotFoundError,
    MetricCsvExportUnavailableError,
)
from backend.app.schemas.metric_csv_export import MetricCsvExportRequest

router = APIRouter(
    prefix="/projects/{project_id}/exports/metrics.csv",
    tags=["exports"],
)


def _raise_export_error(
    exc: MetricCsvExportQueryError
    | MetricCsvExportProjectNotFoundError
    | MetricCsvExportRunNotFoundError
    | MetricCsvExportUnavailableError
    | MetricCsvExportDataError,
) -> NoReturn:
    if isinstance(
        exc,
        (MetricCsvExportProjectNotFoundError, MetricCsvExportRunNotFoundError),
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, MetricCsvExportQueryError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if isinstance(exc, MetricCsvExportUnavailableError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=str(exc),
    ) from exc


@router.post("")
def export_metrics_csv(
    project_id: uuid.UUID,
    payload: MetricCsvExportRequest,
    service: MetricCsvExportServiceDep,
) -> Response:
    try:
        result = service.export(project_id=project_id, run_ids=payload.run_ids)
    except (
        MetricCsvExportQueryError,
        MetricCsvExportProjectNotFoundError,
        MetricCsvExportRunNotFoundError,
        MetricCsvExportUnavailableError,
        MetricCsvExportDataError,
    ) as exc:
        _raise_export_error(exc)

    return Response(
        content=result.content,
        media_type=METRIC_CSV_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "X-Metrics-Csv-Schema": result.schema_version,
        },
    )
