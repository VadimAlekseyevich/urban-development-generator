import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import CheckConstraint
from sqlalchemy.orm.attributes import set_committed_value

from backend.app.models.dataset import DatasetVersion
from backend.app.models.generation_run import (
    IMMUTABLE_SUCCEEDED_RUN_FIELDS,
    RUN_SUCCESS_STATUS,
    GenerationRun,
    GenerationRunImmutableError,
    generation_run_dataset_versions,
    prevent_succeeded_generation_run_update,
)


def _make_run(*, status: str = "queued") -> GenerationRun:
    return GenerationRun(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status=status,
        mode="EXPANSION",
        seed=42,
        working_srid=32637,
        config_json={"target_population": 10_000},
        config_schema_version="v1",
        commit_sha="a" * 40,
        metrics_json=None,
        validation_json=None,
        error_json=None,
        started_at=None,
        finished_at=None,
    )


def _mark_run_committed(run: GenerationRun) -> None:
    for field_name in IMMUTABLE_SUCCEEDED_RUN_FIELDS:
        set_committed_value(run, field_name, getattr(run, field_name))


def _make_dataset_version() -> DatasetVersion:
    return DatasetVersion(
        id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        version=1,
        checksum_sha256="b" * 64,
        status="ready",
        source_metadata={"source": "roads.geojson"},
    )


def test_generation_run_persists_reproducibility_inputs() -> None:
    columns = GenerationRun.__table__.c

    for field_name in {
        "id",
        "project_id",
        "status",
        "mode",
        "seed",
        "working_srid",
        "config_json",
        "config_schema_version",
        "commit_sha",
        "metrics_json",
        "validation_json",
        "started_at",
        "finished_at",
        "created_at",
    }:
        assert field_name in columns

    assert columns.mode.nullable is False
    assert columns.working_srid.nullable is False
    assert columns.config_schema_version.nullable is False
    assert columns.commit_sha.type.length == 40
    assert columns.validation_json.nullable is True
    assert "validation_json" in IMMUTABLE_SUCCEEDED_RUN_FIELDS
    assert "code_version" not in columns


def test_generation_run_has_dataset_version_reference_table() -> None:
    columns = generation_run_dataset_versions.c

    assert set(columns.keys()) == {"run_id", "dataset_version_id"}
    assert columns.run_id.primary_key is True
    assert columns.dataset_version_id.primary_key is True

    dataset_version_fk = next(iter(columns.dataset_version_id.foreign_keys))
    assert dataset_version_fk.target_fullname == "dataset_versions.id"
    assert dataset_version_fk.ondelete == "RESTRICT"


def test_generation_run_constraints_cover_mode_crs_schema_and_commit() -> None:
    constraint_names = {
        constraint.name
        for constraint in GenerationRun.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "ck_generation_runs_mode",
        "ck_generation_runs_working_srid_positive",
        "ck_generation_runs_config_schema_version_nonempty",
        "ck_generation_runs_commit_sha",
        "ck_generation_runs_success_commit_sha",
        "ck_generation_runs_validation_json_object",
    } <= constraint_names


def test_successful_generation_run_rejects_scalar_mutation() -> None:
    run = _make_run(status=RUN_SUCCESS_STATUS)
    _mark_run_committed(run)

    run.seed = 43

    with pytest.raises(GenerationRunImmutableError, match="seed"):
        prevent_succeeded_generation_run_update(object(), object(), run)


def test_run_can_be_finalized_once_before_becoming_immutable() -> None:
    run = _make_run(status="running")
    _mark_run_committed(run)

    run.status = RUN_SUCCESS_STATUS
    run.finished_at = datetime.now(UTC)
    run.metrics_json = {"score": 0.8}
    run.validation_json = {"schema_version": 2, "results": []}

    prevent_succeeded_generation_run_update(object(), object(), run)

    _mark_run_committed(run)
    run.status = "failed"

    with pytest.raises(GenerationRunImmutableError, match="status"):
        prevent_succeeded_generation_run_update(object(), object(), run)


def test_successful_generation_run_rejects_dataset_ref_changes() -> None:
    run = _make_run(status=RUN_SUCCESS_STATUS)

    with pytest.raises(GenerationRunImmutableError, match="dataset refs"):
        run.dataset_versions.append(_make_dataset_version())


def test_non_successful_generation_run_can_collect_dataset_refs() -> None:
    run = _make_run(status="queued")
    version = _make_dataset_version()

    run.dataset_versions.append(version)

    assert run.dataset_versions == [version]
