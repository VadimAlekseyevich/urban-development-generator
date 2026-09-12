import uuid

from sqlalchemy import CheckConstraint, UniqueConstraint

from backend.app.models.job import Job
from core.urban_generator.domain.errors import CancelledError, TransientError


def _make_job() -> Job:
    return Job(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        job_type="generation_run",
        idempotency_key="run:42",
        status="queued",
        attempt_count=0,
        max_attempts=3,
        error_class=None,
        error_code=None,
        error_json=None,
        started_at=None,
        finished_at=None,
    )


def test_job_persists_authoritative_state() -> None:
    columns = Job.__table__.c
    for field_name in {
        "id",
        "project_id",
        "run_id",
        "job_type",
        "idempotency_key",
        "status",
        "attempt_count",
        "max_attempts",
        "error_class",
        "error_code",
        "error_json",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    }:
        assert field_name in columns


def test_job_idempotency_is_unique_per_project_and_job_type() -> None:
    unique_constraints = {
        constraint.name
        for constraint in Job.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_jobs_project_type_idempotency" in unique_constraints


def test_job_constraints_cover_status_attempts_and_error_class() -> None:
    constraint_names = {
        constraint.name
        for constraint in Job.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_jobs_job_type",
        "ck_jobs_idempotency_key_nonempty",
        "ck_jobs_status",
        "ck_jobs_attempt_count_nonnegative",
        "ck_jobs_max_attempts_positive",
        "ck_jobs_attempts_within_limit",
        "ck_jobs_error_class",
        "ck_jobs_error_code_nonempty",
    } <= constraint_names


def test_job_references_project_and_optional_generation_run() -> None:
    project_fk = next(iter(Job.__table__.c.project_id.foreign_keys))
    run_fk = next(iter(Job.__table__.c.run_id.foreign_keys))
    assert project_fk.target_fullname == "projects.id"
    assert project_fk.ondelete == "CASCADE"
    assert run_fk.target_fullname == "generation_runs.id"
    assert run_fk.ondelete == "CASCADE"


def test_job_failure_uses_stable_error_taxonomy() -> None:
    job = _make_job()
    job.record_failure(TransientError("redis unavailable", details={"queue": "generation"}))
    assert job.status == "failed"
    assert job.error_class == "transient"
    assert job.error_code == "transient.error"
    assert job.error_json == {
        "message": "redis unavailable",
        "details": {"queue": "generation"},
        "retryable": True,
        "cancelled": False,
    }


def test_cancelled_error_sets_cancelled_job_state() -> None:
    job = _make_job()
    job.record_failure(CancelledError("user cancelled"))
    assert job.status == "cancelled"
    assert job.error_class == "cancelled"
    assert job.error_code == "cancelled.error"
