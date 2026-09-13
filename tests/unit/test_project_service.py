import uuid
from datetime import UTC, datetime

import pytest

from backend.app.application.projects import (
    CreateProjectCommand,
    ProjectNotFoundError,
    ProjectService,
)
from backend.app.models.project import Project


class FakeProjectRepository:
    def __init__(self) -> None:
        self.projects: dict[uuid.UUID, Project] = {}

    def list(self, *, limit: int, offset: int) -> list[Project]:
        ordered = sorted(
            self.projects.values(),
            key=lambda project: (project.created_at, project.id),
            reverse=True,
        )
        return ordered[offset : offset + limit]

    def get(self, project_id: uuid.UUID) -> Project | None:
        return self.projects.get(project_id)

    def create(self, project: Project) -> Project:
        now = datetime.now(UTC)
        project.id = uuid.uuid4()
        project.created_at = now
        project.updated_at = now
        self.projects[project.id] = project
        return project

    def save(self, project: Project) -> Project:
        project.updated_at = datetime.now(UTC)
        self.projects[project.id] = project
        return project

    def delete(self, project: Project) -> None:
        self.projects.pop(project.id, None)


def _create(service: ProjectService, name: str = "Project A") -> Project:
    return service.create_project(
        CreateProjectCommand(
            name=name,
            description=None,
            working_srid=32637,
            boundary_metadata={"source_srid": 4326},
        )
    )


def test_project_service_crud_is_testable_without_http_or_db_session() -> None:
    repository = FakeProjectRepository()
    service = ProjectService(repository)

    created = _create(service)
    assert service.get_project(created.id) is created

    updated = service.update_project(
        created.id,
        {"name": "Updated", "description": "Scenario project"},
    )
    assert updated.name == "Updated"
    assert updated.description == "Scenario project"
    assert service.list_projects(limit=10, offset=0) == [updated]

    service.delete_project(created.id)
    with pytest.raises(ProjectNotFoundError):
        service.get_project(created.id)


def test_project_service_rejects_invalid_paging_before_repository_call() -> None:
    service = ProjectService(FakeProjectRepository())

    with pytest.raises(ValueError, match="limit must be positive"):
        service.list_projects(limit=0, offset=0)
    with pytest.raises(ValueError, match="offset must be non-negative"):
        service.list_projects(limit=1, offset=-1)


def test_project_service_rejects_unknown_update_fields() -> None:
    service = ProjectService(FakeProjectRepository())
    created = _create(service)

    with pytest.raises(ValueError, match="unsupported project update fields"):
        service.update_project(created.id, {"datasets": []})
