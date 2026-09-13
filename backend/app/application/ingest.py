from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from core.urban_generator.domain import ArtifactStat
from core.urban_generator.domain.errors import TransientError, UrbanGeneratorError

INGEST_JOB_TYPE = "ingest_dataset"


def ingest_idempotency_key(dataset_version_id: uuid.UUID) -> str:
    """Return the stable DB idempotency key for one DatasetVersion ingest."""
    if not isinstance(dataset_version_id, uuid.UUID):
        raise TypeError("dataset_version_id must be UUID")
    return f"dataset-version:{dataset_version_id}"


@dataclass(frozen=True, slots=True)
class IngestJobContext:
    """Immutable inputs resolved from authoritative DB state for one attempt."""

    job_id: uuid.UUID
    dataset_version_id: uuid.UUID
    project_id: uuid.UUID
    dataset_kind: str
    checksum_sha256: str | None
    source_metadata: dict[str, object]
    working_srid: int
    attempt_count: int
    max_attempts: int


class IngestClaimDisposition(StrEnum):
    STARTED = "started"
    ALREADY_READY = "already_ready"
    IN_PROGRESS = "in_progress"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class IngestClaim:
    disposition: IngestClaimDisposition
    attempt_count: int
    max_attempts: int
    context: IngestJobContext | None = None


@dataclass(frozen=True, slots=True)
class IngestPipelineResult:
    """Artifacts and JSON-safe diagnostics produced by one ingest attempt."""

    source_artifact: ArtifactStat
    derived_artifacts: tuple[ArtifactStat, ...] = ()
    details: dict[str, object] | None = None


class IngestPipeline(Protocol):
    def execute(self, context: IngestJobContext) -> IngestPipelineResult: ...


class IngestJobRepository(Protocol):
    def claim(
        self,
        *,
        job_id: uuid.UUID,
        dataset_version_id: uuid.UUID,
    ) -> IngestClaim: ...

    def complete(
        self,
        *,
        context: IngestJobContext,
        result: IngestPipelineResult,
    ) -> None: ...

    def fail(
        self,
        *,
        context: IngestJobContext,
        error: UrbanGeneratorError,
    ) -> bool:
        """Persist failure and return whether another attempt is allowed."""


class IngestJobRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    ALREADY_READY = "already_ready"
    IN_PROGRESS = "in_progress"
    EXHAUSTED = "exhausted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IngestJobRunResult:
    job_id: uuid.UUID
    dataset_version_id: uuid.UUID
    status: IngestJobRunStatus
    attempt_count: int
    max_attempts: int
    retryable: bool = False
    details: dict[str, object] | None = None


class IngestJobService:
    """Coordinate one retry-safe ingest attempt outside the HTTP request lifecycle."""

    def __init__(
        self,
        *,
        repository: IngestJobRepository,
        pipeline: IngestPipeline,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline

    def run(
        self,
        *,
        job_id: uuid.UUID,
        dataset_version_id: uuid.UUID,
    ) -> IngestJobRunResult:
        claim = self._repository.claim(
            job_id=job_id,
            dataset_version_id=dataset_version_id,
        )
        if claim.disposition is IngestClaimDisposition.ALREADY_READY:
            return IngestJobRunResult(
                job_id=job_id,
                dataset_version_id=dataset_version_id,
                status=IngestJobRunStatus.ALREADY_READY,
                attempt_count=claim.attempt_count,
                max_attempts=claim.max_attempts,
            )
        if claim.disposition is IngestClaimDisposition.IN_PROGRESS:
            return IngestJobRunResult(
                job_id=job_id,
                dataset_version_id=dataset_version_id,
                status=IngestJobRunStatus.IN_PROGRESS,
                attempt_count=claim.attempt_count,
                max_attempts=claim.max_attempts,
            )
        if claim.disposition is IngestClaimDisposition.EXHAUSTED:
            return IngestJobRunResult(
                job_id=job_id,
                dataset_version_id=dataset_version_id,
                status=IngestJobRunStatus.EXHAUSTED,
                attempt_count=claim.attempt_count,
                max_attempts=claim.max_attempts,
            )

        context = claim.context
        if context is None:
            raise RuntimeError("started ingest claim must include context")

        try:
            result = self._pipeline.execute(context)
            self._repository.complete(context=context, result=result)
        except UrbanGeneratorError as error:
            retryable = self._repository.fail(context=context, error=error)
            return IngestJobRunResult(
                job_id=job_id,
                dataset_version_id=dataset_version_id,
                status=IngestJobRunStatus.FAILED,
                attempt_count=context.attempt_count,
                max_attempts=context.max_attempts,
                retryable=retryable,
                details={
                    "error_class": error.category.value,
                    "error_code": error.code.value,
                },
            )
        except Exception as exc:
            error = TransientError(
                "ingest pipeline failed unexpectedly",
                details={"exception_type": type(exc).__name__},
            )
            retryable = self._repository.fail(context=context, error=error)
            return IngestJobRunResult(
                job_id=job_id,
                dataset_version_id=dataset_version_id,
                status=IngestJobRunStatus.FAILED,
                attempt_count=context.attempt_count,
                max_attempts=context.max_attempts,
                retryable=retryable,
                details={
                    "error_class": error.category.value,
                    "error_code": error.code.value,
                },
            )

        return IngestJobRunResult(
            job_id=job_id,
            dataset_version_id=dataset_version_id,
            status=IngestJobRunStatus.SUCCEEDED,
            attempt_count=context.attempt_count,
            max_attempts=context.max_attempts,
            details=dict(result.details or {}),
        )
