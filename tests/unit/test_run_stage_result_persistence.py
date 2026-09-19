import uuid

from sqlalchemy import CheckConstraint, UniqueConstraint

from backend.app.models.generation_run import GenerationRun
from backend.app.models.run_stage_result import RunStageResult


def _make_stage_result() -> RunStageResult:
    return RunStageResult(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        stage_name="roads",
        stage_version="1.0.0",
        status="running",
        progress_percent=40,
        input_hash=f"sha256:{'a' * 64}",
        config_hash=f"sha256:{'b' * 64}",
        output_fingerprint=None,
        diagnostics_json=[
            {"code": "roads.seeded", "message": "seed initialized", "level": "INFO"}
        ],
        artifact_refs_json=["artifact:temporary:roads"],
        started_at=None,
        finished_at=None,
    )


def test_run_stage_result_persists_stage_execution_contract() -> None:
    columns = RunStageResult.__table__.c

    for field_name in {
        "id",
        "run_id",
        "stage_name",
        "stage_version",
        "status",
        "progress_percent",
        "input_hash",
        "config_hash",
        "output_fingerprint",
        "diagnostics_json",
        "artifact_refs_json",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    }:
        assert field_name in columns

    assert columns.run_id.nullable is False
    assert columns.stage_name.nullable is False
    assert columns.stage_version.nullable is False
    assert columns.input_hash.type.length == 71
    assert columns.config_hash.type.length == 71
    assert columns.output_fingerprint.type.length == 71
    assert columns.output_fingerprint.nullable is True


def test_run_stage_result_references_generation_run_with_cascade_delete() -> None:
    run_id_column = RunStageResult.__table__.c.run_id
    run_fk = next(iter(run_id_column.foreign_keys))

    assert run_fk.target_fullname == "generation_runs.id"
    assert run_fk.ondelete == "CASCADE"


def test_run_stage_result_has_one_row_per_stage_per_run() -> None:
    unique_constraints = {
        constraint.name
        for constraint in RunStageResult.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert "uq_run_stage_results_run_stage" in unique_constraints


def test_run_stage_result_constraints_cover_identity_progress_hashes_and_json() -> None:
    constraint_names = {
        constraint.name
        for constraint in RunStageResult.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "ck_run_stage_results_stage_name",
        "ck_run_stage_results_stage_version",
        "ck_run_stage_results_status",
        "ck_run_stage_results_progress_percent",
        "ck_run_stage_results_success_progress",
        "ck_run_stage_results_input_hash",
        "ck_run_stage_results_config_hash",
        "ck_run_stage_results_output_fingerprint",
        "ck_run_stage_results_diagnostics_array",
        "ck_run_stage_results_artifact_refs_array",
    } <= constraint_names


def test_stage_result_keeps_diagnostics_and_artifact_refs_without_artifact_fk() -> None:
    result = _make_stage_result()

    assert result.diagnostics_json[0]["code"] == "roads.seeded"
    assert result.artifact_refs_json == ["artifact:temporary:roads"]
    assert not RunStageResult.__table__.c.artifact_refs_json.foreign_keys


def test_generation_run_owns_stage_results_relationship() -> None:
    result = _make_stage_result()
    run = GenerationRun(
        id=result.run_id,
        project_id=uuid.uuid4(),
        status="running",
        mode="EXPANSION",
        seed=42,
        working_srid=32637,
        config_json={"target_population": 10_000},
        config_schema_version="v1",
        commit_sha="c" * 40,
        metrics_json=None,
        error_json=None,
        started_at=None,
        finished_at=None,
    )

    run.stage_results.append(result)

    assert run.stage_results == [result]
    assert result.run is run



def test_output_fingerprint_is_distinct_from_execution_input_and_config_hashes() -> None:
    result = _make_stage_result()
    result.output_fingerprint = f"sha256:{'c' * 64}"

    assert result.input_hash == f"sha256:{'a' * 64}"
    assert result.config_hash == f"sha256:{'b' * 64}"
    assert result.output_fingerprint == f"sha256:{'c' * 64}"
