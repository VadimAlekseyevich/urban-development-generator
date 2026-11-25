"""Version browsing must remain project-scoped and read-only."""

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.app.api.dependencies import get_dataset_version_query_service
from backend.app.application.dataset_versions import (
    DatasetVersionPage,
    DatasetVersionQueryService,
    DatasetVersionSummary,
)
from backend.app.main import app


class FakeDatasetVersionReader:
    def __init__(self, project_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.versions = (
            DatasetVersionSummary(
                id=uuid.uuid4(),
                dataset_id=uuid.uuid4(),
                dataset_kind="roads",
                version=2,
                status="ready",
                checksum_sha256="a" * 64,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            DatasetVersionSummary(
                id=uuid.uuid4(),
                dataset_id=uuid.uuid4(),
                dataset_kind="buildings",
                version=1,
                status="processing",
                checksum_sha256=None,
                created_at=datetime(2025, 1, 1, tzinfo=UTC),
            ),
        )

    def list_versions(
        self, *, project_id: uuid.UUID, limit: int, offset: int
    ) -> DatasetVersionPage | None:
        if project_id != self.project_id:
            return None
        page = self.versions[offset : offset + limit]
        return DatasetVersionPage(
            project_id=project_id,
            limit=limit,
            offset=offset,
            truncated=offset + limit < len(self.versions),
            versions=page,
        )


def test_dataset_version_catalog_is_bounded_and_project_scoped() -> None:
    project_id = uuid.uuid4()
    reader = FakeDatasetVersionReader(project_id)
    service = DatasetVersionQueryService(reader)
    app.dependency_overrides[get_dataset_version_query_service] = lambda: service
    try:
        client = TestClient(app)
        path = f"/api/v1/projects/{project_id}/dataset-versions"
        first = client.get(path + "?limit=1")
        assert first.status_code == 200
        assert first.json()["truncated"] is True
        assert len(first.json()["versions"]) == 1
        assert first.json()["versions"][0]["dataset_kind"] == "roads"
        second = client.get(path + "?limit=1&offset=1")
        assert second.status_code == 200
        assert second.json()["versions"][0]["status"] == "processing"
        assert second.json()["truncated"] is False
        assert client.get(path + "?limit=51").status_code == 422
        assert client.get(path + "?offset=-1").status_code == 422
        missing = client.get(
            f"/api/v1/projects/{uuid.uuid4()}/dataset-versions"
        )
        assert missing.status_code == 404
    finally:
        app.dependency_overrides.pop(get_dataset_version_query_service, None)
