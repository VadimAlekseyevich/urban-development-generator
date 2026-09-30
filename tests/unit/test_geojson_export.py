from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import pytest

from backend.app.adapters import LocalArtifactStore
from backend.app.application.geojson_exports import (
    GEOJSON_EXPORT_CONTENT_TYPE,
    GeoJsonExportClaim,
    GeoJsonExportClaimDisposition,
    GeoJsonExportError,
    GeoJsonExportJobContext,
    GeoJsonExportJobService,
    GeoJsonExportPipelineResult,
    GeoJsonExportRunStatus,
    GeoJsonExportSpec,
)
from backend.app.application.vector_layers import VectorFeature, VectorPage
from backend.app.services.geojson_export import StreamingGeoJsonExportPipeline
from core.urban_generator.domain import ArtifactRef, ArtifactState
from core.urban_generator.domain.errors import DataError, TransientError
from worker.settings import WorkerSettings
from worker.tasks import run_geojson_export

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
DATASET = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")
JOB = uuid.UUID("44444444-4444-4444-8444-444444444444")
BBOX = (-0.2, 51.45, 0.0, 51.6)


def _spec(**overrides: object) -> GeoJsonExportSpec:
    values: dict[str, object] = {
        "layer_id": "source.roads",
        "dataset_version_id": DATASET,
        "run_id": None,
        "bbox": BBOX,
        "max_features": 10,
    }
    values.update(overrides)
    return GeoJsonExportSpec(**values)  # type: ignore[arg-type]


def _context(spec: GeoJsonExportSpec | None = None) -> GeoJsonExportJobContext:
    return GeoJsonExportJobContext(
        job_id=JOB,
        project_id=PROJECT,
        spec=spec or _spec(),
        attempt_count=1,
        max_attempts=3,
    )


def _feature(number: int) -> VectorFeature:
    return VectorFeature(
        id=uuid.UUID(int=number),
        geometry={"type": "Point", "coordinates": [number / 10, 51.5]},
        properties={"number": number},
    )


class FakePageReader:
    def __init__(self, features: list[VectorFeature]) -> None:
        self.features = features
        self.calls: list[tuple[uuid.UUID | None, int]] = []

    def get_page(
        self,
        *,
        context: GeoJsonExportJobContext,
        after: uuid.UUID | None,
        limit: int,
    ) -> VectorPage:
        self.calls.append((after, limit))
        rows = [feature for feature in self.features if after is None or feature.id > after]
        selected = rows[:limit]
        truncated = len(rows) > limit
        return VectorPage(
            project_id=context.project_id,
            layer_id=context.spec.layer_id,
            definition_version="1",
            dataset_version_id=context.spec.dataset_version_id,
            run_id=context.spec.run_id,
            working_srid=3857,
            query_bbox=context.spec.bbox,
            presentation_crs="EPSG:4326",
            simplify_m=0.0,
            limit=limit,
            truncated=truncated,
            next_after=selected[-1].id if truncated else None,
            features=tuple(selected),
        )


def test_export_spec_is_owner_exact_bounded_and_idempotent() -> None:
    first = _spec()
    second = _spec()
    assert first.idempotency_key == second.idempotency_key
    assert first.owner_id == DATASET
    assert _spec(max_features=9).idempotency_key != first.idempotency_key

    run_spec = _spec(
        layer_id="generated.roads",
        dataset_version_id=None,
        run_id=RUN,
    )
    assert run_spec.owner_id == RUN

    for values in (
        {"dataset_version_id": None},
        {"run_id": RUN},
        {"layer_id": "generated.roads"},
        {"layer_id": "validation.violations", "dataset_version_id": None, "run_id": RUN},
        {"layer_id": "project.boundary", "dataset_version_id": None},
        {"bbox": (-181.0, 0.0, 1.0, 1.0)},
        {"bbox": (0.0, 0.0, 0.0, 1.0)},
        {"max_features": 100001},
    ):
        with pytest.raises(GeoJsonExportError):
            _spec(**values)


