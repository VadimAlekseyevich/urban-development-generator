import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Query, Response, status

from backend.app.api.dependencies import ProjectServiceDep
from backend.app.application.projects import CreateProjectCommand, ProjectNotFoundError
from backend.app.models.project import Project
from backend.app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


def _raise_project_not_found(exc: ProjectNotFoundError) -> NoReturn:
    raise HTTPException(status_code=404, detail="Project not found") from exc


@router.get("", response_model=list[ProjectRead])
def list_projects(
    service: ProjectServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Project]:
    return service.list_projects(limit=limit, offset=offset)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, service: ProjectServiceDep) -> Project:
    command = CreateProjectCommand(
        name=payload.name,
        description=payload.description,
        working_srid=payload.working_srid,
        boundary_metadata=payload.boundary_metadata.model_dump(),
    )
    return service.create_project(command)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: uuid.UUID, service: ProjectServiceDep) -> Project:
    try:
        return service.get_project(project_id)
    except ProjectNotFoundError as exc:
        _raise_project_not_found(exc)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    service: ProjectServiceDep,
) -> Project:
    try:
        return service.update_project(
            project_id,
            payload.model_dump(exclude_unset=True),
        )
    except ProjectNotFoundError as exc:
        _raise_project_not_found(exc)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: uuid.UUID, service: ProjectServiceDep) -> Response:
    try:
        service.delete_project(project_id)
    except ProjectNotFoundError as exc:
        _raise_project_not_found(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
