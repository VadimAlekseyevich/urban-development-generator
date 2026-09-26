from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

import pytest

from backend.app.application.checkpoints import (
    CheckpointIdentity,
    DependencyOutputFingerprint,
    build_config_hash,
    build_resolved_input_hash,
)
from backend.app.application.generation import (
    GenerationClaimDisposition,
    GenerationExecutionError,
    GenerationJobService,
    GenerationRuntime,
    StageInvocation,
)
from core.urban_generator.domain import (
    ArtifactRef,
    ArtifactStat,
    ConfigRef,
    CorrelationMetadata,
    PipelineContext,
    PipelinePorts,
    ProjectRef,
    ProjectSettings,
    ResolvedConfigBinding,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    StageFingerprint,
    StageResult,
    TerritorySnapshot,
    build_stage_fingerprint,
)
from core.urban_generator.stages import StageRegistry, StageSkipReason
from core.urban_generator.stages.registry import StageAny
from worker.tasks import (
    GenerationTaskConfigurationError,
    GenerationTaskFailed,
    run_generation,
)

RUN_ID = uuid.UUID("00000000-0000-0000-0000-000000000777")
PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-000000000778")


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
class DummyStage:
    name: str
    dependencies: tuple[str, ...] = ()
    version: str = "1"
    should_fail: bool = False

    def validate_input(self, value: object) -> int:
        if not isinstance(value, int):
            raise ValueError("expected integer stage input")
        return value

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: int,
        config: str,
    ) -> StageResult[int]:
        assert snapshot.settings.working_srid == context.working_srid
        if self.should_fail:
            raise RuntimeError("synthetic stage failure")
        return StageResult(
            output=stage_input + 1,
            fingerprint=build_stage_fingerprint(
                self.name,
                str(stage_input),
                config,
            ),
        )


class FakeInputResolver:
    def __init__(self) -> None:
        self.seen: list[tuple[str, tuple[str, ...]]] = []

    def resolve(
        self,
        *,
        stage: StageAny,
        context: PipelineContext,
        config: object,
        outputs: Mapping[str, object],
    ) -> StageInvocation:
        assert isinstance(config, str)
        assert context.run.run_id == RUN_ID
        self.seen.append((stage.name, tuple(sorted(outputs))))
        value = 1 + sum(int(outputs[dep]) for dep in stage.dependencies)
        return StageInvocation(
            stage_input=value,
            input_parts=(stage.name, str(value)),
            config_parts=(config,),
        )


class FakeStateStore:
    def __init__(
        self,
        disposition: GenerationClaimDisposition = GenerationClaimDisposition.STARTED,
    ) -> None:
        self.disposition = disposition
        self.records: dict[str, tuple[str, StageFingerprint | None]] = {}
        self.order: list[str] = []
        self.dependencies_seen: dict[str, tuple[str, ...]] = {}
        self.run_completed = False
        self.run_failed = False
        self.failed_stage: str | None = None

    def claim(self, *, run_id: uuid.UUID) -> GenerationClaimDisposition:
        assert run_id == RUN_ID
        return self.disposition

    def resolve_identity(
        self,
        *,
        run_id: uuid.UUID,
        stage: StageAny,
        invocation: StageInvocation,
    ) -> CheckpointIdentity:
        dependencies = tuple(
            DependencyOutputFingerprint(
                stage_name=name,
                output_fingerprint=self._output(name),
            )
            for name in stage.dependencies
        )
        self.dependencies_seen[stage.name] = tuple(x.stage_name for x in dependencies)
        return CheckpointIdentity(
            stage_name=stage.name,
            stage_version=stage.version,
            input_hash=build_resolved_input_hash(
                input_parts=invocation.input_parts,
                expected_dependencies=stage.dependencies,
                dependency_outputs=dependencies,
            ),
            config_hash=build_config_hash(*invocation.config_parts),
        )

    def _output(self, name: str) -> StageFingerprint:
        result = self.records[name][1]
        assert result is not None
        return result

    def start_stage(
        self,
        *,
        run_id: uuid.UUID,
        identity: CheckpointIdentity,
    ) -> None:
        self.records[identity.stage_name] = ("running", None)
        self.order.append(identity.stage_name)

    def complete_stage(
        self,
        *,
        run_id: uuid.UUID,
        stage_name: str,
        result: StageResult[object],
    ) -> None:
        self.records[stage_name] = ("succeeded", result.fingerprint)

    def skip_stage(
        self,
        *,
        run_id: uuid.UUID,
        stage: StageAny,
        reason: StageSkipReason,
        blocked_by: tuple[str, ...],
    ) -> None:
        assert reason is not None
        self.records[stage.name] = ("skipped", None)
        self.order.append(stage.name)

    def fail_stage(self, *, run_id: uuid.UUID, stage_name: str) -> None:
        self.failed_stage = stage_name
        if self.records.get(stage_name, ("", None))[0] == "running":
            self.records[stage_name] = ("failed", None)

    def complete_run(
        self,
        *,
        run_id: uuid.UUID,
        expected_stage_names: tuple[str, ...],
    ) -> None:
        assert set(expected_stage_names) == set(self.records)
        self.run_completed = True

    def fail_run(self, *, run_id: uuid.UUID, stage_name: str | None) -> None:
        self.run_failed = True


