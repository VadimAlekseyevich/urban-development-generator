from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from backend.app.api.v1.endpoints import health
from backend.app.db.session import get_db
from backend.app.main import app


class FakeSession:
    def execute(self, _statement: object) -> None:
        return None


def _override_db() -> Generator[FakeSession, None, None]:
    yield FakeSession()


def test_liveness() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_checks_database_and_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr(health, "_probe_redis", lambda: None)

    try:
        client = TestClient(app)
        response = client.get(
            "/api/v1/health/ready",
            headers={"x-request-id": "request-1", "x-correlation-id": "correlation-1"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "redis": "ok"}
    assert response.headers["x-request-id"] == "request-1"
    assert response.headers["x-correlation-id"] == "correlation-1"
