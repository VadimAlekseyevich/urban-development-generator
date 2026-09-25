from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from backend.app.adapters.checkpoints import CheckpointResolution, ReusableCheckpoint
from backend.app.application.checkpoints import CheckpointIdentity, HashPart
from core.urban_generator.domain import PipelineContext, StageResult
from core.urban_generator.stages import (
    StageRegistry,
    StageSkipReason,
)


class GenerationExecutionError(RuntimeError):
    """Raised when the durable generation execution contract is inconsistent."""


class GenerationRunStartDisposition(StrEnum):
    STARTED = "started"
    ALREADY_SUCCEEDED = "already_succeeded"


@dataclass(frozen=True, slots=True)
class StageInvocation:
    """Typed stage values plus canonical provenance parts for one execution attempt."""

    stage_input: object
    config: object
    input_parts: tuple[HashPart, ...]
    config_parts: tuple[HashPart, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.input_parts, tuple):
            raise GenerationExecutionError("input_parts must be an immutable tuple")
        if not isinstance(self.config_parts, tuple):
            raise GenerationExecutionError("config_parts must be an immutable tuple")
        for part in self.input_parts + self.config_parts:
            if not isinstance(part, (str, bytes)):
                raise GenerationExecutionError(
                    "stage provenance parts must be str or bytes"
                )


@runtime_checkable
class StageRuntimeBinding(Protocol):
    """Application binding between one canonical Stage and persisted/runtime values."""

    stage_name: str

    def prepare(
        self,
        *,
        context: PipelineContext,
        dependency_results: Mapping[str, StageResult[object]],
    ) -> StageInvocation:
        """Build typed input/config and canonical checkpoint parts."""

    def restore(
        self,
        *,
        context: PipelineContext,
        checkpoint: ReusableCheckpoint,
    ) -> StageResult[object]:
        """Restore the typed result represented by a reusable persisted checkpoint."""

    def persist(
        self,
        *,
        context: PipelineContext,
        result: StageResult[object],
    ) -> None:
        """Persist stage-owned generated output outside core."""


class CheckpointResolver(Protocol):
    def resolve(
        self,
        *,
        run_id: uuid.UUID,
        stage_name: str,
        stage_version: str,
        input_parts: tuple[HashPart, ...],
        config_parts: tuple[HashPart, ...],
        expected_dependencies: tuple[str, ...],
    ) -> CheckpointResolution:
        """Resolve current identity and optional same-run reusable result."""


@dataclass(frozen=True, slots=True)
class GenerationExecutionRuntime:
    """One immutable runtime bundle for canonical Stage DAG execution."""

    context: PipelineContext
    registry: StageRegistry
    bindings: tuple[StageRuntimeBinding, ...]
    checkpoints: CheckpointResolver
    commit_sha: str
    skip_stages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.context, PipelineContext):
            raise GenerationExecutionError("context must be PipelineContext")
        if not isinstance(self.registry, StageRegistry):
            raise GenerationExecutionError("registry must be StageRegistry")
        if not isinstance(self.bindings, tuple):
            raise GenerationExecutionError("bindings must be an immutable tuple")
        if not isinstance(self.skip_stages, tuple):
            raise GenerationExecutionError("skip_stages must be an immutable tuple")
        if _COMMIT_SHA_RE.fullmatch(self.commit_sha) is None:
            raise GenerationExecutionError(
                "commit_sha must use 40 lowercase hexadecimal characters"
            )

        by_name: dict[str, StageRuntimeBinding] = {}
        for binding in self.bindings:
            if not isinstance(binding, StageRuntimeBinding):
                raise GenerationExecutionError(
                    "bindings must implement StageRuntimeBinding"
                )
            if binding.stage_name in by_name:
                raise GenerationExecutionError(
                    f"duplicate runtime binding: {binding.stage_name}"
                )
            by_name[binding.stage_name] = binding

        registry_names = frozenset(self.registry.names)
        binding_names = frozenset(by_name)
        if binding_names != registry_names:
            missing = sorted(registry_names - binding_names)
            extra = sorted(binding_names - registry_names)
            raise GenerationExecutionError(
                "runtime bindings must exactly match StageRegistry; "
                f"missing={missing}, extra={extra}"
            )

    def binding(self, stage_name: str) -> StageRuntimeBinding:
        for binding in self.bindings:
            if binding.stage_name == stage_name:
                return binding
        raise GenerationExecutionError(
            f"runtime binding is unavailable for stage: {stage_name}"
        )


class GenerationRuntimeLoader(Protocol):
    def load(self, run_id: uuid.UUID) -> GenerationExecutionRuntime:
        """Load one complete execution runtime for a persisted GenerationRun."""


class GenerationProgressStore(Protocol):
    def begin_run(self, run_id: uuid.UUID) -> GenerationRunStartDisposition:
        """Claim one queued generation run for worker execution."""

    def mark_stage_running(
        self,
        *,
        run_id: uuid.UUID,
        identity: CheckpointIdentity,
    ) -> None:
        """Persist running status and current stage provenance."""

    def mark_stage_succeeded(
        self,
        *,
        run_id: uuid.UUID,
        identity: CheckpointIdentity,
        result: StageResult[object],
    ) -> None:
        """Persist stage success, diagnostics and canonical output fingerprint."""

    def mark_stage_skipped(
        self,
        *,
        run_id: uuid.UUID,
        stage_name: str,
        stage_version: str,
        reason: StageSkipReason,
        blocked_by: tuple[str, ...],
    ) -> None:
        """Persist a resolved skip decision."""

    def mark_stage_failed(
        self,
        *,
        run_id: uuid.UUID,
        stage_name: str,
        error: Exception,
    ) -> None:
        """Persist failure for the stage currently executing."""

    def mark_run_succeeded(
        self,
        *,
        run_id: uuid.UUID,
        commit_sha: str,
    ) -> None:
        """Persist the successful immutable terminal run state."""

    def mark_run_failed(
        self,
        *,
        run_id: uuid.UUID,
        error: Exception,
    ) -> None:
        """Persist a terminal failure for this generation attempt."""


