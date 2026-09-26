from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from backend.app.application.checkpoints import CheckpointIdentity, HashPart
from core.urban_generator.domain import PipelineContext, StageResult
from core.urban_generator.stages.registry import (
    StageAny,
    StageRegistry,
    StageSkipReason,
)


class GenerationExecutionError(ValueError):
    """Raised when a generation attempt cannot follow the canonical DAG."""


class GenerationClaimDisposition(StrEnum):
    STARTED = "started"
    ALREADY_SUCCEEDED = "already_succeeded"
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True, slots=True)
class StageInvocation:
    """Typed-input resolver output with explicit canonical checkpoint parts."""

    stage_input: object
    input_parts: tuple[HashPart, ...]
    config_parts: tuple[HashPart, ...]

    def __post_init__(self) -> None:
        for label, parts in (
            ("input_parts", self.input_parts),
            ("config_parts", self.config_parts),
        ):
            if not isinstance(parts, tuple):
                raise GenerationExecutionError(f"{label} must be an immutable tuple")
            if any(not isinstance(part, (str, bytes)) for part in parts):
                raise GenerationExecutionError(f"{label} must contain only str or bytes")


class GenerationInputResolver(Protocol):
    """Resolve one canonical Stage input from completed typed dependency outputs."""

    def resolve(
        self,
        *,
        stage: StageAny,
        context: PipelineContext,
        config: object,
        outputs: Mapping[str, object],
    ) -> StageInvocation: ...


@dataclass(frozen=True, slots=True)
class GenerationRuntime:
    """One assembled core context, DAG and typed stage-input composition."""

    context: PipelineContext
    registry: StageRegistry
    input_resolver: GenerationInputResolver
    skip_stages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.context, PipelineContext):
            raise GenerationExecutionError("context must be PipelineContext")
        if not isinstance(self.registry, StageRegistry):
            raise GenerationExecutionError("registry must be StageRegistry")
        if not isinstance(self.skip_stages, tuple):
            raise GenerationExecutionError("skip_stages must be an immutable tuple")


class GenerationRuntimeFactory(Protocol):
    def create(self, *, run_id: uuid.UUID) -> GenerationRuntime: ...


class GenerationStateStore(Protocol):
    """DB-authoritative lifecycle port; implementations commit stage progress individually."""

    def claim(self, *, run_id: uuid.UUID) -> GenerationClaimDisposition: ...

    def resolve_identity(
        self,
        *,
        run_id: uuid.UUID,
        stage: StageAny,
        invocation: StageInvocation,
    ) -> CheckpointIdentity: ...

    def start_stage(
        self,
        *,
        run_id: uuid.UUID,
        identity: CheckpointIdentity,
    ) -> None: ...

    def complete_stage(
        self,
        *,
        run_id: uuid.UUID,
        stage_name: str,
        result: StageResult[object],
    ) -> None: ...

    def skip_stage(
        self,
        *,
        run_id: uuid.UUID,
        stage: StageAny,
        reason: StageSkipReason,
        blocked_by: tuple[str, ...],
    ) -> None: ...

    def fail_stage(self, *, run_id: uuid.UUID, stage_name: str) -> None: ...

    def complete_run(
        self,
        *,
        run_id: uuid.UUID,
        expected_stage_names: tuple[str, ...],
    ) -> None: ...

    def fail_run(self, *, run_id: uuid.UUID, stage_name: str | None) -> None: ...


@dataclass(frozen=True, slots=True)
class GenerationExecutionResult:
    run_id: uuid.UUID
    status: str
    succeeded_stages: tuple[str, ...] = ()
    skipped_stages: tuple[str, ...] = ()


class GenerationJobService:
    """Execute registered canonical Stage instances outside HTTP with persisted progress."""

    def __init__(
        self,
        *,
        state_store: GenerationStateStore,
        runtime_factory: GenerationRuntimeFactory,
    ) -> None:
        self._store = state_store
        self._factory = runtime_factory

    def run(self, *, run_id: uuid.UUID) -> GenerationExecutionResult:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        claim = self._store.claim(run_id=run_id)
        if claim is GenerationClaimDisposition.ALREADY_SUCCEEDED:
            return GenerationExecutionResult(run_id=run_id, status="already_succeeded")
        if claim is GenerationClaimDisposition.IN_PROGRESS:
            return GenerationExecutionResult(run_id=run_id, status="in_progress")
        if claim is not GenerationClaimDisposition.STARTED:
            raise GenerationExecutionError("unsupported generation claim disposition")

        active_stage: str | None = None
        succeeded: list[str] = []
        skipped: list[str] = []
        try:
            runtime = self._factory.create(run_id=run_id)
            if not isinstance(runtime, GenerationRuntime):
                raise GenerationExecutionError("runtime factory must return GenerationRuntime")
            if runtime.context.run.run_id != run_id:
                raise GenerationExecutionError("assembled PipelineContext belongs to another run")
            plan = runtime.registry.build_plan(skip_stages=runtime.skip_stages)
            completed: dict[str, object] = {}
            for entry in plan:
                stage = entry.stage
                active_stage = stage.name
                if entry.skip_reason is not None:
                    self._store.skip_stage(
                        run_id=run_id,
                        stage=stage,
                        reason=entry.skip_reason,
                        blocked_by=entry.blocked_by,
                    )
                    skipped.append(stage.name)
                    active_stage = None
                    continue

                binding = next(
                    (
                        item
                        for item in runtime.context.configs
                        if item.stage_name == stage.name
                    ),
                    None,
                )
                if binding is None:
                    raise GenerationExecutionError(
                        f"resolved config is missing for stage: {stage.name}"
                    )
                invocation = runtime.input_resolver.resolve(
                    stage=stage,
                    context=runtime.context,
                    config=binding.value,
                    outputs=MappingProxyType(dict(completed)),
                )
                if not isinstance(invocation, StageInvocation):
                    raise GenerationExecutionError(
                        f"input resolver must return StageInvocation: {stage.name}"
                    )
                stage_input = stage.validate_input(invocation.stage_input)
                identity = self._store.resolve_identity(
                    run_id=run_id,
                    stage=stage,
                    invocation=invocation,
                )
                if identity.stage_name != stage.name or identity.stage_version != stage.version:
                    raise GenerationExecutionError(
                        f"checkpoint identity does not match stage: {stage.name}"
                    )
                self._store.start_stage(run_id=run_id, identity=identity)
                result = stage.execute(
                    snapshot=runtime.context.snapshot,
                    context=runtime.context.run,
                    stage_input=stage_input,
                    config=binding.value,
                )
                if not isinstance(result, StageResult):
                    raise GenerationExecutionError(
                        f"stage did not return canonical StageResult: {stage.name}"
                    )
                self._store.complete_stage(
                    run_id=run_id,
                    stage_name=stage.name,
                    result=result,
                )
                completed[stage.name] = result.output
                succeeded.append(stage.name)
                active_stage = None

            self._store.complete_run(
                run_id=run_id,
                expected_stage_names=tuple(item.stage.name for item in plan),
            )
        except Exception as exc:
            if active_stage is not None:
                self._store.fail_stage(run_id=run_id, stage_name=active_stage)
            self._store.fail_run(run_id=run_id, stage_name=active_stage)
            raise GenerationExecutionError(
                f"generation execution failed at stage: {active_stage or 'assembly'}"
            ) from exc

        return GenerationExecutionResult(
            run_id=run_id,
            status="succeeded",
            succeeded_stages=tuple(succeeded),
            skipped_stages=tuple(skipped),
        )
