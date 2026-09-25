from __future__ import annotations

import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

import pytest

from backend.app.adapters.checkpoints import (
    CheckpointResolution,
    ReusableCheckpoint,
)
from backend.app.application.checkpoints import (
    CheckpointIdentity,
    build_config_hash,
    build_resolved_input_hash,
)
from backend.app.application.generation_execution import (
    GenerationExecutionError,
    GenerationExecutionRuntime,
    GenerationExecutionService,
    GenerationRunStartDisposition,
    StageInvocation,
)
from core.urban_generator.domain import (
    ArtifactRef,
    ArtifactStat,
    CorrelationMetadata,
    PipelineContext,
    PipelinePorts,
    ProjectRef,
    ProjectSettings,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    StageResult,
    TerritorySnapshot,
    build_stage_fingerprint,
)
from core.urban_generator.stages import StageRegistry


class FakeArtifactStore:
    def put(
        self,
        ref: ArtifactRef,
        source: BinaryIO,
        *,
        content_type: str | None = None,
    ) -> ArtifactStat:
        raise NotImplementedError

    def open(self, ref: ArtifactRef) -> BinaryIO:
        return BytesIO()

    def stat(self, ref: ArtifactRef) -> ArtifactStat:
        raise NotImplementedError

    def delete(self, ref: ArtifactRef) -> None:
        return None

    def promote(self, ref: ArtifactRef) -> ArtifactStat:
        raise NotImplementedError


@dataclass
class FakeStage:
    name: str
    dependencies: tuple[str, ...]
    calls: list[str]
    fail: bool = False
    version: str = "1.0.0"

    def validate_input(self, value: object) -> str:
        assert isinstance(value, str)
        return value

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: str,
        config: str,
    ) -> StageResult[str]:
        self.calls.append(self.name)
        if self.fail:
            raise ValueError(f"{self.name} failed")
        return StageResult(
            output=f"{self.name}:{stage_input}:{config}",
            fingerprint=build_stage_fingerprint(
                self.name,
                stage_input,
                config,
            ),
        )


@dataclass
class FakeBinding:
    stage_name: str
    prepared_dependency_outputs: list[tuple[str, ...]]
    restored_output: str | None = None

    def prepare(
        self,
        *,
        context: PipelineContext,
        dependency_results,
    ) -> StageInvocation:
        dependency_outputs = tuple(
            item.output for item in dependency_results.values()
        )
        self.prepared_dependency_outputs.append(dependency_outputs)
        stage_input = (
            "|".join(dependency_outputs)
            if dependency_outputs
            else f"{self.stage_name}-input"
        )
        return StageInvocation(
            stage_input=stage_input,
            config=f"{self.stage_name}-config",
            input_parts=(stage_input,),
            config_parts=(f"{self.stage_name}-config",),
        )

    def restore(
        self,
        *,
        context: PipelineContext,
        checkpoint: ReusableCheckpoint,
    ) -> StageResult[object]:
        assert self.restored_output is not None
        return StageResult(
            output=self.restored_output,
            fingerprint=checkpoint.output_fingerprint,
        )

    def persist(
        self,
        *,
        context: PipelineContext,
        result: StageResult[object],
    ) -> None:
        return None


class FakeCheckpointResolver:
    def __init__(
        self,
        *,
        reusable_stage: str | None = None,
        reusable_output_fingerprint=None,
    ) -> None:
        self.reusable_stage = reusable_stage
        self.reusable_output_fingerprint = reusable_output_fingerprint
        self.calls: list[str] = []

    def resolve(
        self,
        *,
        run_id: uuid.UUID,
        stage_name: str,
        stage_version: str,
        input_parts,
        config_parts,
        expected_dependencies,
    ) -> CheckpointResolution:
        self.calls.append(stage_name)
        identity = CheckpointIdentity(
            stage_name=stage_name,
            stage_version=stage_version,
            input_hash=build_resolved_input_hash(
                input_parts=input_parts,
                expected_dependencies=(),
                dependency_outputs=(),
            ),
            config_hash=build_config_hash(*config_parts),
        )
        reusable = None
        if stage_name == self.reusable_stage:
            assert self.reusable_output_fingerprint is not None
            reusable = ReusableCheckpoint(
                run_id=run_id,
                stage_result_id=uuid.uuid4(),
                identity=identity,
                output_fingerprint=self.reusable_output_fingerprint,
            )
        return CheckpointResolution(
            identity=identity,
            dependency_outputs=(),
            reusable=reusable,
        )


