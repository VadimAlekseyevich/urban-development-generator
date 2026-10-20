"""Regression gates for credential-free, exact-origin API CORS policy."""

from fastapi.testclient import TestClient

from backend.app.core.config import settings
from backend.app.main import app


def test_allowed_dev_origin_preflight_has_no_credential_grant() -> None:
    client = TestClient(app)
    origin = settings.cors_origin_list[0]
    response = client.options(
        "/api/v1/uploads",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers


def test_unconfigured_origin_is_not_reflected_by_preflight() -> None:
    client = TestClient(app)
    for origin in ("https://untrusted.example", "null"):
        response = client.options(
            "/api/v1/uploads",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers
