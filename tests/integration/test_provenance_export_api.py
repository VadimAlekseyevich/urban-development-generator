"""PostgreSQL/API acceptance for canonical successful-run provenance export."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.run_metrics_writer import composite_score_payload
from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain.benchmarking import RawMetricId
from core.urban_generator.metrics.score import (
    CompositeScoreMetricResult,
    CompositeScoreResult,
)

WORKING_SRID = 32637


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _evaluation() -> dict[str, object]:
    result = CompositeScoreResult(
        score=0.4,
        score_config_id="provenance-export",
        score_config_version="1",
        normalization_profile_id="fixture",
        normalization_profile_version="1",
        metrics=(
            CompositeScoreMetricResult(
                metric_id=RawMetricId.LAND_DEVELOPED_AREA_M2,
                raw_value=250.0,
                normalized_value=0.4,
                normalization_policy_version="1",
                configured_weight=1.0,
                normalized_weight=1.0,
                contribution=0.4,
                was_clamped=False,
                was_missing=False,
            ),
        ),
    )
    return {"evaluation": composite_score_payload(result)}


def _project(name: str) -> uuid.UUID:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name=name,
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            return project.id


def _valid_run(project_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            dataset = Dataset(project_id=project_id, kind="roads")
            session.add(dataset)
            session.flush()
            version = DatasetVersion(
                dataset_id=dataset.id,
                version=1,
                status="ready",
                checksum_sha256="a" * 64,
                source_metadata={"artifact_key": f"uploads/{dataset.id.hex}/roads.geojson"},
            )
            session.add(version)
            session.flush()
            session.add(
                Artifact(
                    uri=f"artifact://uploads/{dataset.id.hex}/roads.geojson",
                    checksum=f"sha256:{'a' * 64}",
                    size_bytes=123,
                    content_type="application/geo+json",
                    state=ArtifactLifecycleState.REFERENCED.value,
                    owner_type="dataset_version",
                    owner_id=version.id,
                )
            )
            run = GenerationRun(
                project_id=project_id,
                status="running",
                mode="EXPANSION",
                seed=42,
                working_srid=WORKING_SRID,
                config_json={"density": {"target": 1.25}, "label": "тест"},
                config_schema_version="fixture-v1",
                commit_sha="c" * 40,
                dataset_versions=[version],
                metrics_json=_evaluation(),
            )
            session.add(run)
            session.flush()
            session.add(
                RunStageResult(
                    run_id=run.id,
                    stage_name="roads",
                    stage_version="1",
                    status="succeeded",
                    progress_percent=100,
                    input_hash=f"sha256:{'d' * 64}",
                    config_hash=f"sha256:{'e' * 64}",
                    output_fingerprint=f"sha256:{'f' * 64}",
                    diagnostics_json=[],
                    artifact_refs_json=[],
                )
            )
            session.flush()
            run.status = "succeeded"
            return run.id, version.id


def _plain_run(
    project_id: uuid.UUID,
    *,
    status: str,
) -> uuid.UUID:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            run = GenerationRun(
                project_id=project_id,
                status="running",
                mode="EXPANSION",
                seed=7,
                working_srid=WORKING_SRID,
                config_json={},
                config_schema_version="1",
                commit_sha="b" * 40,
                metrics_json=_evaluation(),
            )
            session.add(run)
            session.flush()
            run.status = status
            return run.id


def _export(project_id: uuid.UUID, run_id: uuid.UUID):
    return TestClient(app).get(
        f"/api/v1/projects/{project_id}/exports/provenance/{run_id}"
    )


def test_provenance_export_returns_exact_canonical_reproducibility_document() -> None:
    project_id = _project("S13 provenance export fixture")
    run_id, dataset_version_id = _valid_run(project_id)

    response = _export(project_id, run_id)
    replay = _export(project_id, run_id)

    assert response.status_code == 200, response.text
    assert response.content == replay.content
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-provenance-schema"] == "1"
    expected_checksum = "sha256:" + hashlib.sha256(response.content).hexdigest()
    assert response.headers["x-provenance-checksum"] == expected_checksum
    assert response.headers["content-disposition"] == (
        f'attachment; filename="provenance-{run_id}.json"'
    )

    payload = json.loads(response.content)
    assert payload["run"]["id"] == str(run_id)
    assert payload["run"]["project_id"] == str(project_id)
    assert payload["run"]["seed"] == 42
    assert payload["run"]["working_srid"] == WORKING_SRID
    assert payload["config"]["schema_version"] == "fixture-v1"
    assert payload["config"]["value"] == {
        "density": {"target": 1.25},
        "label": "тест",
    }
    assert payload["datasets"][0]["dataset_version_id"] == str(dataset_version_id)
    assert payload["code"]["commit_sha"] == "c" * 40
    assert payload["stages"][0]["output_fingerprint"] == f"sha256:{'f' * 64}"


def test_provenance_export_enforces_project_success_and_canonical_manifest() -> None:
    project_id = _project("S13 provenance scope")
    other_project = _project("S13 provenance foreign scope")
    good, _ = _valid_run(project_id)
    foreign, _ = _valid_run(other_project)
    pending = _plain_run(project_id, status="running")
    malformed = _plain_run(project_id, status="succeeded")

    assert _export(project_id, good).status_code == 200
    assert _export(project_id, foreign).status_code == 404
    assert _export(uuid.uuid4(), good).status_code == 404
    assert _export(project_id, pending).status_code == 409
    assert _export(project_id, malformed).status_code == 500
    assert _export(project_id, uuid.uuid4()).status_code == 404