class FakeRuntimeLoader:
    def __init__(self, runtime: GenerationExecutionRuntime) -> None:
        self.runtime = runtime

    def load(self, run_id: uuid.UUID) -> GenerationExecutionRuntime:
        return self.runtime


class FakeProgressStore:
    def __init__(
        self,
        disposition: GenerationRunStartDisposition = (
            GenerationRunStartDisposition.STARTED
        ),
    ) -> None:
        self.disposition = disposition
        self.events: list[tuple[str, str]] = []

    def begin_run(self, run_id: uuid.UUID) -> GenerationRunStartDisposition:
        self.events.append(("run", "running"))
        return self.disposition

    def mark_stage_running(self, *, run_id, identity) -> None:
        self.events.append((identity.stage_name, "running"))

    def mark_stage_succeeded(self, *, run_id, identity, result) -> None:
        self.events.append((identity.stage_name, "succeeded"))

    def mark_stage_skipped(
        self,
        *,
        run_id,
        stage_name,
        stage_version,
        reason,
        blocked_by,
    ) -> None:
        self.events.append((stage_name, "skipped"))

    def mark_stage_failed(
        self,
        *,
        run_id,
        stage_name,
        stage_version,
        error,
    ) -> None:
        self.events.append((stage_name, "failed"))

    def mark_run_succeeded(self, *, run_id, commit_sha) -> None:
        self.events.append(("run", "succeeded"))

    def mark_run_failed(self, *, run_id, error) -> None:
        self.events.append(("run", "failed"))


def _context(run_id: uuid.UUID) -> PipelineContext:
    snapshot = TerritorySnapshot(
        snapshot_id=uuid.uuid4(),
        project=ProjectRef(project_id=uuid.uuid4()),
        settings=ProjectSettings(working_srid=32637),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
    )
    run = RunContext(
        run_id=run_id,
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=32637,
        config_refs=(),
        correlation=CorrelationMetadata(correlation_id="generation-execution-test"),
    )
    return PipelineContext(
        run=run,
        snapshot=snapshot,
        configs=(),
        ports=PipelinePorts(artifact_store=FakeArtifactStore()),
    )


def _runtime(
    *,
    run_id: uuid.UUID,
    stages: tuple[FakeStage, ...],
    bindings: tuple[FakeBinding, ...],
    checkpoints: FakeCheckpointResolver,
) -> GenerationExecutionRuntime:
    return GenerationExecutionRuntime(
        context=_context(run_id),
        registry=StageRegistry(stages=stages),
        bindings=bindings,
        checkpoints=checkpoints,
        commit_sha="a" * 40,
    )


def test_execution_service_runs_dependency_safe_dag_and_persists_progress() -> None:
    run_id = uuid.uuid4()
    calls: list[str] = []
    child = FakeStage(name="child", dependencies=("root",), calls=calls)
    root = FakeStage(name="root", dependencies=(), calls=calls)
    root_binding = FakeBinding("root", [])
    child_binding = FakeBinding("child", [])
    checkpoints = FakeCheckpointResolver()
    runtime = _runtime(
        run_id=run_id,
        stages=(child, root),
        bindings=(child_binding, root_binding),
        checkpoints=checkpoints,
    )
    progress = FakeProgressStore()

    report = GenerationExecutionService(
        runtime_loader=FakeRuntimeLoader(runtime),
        progress_store=progress,
    ).run(run_id=run_id)

    assert report.status == "succeeded"
    assert report.executed_stages == ("root", "child")
    assert report.reused_stages == ()
    assert calls == ["root", "child"]
    assert child_binding.prepared_dependency_outputs == (
        [("root:root-input:root-config",)]
    )
    assert progress.events == [
        ("run", "running"),
        ("root", "running"),
        ("root", "succeeded"),
        ("child", "running"),
        ("child", "succeeded"),
        ("run", "succeeded"),
    ]


