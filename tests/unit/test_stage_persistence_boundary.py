from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain import StageFingerprint, validate_stage_metadata
from core.urban_generator.stages import (
    CANONICAL_STAGE_DEPENDENCIES,
    BlocksAndParcelsStage,
    BuildingStage,
    ConstraintMaskStage,
    DemographyStage,
    RoadStage,
    SuitabilityStage,
    ZoningStage,
)

IMPLEMENTED_STAGE_TYPES = (
    ConstraintMaskStage,
    SuitabilityStage,
    ZoningStage,
    RoadStage,
    BlocksAndParcelsStage,
    BuildingStage,
    DemographyStage,
)


def test_implemented_stage_metadata_matches_persisted_identity_vocabulary() -> None:
    for stage_type in IMPLEMENTED_STAGE_TYPES:
        validate_stage_metadata(
            stage_type.name,
            stage_type.version,
            stage_type.dependencies,
        )
        assert stage_type.name in CANONICAL_STAGE_DEPENDENCIES
        assert (
            stage_type.dependencies
            == CANONICAL_STAGE_DEPENDENCIES[stage_type.name]
        )
        assert len(stage_type.name) <= RunStageResult.__table__.c.stage_name.type.length
        assert (
            len(stage_type.version)
            <= RunStageResult.__table__.c.stage_version.type.length
        )


def test_stage_output_fingerprint_has_dedicated_persistence_field() -> None:
    fingerprint = StageFingerprint(value=f"sha256:{'d' * 64}")
    row = RunStageResult(
        stage_name=RoadStage.name,
        stage_version=RoadStage.version,
        status="succeeded",
        progress_percent=100,
        input_hash=f"sha256:{'a' * 64}",
        config_hash=f"sha256:{'b' * 64}",
        output_fingerprint=str(fingerprint),
        diagnostics_json=[],
        artifact_refs_json=[],
    )

    assert row.output_fingerprint == str(fingerprint)
    assert row.output_fingerprint != row.input_hash
    assert row.output_fingerprint != row.config_hash