class StaticFactory:
    def __init__(self, runtime: GenerationRuntime) -> None:
        self.runtime = runtime
        self.calls = 0

    def create(self, *, run_id: uuid.UUID) -> GenerationRuntime:
        self.calls += 1
        return self.runtime


def _runtime(
    stages: tuple[DummyStage, ...],
    *,
    skip_stages: tuple[str, ...] = (),
    omitted_config: str | None = None,
    run_id: uuid.UUID = RUN_ID,
) -> tuple[GenerationRuntime, FakeInputResolver]:
    source = ConfigRef(name="generation", ref="config:v1")
    context = PipelineContext(
        run=RunContext(
            run_id=run_id,
            mode=RunMode.EXPANSION,
            seed=4,
            working_srid=32637,
            config_refs=(source,),
            correlation=CorrelationMetadata(correlation_id="generation-fixture"),
        ),
        snapshot=TerritorySnapshot(
            snapshot_id=run_id,
            project=ProjectRef(project_id=PROJECT_ID),
            settings=ProjectSettings(working_srid=32637),
            boundary=SnapshotLayerRef(
                kind=SnapshotLayerKind.BOUNDARY,
                source_ref="fixture:boundary",
            ),
        ),
        configs=tuple(
            ResolvedConfigBinding(
                stage_name=stage.name,
                source=source,
                value=f"config:{stage.name}",
            )
            for stage in stages
            if stage.name != omitted_config
        ),
        ports=PipelinePorts(artifact_store=FakeArtifactStore()),
    )
    resolver = FakeInputResolver()
    return (
        GenerationRuntime(
            context=context,
            registry=StageRegistry(stages=stages),
            input_resolver=resolver,
            skip_stages=skip_stages,
        ),
        resolver,
    )


def test_generation_executor_uses_topological_order_and_dependency_outputs() -> None:
    runtime, resolver = _runtime(
        (
            DummyStage("joined", ("beta", "alpha")),
            DummyStage("beta", ("root",)),
            DummyStage("alpha", ("root",)),
            DummyStage("root"),
        )
    )
    store = FakeStateStore()

    result = GenerationJobService(
        state_store=store, runtime_factory=StaticFactory(runtime)
    ).run(run_id=RUN_ID)

    assert result.status == "succeeded"
    assert result.succeeded_stages == ("root", "alpha", "beta", "joined")
    assert store.order == list(result.succeeded_stages)
    assert store.dependencies_seen["joined"] == ("beta", "alpha")
    assert resolver.seen[-1] == ("joined", ("alpha", "beta", "root"))
    assert all(status == "succeeded" for status, _ in store.records.values())
    assert all(fingerprint is not None for _, fingerprint in store.records.values())
    assert store.run_completed and not store.run_failed


