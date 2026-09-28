"""S12 run control: only existing DB-authoritative lifecycle and outbox writes."""

from __future__ import annotations

import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.api.dependencies import RunControlServiceDep
from backend.app.db.run_control import RunControlConflict, RunControlNotFound
from backend.app.schemas.run_control import RunCreate, RunListRead, RunStateRead

router = APIRouter(prefix="/projects/{project_id}/runs", tags=["runs"])


def _raise_run_error(exc: RunControlNotFound | RunControlConflict) -> NoReturn:
    if isinstance(exc, RunControlNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("", response_model=RunListRead)
def list_runs(
    project_id: uuid.UUID,
    service: RunControlServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> RunListRead:
    try:
        return service.list(project_id=project_id, limit=limit)
    except (RunControlNotFound, RunControlConflict) as exc:
        _raise_run_error(exc)


@router.post("", response_model=RunStateRead, status_code=status.HTTP_201_CREATED)
def create_run(
    project_id: uuid.UUID,
    payload: RunCreate,
    service: RunControlServiceDep,
) -> RunStateRead:
    try:
        return service.create(
            project_id=project_id,
            mode=payload.mode,
            seed=payload.seed,
            dataset_version_ids=tuple(payload.dataset_version_ids),
            config_json=payload.config_json,
            config_schema_version=payload.config_schema_version,
            commit_sha=payload.commit_sha,
        )
    except (RunControlNotFound, RunControlConflict) as exc:
        _raise_run_error(exc)


@router.get("/{run_id}", response_model=RunStateRead)
def get_run(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: RunControlServiceDep,
) -> RunStateRead:
    try:
        return service.get(project_id=project_id, run_id=run_id)
    except (RunControlNotFound, RunControlConflict) as exc:
        _raise_run_error(exc)


@router.post("/{run_id}/cancel", response_model=RunStateRead)
def cancel_run(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: RunControlServiceDep,
) -> RunStateRead:
    try:
        return service.cancel(project_id=project_id, run_id=run_id)
    except (RunControlNotFound, RunControlConflict) as exc:
        _raise_run_error(exc)


@router.post(
    "/{run_id}/retry", response_model=RunStateRead, status_code=status.HTTP_201_CREATED
)
def retry_run(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    service: RunControlServiceDep,
) -> RunStateRead:
    try:
        return service.retry(project_id=project_id, run_id=run_id)
    except (RunControlNotFound, RunControlConflict) as exc:
        _raise_run_error(exc)
