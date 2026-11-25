import uuid

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from backend.app.models.artifact import (
    Artifact,
    ArtifactLifecycleError,
    ArtifactLifecycleState,
    run_stage_result_artifacts,
)
from backend.app.models.generation_run import GenerationRun
from backend.app.models.run_stage_result import (
    RunStageResult,
    RunStageResultImmutableError,
)


def _make_artifact(*, state: ArtifactLifecycleState) -> Artifact:
    return Artifact(
        id=uuid.uuid4(),
        uri=f"local://artifacts/{uuid.uuid4()}",
        checksum=f"sha256:{'a' * 64}",
        size_bytes=128,
        content_type="application/octet-stream",
        state=state.value,
        owner_type=None,
        owner_id=None,
    )


def _make_stage_result() -> RunStageResult:
    return RunStageResult(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        stage_name="roads",
        stage_version="1.0.0",
        status="running",
        progress_percent=50,
        input_hash=f"sha256:{'b' * 64}",
        config_hash=f"sha256:{'c' * 64}",
        diagnostics_json=[],
        artifact_refs_json=["artifact:legacy:roads"],
        started_at=None,
        finished_at=None,
    )


def test_artifact_persists_storage_metadata_lifecycle_and_owner() -> None:
    columns = Artifact.__table__.c

    for field_name in {
        "id",
        "uri",
        "checksum",
        "size_bytes",
        "content_type",
        "state",
        "owner_type",
        "owner_id",
        "created_at",
        "updated_at",
    }:
        assert field_name in columns

    assert columns.uri.nullable is False
    assert columns.checksum.type.length == 71
    assert columns.size_bytes.nullable is False
    assert columns.state.default.arg == ArtifactLifecycleState.TEMPORARY.value


def test_artifact_constraints_cover_identity_owner_and_lifecycle() -> None:
    constraint_names = {
        constraint.name
        for constraint in Artifact.__table__.constraints
        if isinstance(constraint, (CheckConstraint, UniqueConstraint))
    }

    assert {
        "uq_artifacts_uri",
        "ck_artifacts_uri_nonempty",
        "ck_artifacts_checksum",
        "ck_artifacts_size_nonnegative",
        "ck_artifacts_content_type_nonempty",
        "ck_artifacts_state",
        "ck_artifacts_owner_pair",
        "ck_artifacts_owner_type",
        "ck_artifacts_unowned_pre_reference",
        "ck_artifacts_referenced_owner",
    } <= constraint_names


def test_artifact_lifecycle_requires_ready_before_reference() -> None:
    artifact = _make_artifact(state=ArtifactLifecycleState.TEMPORARY)

    with pytest.raises(ArtifactLifecycleError, match="temporary -> referenced"):
        artifact.transition_to(
            ArtifactLifecycleState.REFERENCED,
            owner_type="run_stage_result",
            owner_id=uuid.uuid4(),
        )

    artifact.transition_to(ArtifactLifecycleState.READY)
    owner_id = uuid.uuid4()
    artifact.transition_to(
        ArtifactLifecycleState.REFERENCED,
        owner_type="run_stage_result",
        owner_id=owner_id,
    )

    assert artifact.state == ArtifactLifecycleState.REFERENCED.value
    assert artifact.owner_type == "run_stage_result"
    assert artifact.owner_id == owner_id


def test_artifact_lifecycle_allows_cleanup_and_makes_expired_terminal() -> None:
    artifact = _make_artifact(state=ArtifactLifecycleState.TEMPORARY)
    artifact.transition_to(ArtifactLifecycleState.EXPIRED)

    assert artifact.state == ArtifactLifecycleState.EXPIRED.value

    with pytest.raises(ArtifactLifecycleError, match="expired -> ready"):
        artifact.transition_to(ArtifactLifecycleState.READY)


def test_referenced_artifact_requires_complete_owner_identity() -> None:
    artifact = _make_artifact(state=ArtifactLifecycleState.READY)

    with pytest.raises(ArtifactLifecycleError, match="require owner_type and owner_id"):
        artifact.transition_to(
            ArtifactLifecycleState.REFERENCED,
            owner_type="run_stage_result",
        )


def test_stage_result_artifacts_use_relational_refs_and_keep_legacy_json() -> None:
    stage_result = _make_stage_result()
    artifact = _make_artifact(state=ArtifactLifecycleState.READY)

    stage_result.artifacts.append(artifact)

    assert stage_result.artifacts == [artifact]
    assert artifact.stage_results == [stage_result]
    assert stage_result.artifact_refs_json == ["artifact:legacy:roads"]

    stage_fk = next(iter(run_stage_result_artifacts.c.stage_result_id.foreign_keys))
    artifact_fk = next(iter(run_stage_result_artifacts.c.artifact_id.foreign_keys))
    assert stage_fk.target_fullname == "run_stage_results.id"
    assert stage_fk.ondelete == "CASCADE"
    assert artifact_fk.target_fullname == "artifacts.id"
    assert artifact_fk.ondelete == "RESTRICT"


def test_successful_run_artifact_refs_are_immutable_in_orm() -> None:
    stage_result = _make_stage_result()
    run = GenerationRun(
        id=stage_result.run_id,
        project_id=uuid.uuid4(),
        status="succeeded",
        mode="EXPANSION",
        seed=42,
        working_srid=32637,
        config_json={},
        config_schema_version="v1",
        commit_sha="d" * 40,
        metrics_json={},
        error_json=None,
        started_at=None,
        finished_at=None,
    )
    stage_result.run = run

    with pytest.raises(RunStageResultImmutableError, match="artifact refs"):
        stage_result.artifacts.append(
            _make_artifact(state=ArtifactLifecycleState.REFERENCED)
        )