@dataclass(frozen=True, slots=True)
class GenerationExecutionReport:
    run_id: uuid.UUID
    status: str
    executed_stages: tuple[str, ...] = ()
    reused_stages: tuple[str, ...] = ()
    skipped_stages: tuple[str, ...] = ()


class GenerationExecutionService:
    """Execute one canonical Stage DAG outside HTTP with durable progress."""

    def __init__(
        self,
        *,
        runtime_loader: GenerationRuntimeLoader,
        progress_store: GenerationProgressStore,
    ) -> None:
        self._runtime_loader = runtime_loader
        self._progress_store = progress_store

    def run(self, *, run_id: uuid.UUID) -> GenerationExecutionReport:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")

        disposition = self._progress_store.begin_run(run_id)
        if disposition is GenerationRunStartDisposition.ALREADY_SUCCEEDED:
            return GenerationExecutionReport(
                run_id=run_id,
                status=GenerationRunStartDisposition.ALREADY_SUCCEEDED.value,
            )

        current_stage_name: str | None = None
        try:
            runtime = self._runtime_loader.load(run_id)
            if runtime.context.run.run_id != run_id:
                raise GenerationExecutionError(
                    "runtime PipelineContext run_id does not match requested run"
                )

            plan = runtime.registry.build_plan(skip_stages=runtime.skip_stages)
            results: dict[str, StageResult[object]] = {}
            executed: list[str] = []
            reused: list[str] = []
            skipped: list[str] = []

            for entry in plan:
                stage = entry.stage
                current_stage_name = stage.name

                if not entry.should_execute:
                    assert entry.skip_reason is not None
                    self._progress_store.mark_stage_skipped(
                        run_id=run_id,
                        stage_name=stage.name,
                        stage_version=stage.version,
                        reason=entry.skip_reason,
                        blocked_by=entry.blocked_by,
                    )
                    skipped.append(stage.name)
                    continue

                binding = runtime.binding(stage.name)
                dependency_results = MappingProxyType(
                    {
                        dependency: results[dependency]
                        for dependency in stage.dependencies
                    }
                )
                invocation = binding.prepare(
                    context=runtime.context,
                    dependency_results=dependency_results,
                )
                resolution = runtime.checkpoints.resolve(
                    run_id=run_id,
                    stage_name=stage.name,
                    stage_version=stage.version,
                    input_parts=invocation.input_parts,
                    config_parts=invocation.config_parts,
                    expected_dependencies=stage.dependencies,
                )

                if resolution.reusable is not None:
                    restored = binding.restore(
                        context=runtime.context,
                        checkpoint=resolution.reusable,
                    )
                    _require_restored_fingerprint(
                        restored,
                        resolution.reusable,
                    )
                    results[stage.name] = restored
                    reused.append(stage.name)
                    continue

                self._progress_store.mark_stage_running(
                    run_id=run_id,
                    identity=resolution.identity,
                )
                result = stage.execute(
                    snapshot=runtime.context.snapshot,
                    context=runtime.context.run,
                    stage_input=invocation.stage_input,
                    config=invocation.config,
                )
                if not isinstance(result, StageResult):
                    raise GenerationExecutionError(
                        f"stage {stage.name} returned non-StageResult output"
                    )
                typed_result = _erase_stage_result(result)
                binding.persist(
                    context=runtime.context,
                    result=typed_result,
                )
                self._progress_store.mark_stage_succeeded(
                    run_id=run_id,
                    identity=resolution.identity,
                    result=typed_result,
                )
                results[stage.name] = typed_result
                executed.append(stage.name)

            self._progress_store.mark_run_succeeded(
                run_id=run_id,
                commit_sha=runtime.commit_sha,
            )
            return GenerationExecutionReport(
                run_id=run_id,
                status="succeeded",
                executed_stages=tuple(executed),
                reused_stages=tuple(reused),
                skipped_stages=tuple(skipped),
            )
        except Exception as exc:
            if current_stage_name is not None:
                self._progress_store.mark_stage_failed(
                    run_id=run_id,
                    stage_name=current_stage_name,
                    error=exc,
                )
            self._progress_store.mark_run_failed(
                run_id=run_id,
                error=exc,
            )
            raise


def _erase_stage_result(result: StageResult[Any]) -> StageResult[object]:
    return StageResult(
        output=result.output,
        fingerprint=result.fingerprint,
        diagnostics=result.diagnostics,
    )


def _require_restored_fingerprint(
    result: StageResult[object],
    checkpoint: ReusableCheckpoint,
) -> None:
    if not isinstance(result, StageResult):
        raise GenerationExecutionError(
            "checkpoint restore must return StageResult"
        )
    if result.fingerprint != checkpoint.output_fingerprint:
        raise GenerationExecutionError(
            "restored checkpoint fingerprint does not match persisted provenance"
        )


_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
