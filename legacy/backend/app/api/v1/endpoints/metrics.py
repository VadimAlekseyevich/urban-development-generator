import uuid

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.api.dependencies import MetricDashboardQueryServiceDep
from backend.app.application.metric_dashboard import (
    DEFAULT_METRIC_RUN_LIMIT,
    MAX_METRIC_RUN_LIMIT,
    MetricDashboardDataError,
    MetricDashboardProjectNotFoundError,
    MetricDashboardQueryError,
    MetricDashboardRunNotFoundError,
    MetricDashboardUnavailableError,
)
from backend.app.schemas.metric_dashboard import (
    MetricDashboardResponse,
    MetricRunListResponse,
)

router = APIRouter(tags=["metrics"])


@router.get(
    "/projects/{project_id}/metric-runs",
    response_model=MetricRunListResponse,
)
def list_metric_runs(
    project_id: uuid.UUID,
    service: MetricDashboardQueryServiceDep,
    limit: int = Query(
        DEFAULT_METRIC_RUN_LIMIT,
        ge=1,
        le=MAX_METRIC_RUN_LIMIT,
    ),
) -> MetricRunListResponse:
    try:
        result = service.list_runs(project_id=project_id, limit=limit)
    except MetricDashboardProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except MetricDashboardQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except MetricDashboardDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return MetricRunListResponse.model_validate(result)


@router.get(
    "/projects/{project_id}/metric-runs/{run_id}/metrics",
    response_model=MetricDashboardResponse,
)
def get_metric_dashboard(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: MetricDashboardQueryServiceDep,
) -> MetricDashboardResponse:
    try:
        result = service.get_dashboard(
            project_id=project_id,
            run_id=run_id,
        )
    except MetricDashboardRunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except MetricDashboardUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except MetricDashboardDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return MetricDashboardResponse.model_validate(result)
