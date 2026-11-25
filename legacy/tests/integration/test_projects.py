import uuid
from collections.abc import Generator
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models.project import Project


class FakeScalarResult:
    def __init__(self, projects: list[Project]) -> None:
        self._projects = projects

    def all(self) -> list[Project]:
        return list(self._projects)


class FakeProjectSession:
    def __init__(self) -> None:
        self.projects: dict[uuid.UUID, Project] = {}

    def add(self, project: Project) -> None:
        now = datetime.now(UTC)
        project.id = uuid.uuid4()
        project.created_at = now
        project.updated_at = now
        self.projects[project.id] = project

    def get(self, _model: type[Project], project_id: uuid.UUID) -> Project | None:
        return self.projects.get(project_id)

    def scalars(self, _statement: object) -> FakeScalarResult:
        return FakeScalarResult(list(self.projects.values()))

    def commit(self) -> None:
        return None

    def refresh(self, project: Project) -> None:
        project.updated_at = datetime.now(UTC)

    def delete(self, project: Project) -> None:
        self.projects.pop(project.id, None)


def test_project_crud_api_uses_explicit_working_srid() -> None:
    fake_db = FakeProjectSession()

    def override_db() -> Generator[FakeProjectSession, None, None]:
        yield fake_db

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)

        created = client.post(
            "/api/v1/projects",
            json={
                "name": "Demo project",
                "working_srid": 32637,
                "boundary_metadata": {
                    "source_srid": 4326,
                    "geometry_type": "MULTIPOLYGON",
                    "feature_count": 1,
                },
            },
        )
        assert created.status_code == 201
        project_id = created.json()["id"]
        assert created.json()["working_srid"] == 32637

        fetched = client.get(f"/api/v1/projects/{project_id}")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "Demo project"

        updated = client.patch(
            f"/api/v1/projects/{project_id}",
            json={"name": "Updated project", "working_srid": 32636},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Updated project"
        assert updated.json()["working_srid"] == 32636

        listed = client.get("/api/v1/projects?limit=10&offset=0")
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        deleted = client.delete(f"/api/v1/projects/{project_id}")
        assert deleted.status_code == 204
        assert client.get(f"/api/v1/projects/{project_id}").status_code == 404

        invalid = client.post(
            "/api/v1/projects",
            json={"name": "Geographic project", "working_srid": 4326},
        )
        assert invalid.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
