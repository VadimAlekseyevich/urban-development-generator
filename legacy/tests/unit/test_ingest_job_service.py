import uuid

import pytest

from backend.app.application.ingest import (
    IngestClaim,
    IngestClaimDisposition,
    IngestJobContext,
    IngestJobRunStatus,
    IngestJobService,
    IngestPipelineResult,
)
from backend.app.services.ingest_pipeline import IngestManifest, IngestSourceFormat
from backend.app.services.raster_normalization import RasterResampling
from core.urban_generator.domain import ArtifactRef, ArtifactStat, ArtifactState
from core.urban_generator.domain.errors import DataError, TransientError
from worker.settings import WorkerSettings
from worker.tasks import run_ingest


def _context(*, attempt_count: int = 1, max_attempts: int = 3) -> IngestJobContext:
    return IngestJobContext(
        job_id=uuid.uuid4(),
        dataset_version_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        dataset_kind="roads",
        checksum_sha256=None,
        source_metadata={"artifact_key": "uploads/source.geojson", "format": "geojson"},
        working_srid=3857,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
    )


def _source_stat() -> ArtifactStat:
    return ArtifactStat(
        ref=ArtifactRef("uploads/source.geojson", ArtifactState.READY),
        size_bytes=12,
        checksum="sha256:" + "a" * 64,
        content_type="application/geo+json",
    )


class _Repository:
    def __init__(self, claim: IngestClaim, *, retryable: bool = False) -> None:
        self.claim_value = claim
        self.retryable = retryable
        self.completed: list[IngestPipelineResult] = []
        self.failures: list[Exception] = []

    def claim(self, *, job_id: uuid.UUID, dataset_version_id: uuid.UUID) -> IngestClaim:
        return self.claim_value

    def complete(
        self,
        *,
        context: IngestJobContext,
        result: IngestPipelineResult,
    ) -> None:
        self.completed.append(result)

    def fail(self, *, context: IngestJobContext, error) -> bool:
        self.failures.append(error)
        return self.retryable


class _Pipeline:
    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.calls = 0

    def execute(self, context: IngestJobContext) -> IngestPipelineResult:
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _started_claim(context: IngestJobContext) -> IngestClaim:
    return IngestClaim(
        disposition=IngestClaimDisposition.STARTED,
        attempt_count=context.attempt_count,
        max_attempts=context.max_attempts,
        context=context,
    )


def test_successful_attempt_completes_repository() -> None:
    context = _context()
    repository = _Repository(_started_claim(context))
    pipeline_result = IngestPipelineResult(
        source_artifact=_source_stat(),
        details={"kind": "vector", "inserted_rows": 2},
    )
    pipeline = _Pipeline(pipeline_result)
    service = IngestJobService(repository=repository, pipeline=pipeline)

    result = service.run(
        job_id=context.job_id,
        dataset_version_id=context.dataset_version_id,
    )

    assert result.status is IngestJobRunStatus.SUCCEEDED
    assert result.details == {"kind": "vector", "inserted_rows": 2}
    assert repository.completed == [pipeline_result]
    assert repository.failures == []
    assert pipeline.calls == 1


def test_already_ready_delivery_is_noop() -> None:
    context = _context(attempt_count=1)
    repository = _Repository(
        IngestClaim(
            disposition=IngestClaimDisposition.ALREADY_READY,
            attempt_count=1,
            max_attempts=3,
        )
    )
    pipeline = _Pipeline(IngestPipelineResult(source_artifact=_source_stat()))
    service = IngestJobService(repository=repository, pipeline=pipeline)

    result = service.run(
        job_id=context.job_id,
        dataset_version_id=context.dataset_version_id,
    )

    assert result.status is IngestJobRunStatus.ALREADY_READY
    assert pipeline.calls == 0
    assert repository.completed == []


@pytest.mark.parametrize(
    ("error", "repository_retryable", "expected_retryable"),
    [
        (DataError("bad vector source"), False, False),
        (TransientError("storage unavailable"), True, True),
    ],
)
def test_failure_is_persisted_with_explicit_retry_decision(
    error,
    repository_retryable: bool,
    expected_retryable: bool,
) -> None:
    context = _context()
    repository = _Repository(
        _started_claim(context),
        retryable=repository_retryable,
    )
    pipeline = _Pipeline(error)
    service = IngestJobService(repository=repository, pipeline=pipeline)

    result = service.run(
        job_id=context.job_id,
        dataset_version_id=context.dataset_version_id,
    )

    assert result.status is IngestJobRunStatus.FAILED
    assert result.retryable is expected_retryable
    assert repository.failures == [error]


def test_unexpected_pipeline_error_is_classified_transient() -> None:
    context = _context()
    repository = _Repository(_started_claim(context), retryable=True)
    service = IngestJobService(
        repository=repository,
        pipeline=_Pipeline(RuntimeError("unexpected")),
    )

    result = service.run(
        job_id=context.job_id,
        dataset_version_id=context.dataset_version_id,
    )

    assert result.status is IngestJobRunStatus.FAILED
    assert result.retryable is True
    assert isinstance(repository.failures[0], TransientError)


def test_manifest_parses_vector_and_raster_contracts() -> None:
    vector = IngestManifest.from_metadata(
        {
            "artifact_key": "uploads/roads.gpkg",
            "format": "gpkg",
            "layer": "roads",
            "geometry_family": "line",
        }
    )
    raster = IngestManifest.from_metadata(
        {
            "artifact_key": "uploads/dem.tif",
            "format": "geotiff",
            "target_resolution_m": 20,
            "clip_bounds": [0, 0, 100, 100],
            "resampling": "bilinear",
        }
    )

    assert vector.source_format is IngestSourceFormat.GPKG
    assert vector.layer == "roads"
    assert raster.source_format is IngestSourceFormat.GEOTIFF
    assert raster.target_resolution_m == 20.0
    assert raster.clip_bounds == (0.0, 0.0, 100.0, 100.0)
    assert raster.resampling is RasterResampling.BILINEAR


def test_manifest_rejects_mixed_vector_raster_options() -> None:
    with pytest.raises(DataError):
        IngestManifest.from_metadata(
            {
                "artifact_key": "uploads/roads.geojson",
                "format": "geojson",
                "target_resolution_m": 10,
            }
        )


def test_worker_registers_ingest_task() -> None:
    assert run_ingest in WorkerSettings.functions
