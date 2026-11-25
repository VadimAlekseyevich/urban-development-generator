"""Build identity is supplied by the backend image, not by the client."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core import generation_revision
from backend.app.main import app


def test_labelled_build_requires_exact_revision(tmp_path: Path, monkeypatch) -> None:
    marker = tmp_path / "revision"
    marker.write_text("a" * 40 + "\n", encoding="ascii")
    monkeypatch.setattr(generation_revision, "_REVISION_FILE", marker)
    assert generation_revision.build_revision() == "a" * 40
    generation_revision.require_generation_revision("a" * 40)
    for requested in ("b" * 40, None):
        with pytest.raises(generation_revision.GenerationRevisionError, match="does not match"):
            generation_revision.require_generation_revision(requested)
    response = TestClient(app).get("/api/v1/generation/revision")
    assert response.status_code == 200
    assert response.json()["commit_sha"] == "a" * 40


def test_production_missing_revision_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(generation_revision, "_REVISION_FILE", tmp_path / "missing")
    monkeypatch.setattr(generation_revision.settings, "app_env", "production")
    assert generation_revision.build_revision() is None
    with pytest.raises(generation_revision.GenerationRevisionError, match="requires"):
        generation_revision.require_generation_revision("a" * 40)
    response = TestClient(app).get("/api/v1/generation/revision")
    assert response.json() == {"commit_sha": None, "required": True}


def test_invalid_marker_is_not_treated_as_unlabelled(tmp_path: Path, monkeypatch) -> None:
    marker = tmp_path / "revision"
    marker.write_text("not-a-sha", encoding="ascii")
    monkeypatch.setattr(generation_revision, "_REVISION_FILE", marker)
    with pytest.raises(generation_revision.GenerationRevisionError, match="invalid"):
        generation_revision.require_generation_revision("a" * 40)
    assert TestClient(app).get("/api/v1/generation/revision").status_code == 503
