import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from backend.app.models.project import Project


@dataclass(frozen=True, slots=True)
class CreateProjectCommand:
    """Application-level inputs required to create a project."""

    name: str
    description: str | None
    working_srid: int
    boundary_metadata: dict[str, object]


class ProjectRepository(Protocol):
    """Persistence port used by ProjectService."""

    def list(self, *, limit: int, offset: int) -> list[Project]:
        """Return projects in the repository's canonical list ordering."""
        ...

    def get(self, project_id: uuid.UUID) -> Project | None:
        """Return one project by id."""
        ...

    def create(self, project: Project) -> Project:
        """Persist a new project and return its refreshed state."""
        ...

    def save(self, project: Project) -> Project:
        """Persist changes to an existing project and return refreshed state."""
        ...

    def delete(self, project: Project) -> None:
        """Delete one project."""
        ...


class ProjectNotFoundError(LookupError):
    """Raised when an application operation targets a missing project."""

    def __init__(self, project_id: uuid.UUID) -> None:
        self.project_id = project_id
        super().__init__(f"project not found: {project_id}")


class ProjectService:
    """Project use cases independent from HTTP and SQL query construction."""

    _UPDATE_FIELDS = frozenset(
        {"name", "description", "working_srid", "boundary_metadata"}
    )

    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    def list_projects(self, *, limit: int, offset: int) -> list[Project]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        return self._repository.list(limit=limit, offset=offset)

    def get_project(self, project_id: uuid.UUID) -> Project:
        project = self._repository.get(project_id)
        if project is None:
            raise ProjectNotFoundError(project_id)
        return project

    def create_project(self, command: CreateProjectCommand) -> Project:
        project = Project(
            name=command.name,
            description=command.description,
            working_srid=command.working_srid,
            boundary_metadata=dict(command.boundary_metadata),
        )
        return self._repository.create(project)

    def update_project(
        self,
        project_id: uuid.UUID,
        changes: Mapping[str, object],
    ) -> Project:
        unsupported_fields = set(changes) - self._UPDATE_FIELDS
        if unsupported_fields:
            unsupported = ", ".join(sorted(unsupported_fields))
            raise ValueError(f"unsupported project update fields: {unsupported}")

        project = self.get_project(project_id)
        for field_name, value in changes.items():
            setattr(project, field_name, value)
        return self._repository.save(project)

    def delete_project(self, project_id: uuid.UUID) -> None:
        project = self.get_project(project_id)
        self._repository.delete(project)