def test_generation_executor_persists_requested_and_downstream_skips() -> None:
    runtime, _ = _runtime(
        (
            DummyStage("joined", ("alpha", "beta")),
            DummyStage("alpha", ("root",)),
            DummyStage("beta", ("root",)),
            DummyStage("root"),
        ),
        skip_stages=("alpha",),
    )
    store = FakeStateStore()

    result = GenerationJobService(
        state_store=store, runtime_factory=StaticFactory(runtime)
    ).run(run_id=RUN_ID)

    assert result.status == "succeeded"
    assert result.succeeded_stages == ("root", "beta")
    assert result.skipped_stages == ("alpha", "joined")
    assert store.records["alpha"] == ("skipped", None)
    assert store.records["joined"] == ("skipped", None)
    assert store.run_completed


def test_generation_executor_marks_stage_and_run_failed_on_execution_error() -> None:
    runtime, _ = _runtime((DummyStage("root", should_fail=True),))
    store = FakeStateStore()

    with pytest.raises(GenerationExecutionError, match="stage: root"):
        GenerationJobService(
            state_store=store, runtime_factory=StaticFactory(runtime)
        ).run(run_id=RUN_ID)

    assert store.records["root"] == ("failed", None)
    assert store.failed_stage == "root"
    assert store.run_failed and not store.run_completed


def test_generation_executor_does_not_claim_success_without_config() -> None:
    runtime, _ = _runtime((DummyStage("root"),), omitted_config="root")
    store = FakeStateStore()

    with pytest.raises(GenerationExecutionError, match="stage: root"):
        GenerationJobService(
            state_store=store, runtime_factory=StaticFactory(runtime)
        ).run(run_id=RUN_ID)

    assert store.records == {}
    assert store.run_failed and not store.run_completed


def test_generation_executor_rejects_wrong_assembled_run_identity() -> None:
    runtime, _ = _runtime((DummyStage("root"),), run_id=uuid.uuid4())
    store = FakeStateStore()

    with pytest.raises(GenerationExecutionError, match="stage: assembly"):
        GenerationJobService(
            state_store=store, runtime_factory=StaticFactory(runtime)
        ).run(run_id=RUN_ID)

    assert store.run_failed and not store.records


@pytest.mark.parametrize(
    "disposition",
    (
        GenerationClaimDisposition.ALREADY_SUCCEEDED,
        GenerationClaimDisposition.IN_PROGRESS,
    ),
)
def test_generation_executor_does_not_reexecute_an_existing_attempt(
    disposition: GenerationClaimDisposition,
) -> None:
    runtime, _ = _runtime((DummyStage("root"),))
    factory = StaticFactory(runtime)
    result = GenerationJobService(
        state_store=FakeStateStore(disposition),
        runtime_factory=factory,
    ).run(run_id=RUN_ID)

    assert result.status == disposition.value
    assert factory.calls == 0


def test_worker_generation_task_uses_injected_real_service() -> None:
    runtime, _ = _runtime((DummyStage("root"),))
    service = GenerationJobService(
        state_store=FakeStateStore(),
        runtime_factory=StaticFactory(runtime),
    )
    result = asyncio.run(
        run_generation({"generation_job_service": service}, str(RUN_ID))
    )

    assert result == {
        "run_id": str(RUN_ID),
        "status": "succeeded",
        "succeeded_stages": ["root"],
        "skipped_stages": [],
    }


def test_worker_generation_task_rejects_unconfigured_runtime() -> None:
    with pytest.raises(GenerationTaskConfigurationError, match="runtime_factory"):
        asyncio.run(run_generation({}, str(RUN_ID)))


def test_worker_generation_task_does_not_report_failed_stage_as_success() -> None:
    runtime, _ = _runtime((DummyStage("root", should_fail=True),))
    service = GenerationJobService(
        state_store=FakeStateStore(),
        runtime_factory=StaticFactory(runtime),
    )

    with pytest.raises(GenerationTaskFailed, match=str(RUN_ID)):
        asyncio.run(run_generation({"generation_job_service": service}, str(RUN_ID)))


def test_stage_invocation_requires_explicit_canonical_hash_parts() -> None:
    with pytest.raises(GenerationExecutionError, match="input_parts"):
        StageInvocation(  # type: ignore[arg-type]
            stage_input=1, input_parts=["raw"], config_parts=("v1",)
        )

    with pytest.raises(GenerationExecutionError, match="config_parts"):
        StageInvocation(  # type: ignore[arg-type]
            stage_input=1, input_parts=("v1",), config_parts=(1,)
        )
