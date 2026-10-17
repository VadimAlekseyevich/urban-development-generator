"""Production-schema acceptance for protected Prometheus operational metrics."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.api.v1.endpoints import operational_metrics
from backend.app.core.config import Settings, settings
from backend.app.db.session import engine
from backend.app.main import app
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from backend.app.models.run_stage_result import RunStageResult

URL = "/internal/metrics"
TOKEN = "s14-metrics-test-token-longer-than-32-characters"
QUEUE_DEPTHS = {
    "ingest": 3, "generation": 1, "analysis": 0, "export": 2, "legacy": 0,
}

# Save real reader before the autouse fixture stubs scrape dependencies.
_READ_REAL_REDIS_DEPTHS = operational_metrics._redis_queue_depths


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None, monkeypatch: pytest.MonkeyPatch) -> None:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    monkeypatch.setattr(settings, "metrics_bearer_token", None)
    monkeypatch.setattr(operational_metrics, "_redis_queue_depths", lambda: QUEUE_DEPTHS)
    yield
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _seed() -> tuple[uuid.UUID, uuid.UUID]:
    now = datetime.now(UTC)
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="Private monitoring contract", working_srid=3857,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            run = GenerationRun(
                project_id=project.id, status="running", mode="EXPANSION",
                seed=41, working_srid=3857, config_json={},
                config_schema_version="1",
            )
            session.add(run)
            session.flush()
            session.add_all((
                Job(
                    project_id=project.id, run_id=run.id,
                    job_type="generation_run", idempotency_key="run-1",
                    status="queued",
                ),
                Job(
                    project_id=project.id, job_type="geojson_export",
                    idempotency_key="export-1", status="failed",
                ),
                Job(
                    project_id=project.id, job_type="private_new_job_kind",
                    idempotency_key="unknown-1", status="running",
                ),
                RunStageResult(
                    run_id=run.id, stage_name="roads", stage_version="1.0.0",
                    status="succeeded", progress_percent=100,
                    input_hash="sha256:" + "a" * 64,
                    config_hash="sha256:" + "b" * 64,
                    started_at=now - timedelta(seconds=3),
                    finished_at=now,
                ),
            ))
            session.flush()
            queued = session.query(Job).filter_by(idempotency_key="run-1").one()
            session.add(
                JobOutbox(
                    job_id=queued.id, queue_name="generation",
                    payload={"task": "run_generation", "run_id": str(run.id)},
                    status="pending", delivery_attempts=1,
                )
            )
            return project.id, run.id


def test_scrape_disabled_by_default_even_with_populated_database() -> None:
    _seed()
    response = TestClient(app).get(URL)
    assert response.status_code == 404
    assert "udg_jobs" not in response.text


def test_scrape_requires_separate_token_and_never_echoes_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "metrics_bearer_token", SecretStr(TOKEN))
    unauthenticated = TestClient(app).get(URL)
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["www-authenticate"] == "Bearer"
    denied = TestClient(app).get(URL, headers={"Authorization": "Bearer invalid"})
    assert denied.status_code == 401
    assert TOKEN not in denied.text


def test_authorized_scrape_exposes_persisted_gauges_and_fixed_label_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, run_id = _seed()
    monkeypatch.setattr(settings, "metrics_bearer_token", SecretStr(TOKEN))
    client = TestClient(app)
    response = client.get(URL, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    body = response.text
    assert '# TYPE udg_jobs gauge' in body
    assert 'udg_jobs{type="generation_run",status="queued"} 1' in body
    assert 'udg_jobs{type="geojson_export",status="failed"} 1' in body
    assert 'udg_outbox_messages{queue="generation",status="pending"} 1' in body
    assert 'udg_stages{stage="roads",status="succeeded"} 1' in body
    assert 'udg_stage_duration_seconds_sum{stage="roads"} 3.0' in body
    assert 'udg_stage_duration_samples{stage="roads"} 1' in body
    assert 'udg_arq_queue_depth{queue="ingest"} 3' in body
    assert "udg_outbox_due 1" in body
    assert "udg_outbox_retries 1" in body
    assert "udg_oldest_queued_job_age_seconds " in body
    assert "private_new_job_kind" not in body
    assert str(project_id) not in body
    assert str(run_id) not in body
    assert TOKEN not in body
    assert URL not in client.get("/openapi.json").text


def test_scrape_redis_failure_is_503_without_partial_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "metrics_bearer_token", SecretStr(TOKEN))

    def redis_unavailable() -> dict[str, int]:
        raise RedisError("mock redis failure")

    monkeypatch.setattr(operational_metrics, "_redis_queue_depths", redis_unavailable)
    response = TestClient(app).get(URL, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 503
    assert "udg_jobs" not in response.text


def test_metrics_token_cannot_be_accidentally_short() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(metrics_bearer_token="short")


def test_redis_depth_reader_uses_arq_sorted_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePipeline:
        def __init__(self) -> None:
            self.queues: list[str] = []

        def zcard(self, name: str) -> None:
            self.queues.append(name)

        def execute(self) -> list[int]:
            return [3, 1, 0, 2, 0]

    class FakeRedis:
        def __init__(self) -> None:
            self.pipeline_instance = FakePipeline()
            self.closed = False

        def pipeline(self, *, transaction: bool) -> FakePipeline:
            assert transaction is False
            return self.pipeline_instance

        def close(self) -> None:
            self.closed = True

    fake = FakeRedis()
    monkeypatch.setattr(
        operational_metrics.Redis, "from_url",
        lambda *_args, **_kwargs: fake,
    )
    assert _READ_REAL_REDIS_DEPTHS() == QUEUE_DEPTHS
    assert fake.pipeline_instance.queues == [
        "arq:queue:ingest", "arq:queue:generation",
        "arq:queue:analysis", "arq:queue:export", "arq:queue",
    ]
    assert fake.closed is True
