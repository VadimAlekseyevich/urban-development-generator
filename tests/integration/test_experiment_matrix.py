"""S15 research runner acceptance on the existing S12 PostgreSQL job boundary."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.application.experiment_matrix import (
    ExperimentManifest,
    ExperimentMatrixRunner,
    build_experiment_plan,
)
from backend.app.application.generation import (
    GenerationClaimDisposition,
    GenerationRetryScheduled,
)
from backend.app.db.generation_state import SqlAlchemyGenerationStateStore
from backend.app.db.scenario_batch import ScenarioBatchError, SqlAlchemyScenarioBatchStore
from backend.app.db.session import engine
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.job import Job
from backend.app.models.job_outbox import JobOutbox
from backend.app.models.project import Project
from backend.app.models.scenario_batch import ScenarioBatch, ScenarioBatchRun

SessionFactory: Callable[[], Session] = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    yield
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _project() -> tuple[uuid.UUID, uuid.UUID]:
    with SessionFactory() as session:
        project = Project(
            name="Research territory",
            working_srid=32637,
            boundary_metadata={},
            boundary=WKTElement(
                "MULTIPOLYGON(((0 0, 100 0, 100 100, 0 100, 0 0)))",
                srid=32637,
            ),
        )
        session.add(project)
        session.flush()
        dataset = Dataset(project_id=project.id, kind="roads")
        session.add(dataset)
        session.flush()
        version = DatasetVersion(
            dataset_id=dataset.id,
            version=1,
            status="ready",
            checksum_sha256="b" * 64,
            source_metadata={"source": "experiment-fixture"},
        )
        session.add(version)
        session.commit()
        return project.id, version.id


def _manifest(
    a: tuple[uuid.UUID, uuid.UUID], b: tuple[uuid.UUID, uuid.UUID]
) -> ExperimentManifest:
    return ExperimentManifest.model_validate_json(json.dumps({
        "schema_version": "s15-experiment-matrix-v1",
        "experiment_id": "density-study",
        "territories": [
            {"name": "a", "project_id": str(a[0]), "mode": "EXPANSION",
             "dataset_version_ids": [str(a[1])]},
            {"name": "b", "project_id": str(b[0]), "mode": "FROM_SCRATCH",
             "dataset_version_ids": [str(b[1])]},
        ],
        "seeds": [19, 7],
        "cells": [
            {"config_name": "low", "ablation_name": "full",
             "config_json": {"density": 1}},
            {"config_name": "high", "ablation_name": "full",
             "config_json": {"density": 3}},
        ],
        "config_schema_version": "fixture-v1",
        "commit_sha": "a" * 40,
        "concurrency_limit": 1,
    }))


def _counts() -> tuple[int, ...]:
    with SessionFactory() as session:
        return tuple(int(session.scalar(select(func.count()).select_from(model)) or 0)
                     for model in (
                         ScenarioBatch, ScenarioBatchRun,
                         GenerationRun, Job, JobOutbox,
                     ))


def test_two_territories_use_distinct_normal_batches_and_worker_claims() -> None:
    a, b = _project(), _project()
    manifest = _manifest(a, b)
    plan = build_experiment_plan(manifest)
    runner = ExperimentMatrixRunner(
        batch_store=SqlAlchemyScenarioBatchStore(session_factory=SessionFactory)
    )
    first = runner.submit_territory(manifest=manifest, territory_name="a")
    assert _counts() == (1, 4, 4, 4, 4)
    second = runner.submit_territory(manifest=manifest, territory_name="b")
    assert _counts() == (2, 8, 8, 8, 8)
    assert first.plan_sha256 == second.plan_sha256 == plan.plan_sha256
    assert first.batch_id != second.batch_id
    assert {row["run_id"] for row in first.runs}.isdisjoint(
        row["run_id"] for row in second.runs
    )

    with SessionFactory() as session:
        for territory, receipt in (("a", first), ("b", second)):
            territory_plan = next(t for t in plan.territories if t.name == territory)
            batch = session.get(ScenarioBatch, uuid.UUID(receipt.batch_id))
            assert batch is not None and batch.status == "queued"
            assert batch.concurrency_limit == 1
            members = session.scalars(
                select(ScenarioBatchRun)
                .where(ScenarioBatchRun.batch_id == batch.id)
                .order_by(ScenarioBatchRun.position)
            ).all()
            assert [str(item.run_id) for item in members] == [
                item["run_id"] for item in receipt.runs
            ]
            for input_row, receipt_row in zip(
                territory_plan.runs, receipt.runs, strict=True
            ):
                run = session.get(GenerationRun, uuid.UUID(str(receipt_row["run_id"])))
                assert run is not None and run.status == "queued"
                assert (run.mode, run.seed, run.config_json) == (
                    territory_plan.mode, input_row.seed, input_row.config_json
                )
                assert [str(v.id) for v in run.dataset_versions] == list(
                    territory_plan.dataset_version_ids
                )
                job = session.scalar(select(Job).where(Job.run_id == run.id))
                assert job is not None and job.status == "queued"
                assert job.idempotency_key == f"run:{run.id}"
                outbox = session.scalar(
                    select(JobOutbox).where(JobOutbox.job_id == job.id)
                )
                assert outbox is not None
                assert outbox.status == "pending"
                assert outbox.payload == {
                    "task": "run_generation", "run_id": str(run.id)
                }
                assert outbox.enqueue_key == f"job:{job.id}"

    state = SqlAlchemyGenerationStateStore(session_factory=SessionFactory)
    first_run = uuid.UUID(str(first.runs[0]["run_id"]))
    next_run = uuid.UUID(str(first.runs[1]["run_id"]))
    assert state.claim(run_id=first_run) is GenerationClaimDisposition.STARTED
    with pytest.raises(GenerationRetryScheduled):
        state.claim(run_id=next_run)
    assert _counts() == (2, 8, 8, 8, 8)


def test_foreign_version_refuses_one_territory_without_partial_jobs() -> None:
    a, b = _project(), _project()
    raw = _manifest(a, b).model_dump(mode="json")
    # In a valid manifest, the schema cannot prove project ownership;
    # the canonical ScenarioBatch DB boundary must check it atomically.
    raw["territories"][0]["dataset_version_ids"] = [str(b[1])]
    manifest = ExperimentManifest.model_validate_json(json.dumps(raw))
    runner = ExperimentMatrixRunner(
        batch_store=SqlAlchemyScenarioBatchStore(session_factory=SessionFactory)
    )
    assert _counts() == (0, 0, 0, 0, 0)
    with pytest.raises(ScenarioBatchError, match="project-owned"):
        runner.submit_territory(manifest=manifest, territory_name="a")
    assert _counts() == (0, 0, 0, 0, 0)
    succeeded = runner.submit_territory(manifest=manifest, territory_name="b")
    assert succeeded.territory_name == "b"
    assert _counts() == (1, 4, 4, 4, 4)