def test_execution_service_restores_checkpoint_for_downstream_stage() -> None:
    run_id = uuid.uuid4()
    calls: list[str] = []
    root = FakeStage(name="root", dependencies=(), calls=calls)
    child = FakeStage(name="child", dependencies=("root",), calls=calls)
    root_fingerprint = build_stage_fingerprint("restored-root")
    root_binding = FakeBinding(
        "root",
        [],
        restored_output="restored-root-output",
    )
    child_binding = FakeBinding("child", [])
    checkpoints = FakeCheckpointResolver(
        reusable_stage="root",
        reusable_output_fingerprint=root_fingerprint,
    )
    runtime = _runtime(
        run_id=run_id,
        stages=(root, child),
        bindings=(root_binding, child_binding),
        checkpoints=checkpoints,
    )
    progress = FakeProgressStore()

    report = GenerationExecutionService(
        runtime_loader=FakeRuntimeLoader(runtime),
        progress_store=progress,
    ).run(run_id=run_id)

    assert report.reused_stages == ("root",)
    assert report.executed_stages == ("child",)
    assert calls == ["child"]
    assert child_binding.prepared_dependency_outputs == [
        ("restored-root-output",)
    ]
    assert ("root", "running") not in progress.events
    assert ("root", "succeeded") not in progress.events


def test_execution_service_persists_stage_and_run_failure() -> None:
    run_id = uuid.uuid4()
    stage = FakeStage(
        name="broken",
        dependencies=(),
        calls=[],
        fail=True,
    )
    runtime = _runtime(
        run_id=run_id,
        stages=(stage,),
        bindings=(FakeBinding("broken", []),),
        checkpoints=FakeCheckpointResolver(),
    )
    progress = FakeProgressStore()

    with pytest.raises(ValueError, match="broken failed"):
        GenerationExecutionService(
            runtime_loader=FakeRuntimeLoader(runtime),
            progress_store=progress,
        ).run(run_id=run_id)

    assert progress.events[-2:] == [
        ("broken", "failed"),
        ("run", "failed"),
    ]


def test_execution_service_short_circuits_immutable_successful_run() -> None:
    run_id = uuid.uuid4()
    stage = FakeStage(name="root", dependencies=(), calls=[])
    runtime = _runtime(
        run_id=run_id,
        stages=(stage,),
        bindings=(FakeBinding("root", []),),
        checkpoints=FakeCheckpointResolver(),
    )
    progress = FakeProgressStore(
        GenerationRunStartDisposition.ALREADY_SUCCEEDED
    )

    report = GenerationExecutionService(
        runtime_loader=FakeRuntimeLoader(runtime),
        progress_store=progress,
    ).run(run_id=run_id)

    assert report.status == "already_succeeded"
    assert stage.calls == []
    assert progress.events == [("run", "running")]


def test_runtime_rejects_missing_or_extra_stage_bindings() -> None:
    run_id = uuid.uuid4()
    stage = FakeStage(name="root", dependencies=(), calls=[])

    with pytest.raises(
        GenerationExecutionError,
        match="bindings must exactly match StageRegistry",
    ):
        GenerationExecutionRuntime(
            context=_context(run_id),
            registry=StageRegistry(stages=(stage,)),
            bindings=(),
            checkpoints=FakeCheckpointResolver(),
            commit_sha="a" * 40,
        )
