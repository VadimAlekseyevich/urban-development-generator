"""Compare persisted evaluation and canonical validation, without GIS recomputation."""

import uuid

from fastapi import APIRouter, HTTPException, status

from backend.app.api.dependencies import RunCompareServiceDep
from backend.app.application.run_compare import (
    RunCompareDataError,
    RunCompareProjectNotFoundError,
    RunCompareQueryError,
    RunCompareRunNotFoundError,
    RunCompareUnavailableError,
)
from backend.app.schemas.run_compare import RunCompareRequest, RunCompareResponse

router = APIRouter(tags=["compare"])


@router.post(
    "/projects/{project_id}/compare",
    response_model=RunCompareResponse,
)
def compare_runs(
    project_id: uuid.UUID,
    request: RunCompareRequest,
    service: RunCompareServiceDep,
) -> RunCompareResponse:
    try:
        result = service.compare(
            project_id=project_id,
            run_ids=tuple(request.run_ids),
        )
    except (RunCompareProjectNotFoundError, RunCompareRunNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except RunCompareQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except RunCompareUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except RunCompareDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    return RunCompareResponse.model_validate(result)