def test_pipeline_streams_deterministic_feature_collection_and_reuses_ready_blob(
    tmp_path,
) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    reader = FakePageReader([_feature(1), _feature(2), _feature(3)])
    pipeline = StreamingGeoJsonExportPipeline(store=store, page_reader=reader)

    first = pipeline.execute(_context(_spec(max_features=3)))
    assert first.feature_count == 3
    assert first.artifact.ref.state is ArtifactState.READY
    assert first.artifact.content_type == GEOJSON_EXPORT_CONTENT_TYPE
    assert reader.calls == [(None, 3)]

    with store.open(first.artifact.ref) as source:
        payload = json.load(source)
    assert payload["type"] == "FeatureCollection"
    assert [feature["id"] for feature in payload["features"]] == [
        str(uuid.UUID(int=1)),
        str(uuid.UUID(int=2)),
        str(uuid.UUID(int=3)),
    ]
    assert payload["features"][1]["properties"] == {"number": 2}

    reader.calls.clear()
    second = pipeline.execute(_context(_spec(max_features=3)))
    assert second.artifact.checksum == first.artifact.checksum
    assert second.artifact.size_bytes == first.artifact.size_bytes
    assert reader.calls == [(None, 3)]


def test_pipeline_refuses_partial_artifact_when_feature_cap_is_exceeded(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    pipeline = StreamingGeoJsonExportPipeline(
        store=store,
        page_reader=FakePageReader([_feature(1), _feature(2), _feature(3)]),
    )
    context = _context(_spec(max_features=2))
    with pytest.raises(DataError, match="feature cap"):
        pipeline.execute(context)

    filename = "source-roads-44444444.geojson"
    ref = ArtifactRef(f"exports/{PROJECT}/{JOB}/{filename}", ArtifactState.READY)
    with pytest.raises(KeyError):
        store.stat(ref)


@dataclass
class FakeExecutionRepository:
    claim_value: GeoJsonExportClaim
    retryable: bool = False
    completed: list[GeoJsonExportPipelineResult] | None = None
    failures: list[Exception] | None = None

    def __post_init__(self) -> None:
        self.completed = []
        self.failures = []

    def claim(self, *, job_id: uuid.UUID) -> GeoJsonExportClaim:
        assert job_id == JOB
        return self.claim_value

    def complete(
        self,
        *,
        context: GeoJsonExportJobContext,
        result: GeoJsonExportPipelineResult,
    ) -> None:
        assert self.completed is not None
        self.completed.append(result)

    def fail(self, *, context: GeoJsonExportJobContext, error) -> bool:
        assert self.failures is not None
        self.failures.append(error)
        return self.retryable


class FakePipeline:
    def __init__(self, outcome: GeoJsonExportPipelineResult | Exception) -> None:
        self.outcome = outcome
        self.calls = 0

    def execute(self, context: GeoJsonExportJobContext) -> GeoJsonExportPipelineResult:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_job_service_persists_success_and_transient_retry(tmp_path) -> None:
    context = _context()
    store = LocalArtifactStore(tmp_path / "store")
    temporary = ArtifactRef(
        f"exports/{PROJECT}/{JOB}/source-roads-44444444.geojson"
    )
    store.put(temporary, __import__("io").BytesIO(b"{}"), content_type=GEOJSON_EXPORT_CONTENT_TYPE)
    stat = store.promote(temporary)
    pipeline_result = GeoJsonExportPipelineResult(artifact=stat, feature_count=2)
    claim = GeoJsonExportClaim(
        disposition=GeoJsonExportClaimDisposition.STARTED,
        attempt_count=1,
        max_attempts=3,
        context=context,
    )
    repository = FakeExecutionRepository(claim)
    success = GeoJsonExportJobService(
        repository=repository,
        pipeline=FakePipeline(pipeline_result),
    ).run(job_id=JOB)
    assert success.status is GeoJsonExportRunStatus.SUCCEEDED
    assert success.feature_count == 2
    assert repository.completed == [pipeline_result]

    retry_repository = FakeExecutionRepository(claim, retryable=True)
    failed = GeoJsonExportJobService(
        repository=retry_repository,
        pipeline=FakePipeline(TransientError("storage unavailable")),
    ).run(job_id=JOB)
    assert failed.status is GeoJsonExportRunStatus.FAILED
    assert failed.retryable is True
    assert retry_repository.failures is not None
    assert isinstance(retry_repository.failures[0], TransientError)


def test_worker_registers_geojson_export_task() -> None:
    assert run_geojson_export in WorkerSettings.functions
