import uuid
from dataclasses import dataclass

import pytest

from core.urban_generator.domain import (
    ConfigRef,
    CorrelationMetadata,
    ProjectRef,
    ProjectSettings,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    Stage,
    StageContractError,
    StageDiagnostic,
    StageDiagnosticLevel,
    StageFingerprint,
    StageResult,
    TerritorySnapshot,
    build_stage_fingerprint,
    require_stage_input,
    validate_stage_metadata,
)


@dataclass(frozen=True, slots=True)
class DummyInput:
    value: int


@dataclass(frozen=True, slots=True)
class DummyConfig:
    multiplier: int


@dataclass(frozen=True, slots=True)
class DummyOutput:
    value: int


class DummyStage:
    name = "dummy_stage"
    version = "1.0.0"
    dependencies = ("prepare_snapshot",)

    def validate_input(self, value: object) -> DummyInput:
        return require_stage_input(value, DummyInput, stage_name=self.name)

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: DummyInput,
        config: DummyConfig,
    ) -> StageResult[DummyOutput]:
        validated = self.validate_input(stage_input)
        output = DummyOutput(value=validated.value * config.multiplier)
        fingerprint = build_stage_fingerprint(
            self.name,
            self.version,
            str(snapshot.snapshot_id),
            str(context.seed),
            str(validated.value),
            str(config.multiplier),
        )
        return StageResult(
            output=output,
            fingerprint=fingerprint,
            diagnostics=(
                StageDiagnostic(
                    code="dummy.executed",
                    message="Dummy stage executed successfully",
                    level=StageDiagnosticLevel.INFO,
                ),
            ),
        )


def make_snapshot() -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        project=ProjectRef(project_id=uuid.UUID("00000000-0000-0000-0000-000000000201")),
        settings=ProjectSettings(working_srid=32637),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
    )


def make_context() -> RunContext:
    return RunContext(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000301"),
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=32637,
        config_refs=(ConfigRef(name="generation", ref="synthetic:generation:v1"),),
        correlation=CorrelationMetadata(correlation_id="stage-contract-test"),
    )


def test_dummy_stage_runs_without_backend() -> None:
    stage = DummyStage()
    validate_stage_metadata(stage.name, stage.version, stage.dependencies)

    result = stage.execute(
        snapshot=make_snapshot(),
        context=make_context(),
        stage_input=DummyInput(value=7),
        config=DummyConfig(multiplier=3),
    )

    assert isinstance(stage, Stage)
    assert result.output == DummyOutput(value=21)
    assert result.diagnostics[0].code == "dummy.executed"
    assert result.fingerprint.value.startswith("sha256:")


def test_dummy_stage_fingerprint_is_deterministic() -> None:
    stage = DummyStage()
    snapshot = make_snapshot()
    context = make_context()
    stage_input = DummyInput(value=7)
    config = DummyConfig(multiplier=3)

    first = stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=stage_input,
        config=config,
    )
    second = stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=stage_input,
        config=config,
    )

    assert first.fingerprint == second.fingerprint


def test_stage_input_validation_rejects_wrong_type() -> None:
    stage = DummyStage()

    with pytest.raises(StageContractError, match="dummy_stage input must be DummyInput"):
        stage.validate_input("not-a-dummy-input")


def test_stage_metadata_rejects_self_and_duplicate_dependencies() -> None:
    with pytest.raises(StageContractError, match="cannot depend on itself"):
        validate_stage_metadata("roads", "1.0.0", ("roads",))

    with pytest.raises(StageContractError, match="duplicate stage dependency"):
        validate_stage_metadata("roads", "1.0.0", ("zoning", "zoning"))


def test_fingerprint_builder_is_order_sensitive_and_validated() -> None:
    first = build_stage_fingerprint("roads", b"input")
    second = build_stage_fingerprint(b"input", "roads")

    assert first != second
    assert len(first.value) == len("sha256:") + 64

    with pytest.raises(StageContractError, match="fingerprint must use sha256"):
        StageFingerprint(value="not-a-fingerprint")


def test_stage_result_requires_immutable_structured_diagnostics() -> None:
    fingerprint = build_stage_fingerprint("dummy")

    with pytest.raises(StageContractError, match="immutable tuple"):
        StageResult(output=DummyOutput(value=1), fingerprint=fingerprint, diagnostics=[])
