from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.checkpoints import (
    CheckpointIdentity,
    DependencyOutputFingerprint,
    HashPart,
    build_config_hash,
    build_resolved_input_hash,
)
from backend.app.models.run_stage_result import (
    STAGE_SUCCESS_STATUS,
    RunStageResult,
)
from core.urban_generator.domain import (
    StageContractError,
    StageFingerprint,
    validate_stage_metadata,
)


class PersistedCheckpointError(ValueError):
    """Raised when persisted checkpoint provenance is incomplete or malformed."""


@dataclass(frozen=True, slots=True)
class ReusableCheckpoint:
    """One same-run successful stage result eligible for deterministic reuse."""

    run_id: uuid.UUID
    stage_result_id: uuid.UUID
    identity: CheckpointIdentity
    output_fingerprint: StageFingerprint

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, uuid.UUID):
            raise PersistedCheckpointError("run_id must be UUID")
        if not isinstance(self.stage_result_id, uuid.UUID):
            raise PersistedCheckpointError("stage_result_id must be UUID")
        if not isinstance(self.identity, CheckpointIdentity):
            raise PersistedCheckpointError("identity must be CheckpointIdentity")
        if not isinstance(self.output_fingerprint, StageFingerprint):
            raise PersistedCheckpointError(
                "output_fingerprint must be StageFingerprint"
            )


@dataclass(frozen=True, slots=True)
class CheckpointResolution:
    """Resolved current provenance plus an optional persisted reuse candidate."""

    identity: CheckpointIdentity
    dependency_outputs: tuple[DependencyOutputFingerprint, ...]
    reusable: ReusableCheckpoint | None


class SqlAlchemyCheckpointStore:
    """Resolve and match persisted checkpoints within one GenerationRun."""

    def __init__(self, *, session: Session) -> None:
        self._session = session

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
        """Build current identity from persisted dependencies and match same-run result."""

        _require_run_id(run_id)
        _validate_stage_identity(stage_name, stage_version)
        dependency_outputs = self.load_dependency_outputs(
            run_id=run_id,
            expected_dependencies=expected_dependencies,
        )
        identity = CheckpointIdentity(
            stage_name=stage_name,
            stage_version=stage_version,
            input_hash=build_resolved_input_hash(
                input_parts=input_parts,
                expected_dependencies=expected_dependencies,
                dependency_outputs=dependency_outputs,
            ),
            config_hash=build_config_hash(*config_parts),
        )
        return CheckpointResolution(
            identity=identity,
            dependency_outputs=dependency_outputs,
            reusable=self.find_reusable(
                run_id=run_id,
                expected=identity,
            ),
        )

    def load_dependency_outputs(
        self,
        *,
        run_id: uuid.UUID,
        expected_dependencies: tuple[str, ...],
    ) -> tuple[DependencyOutputFingerprint, ...]:
        """Load successful direct-dependency fingerprints in canonical stage order."""

        _require_run_id(run_id)
        _validate_dependencies(expected_dependencies)
        if not expected_dependencies:
            return ()

        rows = self._session.scalars(
            select(RunStageResult).where(
                RunStageResult.run_id == run_id,
                RunStageResult.stage_name.in_(expected_dependencies),
            )
        ).all()
        by_name = {row.stage_name: row for row in rows}

        outputs: list[DependencyOutputFingerprint] = []
        for dependency_name in expected_dependencies:
            row = by_name.get(dependency_name)
            if row is None:
                raise PersistedCheckpointError(
                    f"missing persisted dependency stage result: {dependency_name}"
                )
            if row.status != STAGE_SUCCESS_STATUS:
                raise PersistedCheckpointError(
                    f"dependency stage is not succeeded: {dependency_name}"
                )
            if row.output_fingerprint is None:
                raise PersistedCheckpointError(
                    f"dependency stage is missing output_fingerprint: {dependency_name}"
                )
            outputs.append(
                DependencyOutputFingerprint(
                    stage_name=dependency_name,
                    output_fingerprint=_stage_fingerprint(
                        row.output_fingerprint,
                        field_name=(
                            f"dependency {dependency_name} output_fingerprint"
                        ),
                    ),
                )
            )
        return tuple(outputs)

    def find_reusable(
        self,
        *,
        run_id: uuid.UUID,
        expected: CheckpointIdentity,
    ) -> ReusableCheckpoint | None:
        """Return an exact successful same-run checkpoint, otherwise a cache miss."""

        _require_run_id(run_id)
        if not isinstance(expected, CheckpointIdentity):
            raise PersistedCheckpointError(
                "expected must be CheckpointIdentity"
            )

        row = self._session.scalar(
            select(RunStageResult).where(
                RunStageResult.run_id == run_id,
                RunStageResult.stage_name == expected.stage_name,
            )
        )
        if row is None:
            return None

        candidate = CheckpointIdentity(
            stage_name=row.stage_name,
            stage_version=row.stage_version,
            input_hash=row.input_hash,
            config_hash=row.config_hash,
        )
        if not expected.is_eligible_match(candidate):
            return None
        if row.status != STAGE_SUCCESS_STATUS:
            return None
        if row.output_fingerprint is None:
            raise PersistedCheckpointError(
                "eligible succeeded checkpoint is missing output_fingerprint"
            )

        return ReusableCheckpoint(
            run_id=run_id,
            stage_result_id=row.id,
            identity=candidate,
            output_fingerprint=_stage_fingerprint(
                row.output_fingerprint,
                field_name="checkpoint output_fingerprint",
            ),
        )


def _require_run_id(run_id: uuid.UUID) -> None:
    if not isinstance(run_id, uuid.UUID):
        raise TypeError("run_id must be UUID")


def _validate_dependencies(dependencies: tuple[str, ...]) -> None:
    if not isinstance(dependencies, tuple):
        raise PersistedCheckpointError(
            "expected_dependencies must be an immutable tuple"
        )
    seen: set[str] = set()
    for dependency in dependencies:
        _validate_stage_identity(dependency, "checkpoint")
        if dependency in seen:
            raise PersistedCheckpointError(
                f"duplicate expected dependency: {dependency}"
            )
        seen.add(dependency)


def _validate_stage_identity(stage_name: str, stage_version: str) -> None:
    try:
        validate_stage_metadata(stage_name, stage_version, ())
    except StageContractError as exc:
        raise PersistedCheckpointError(str(exc)) from exc


def _stage_fingerprint(
    value: str,
    *,
    field_name: str,
) -> StageFingerprint:
    try:
        return StageFingerprint(value=value)
    except StageContractError as exc:
        raise PersistedCheckpointError(
            f"invalid persisted {field_name}"
        ) from exc
