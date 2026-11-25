from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass

import pyogrio
import pytest

from backend.app.adapters import LocalArtifactStore
from backend.app.application.geopackage_exports import (
    GEOPACKAGE_EXPORT_CONTENT_TYPE,
    GeoPackageExportClaim,
    GeoPackageExportClaimDisposition,
    GeoPackageExportError,
    GeoPackageExportJobContext,
    GeoPackageExportJobService,
    GeoPackageExportPipelineResult,
    GeoPackageExportRunStatus,
    GeoPackageExportSpec,
    GeoPackageLayerCount,
)
from backend.app.application.vector_layers import VectorFeature, VectorPage
from backend.app.services.geopackage_export import StreamingGeoPackageExportPipeline
from core.urban_generator.domain import ArtifactRef, ArtifactStat, ArtifactState
from core.urban_generator.domain.errors import DataError, TransientError
from worker.settings import WorkerSettings
from worker.tasks import run_geopackage_export

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
DATASET = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")
JOB = uuid.UUID("44444444-4444-4444-8444-444444444444")
BBOX = (-0.2, 51.45, 0.0, 51.6)


def _spec(**overrides: object) -> GeoPackageExportSpec:
    values: dict[str, object] = {
        "layer_ids": ("source.roads", "source.buildings"),
        "dataset_version_id": DATASET,
        "run_id": None,
        "bbox": BBOX,
        "max_features_per_layer": 10,
        "max_total_features": 20,
    }
    values.update(overrides)
    return GeoPackageExportSpec(**values)  # type: ignore[arg-type]


def _context(spec: GeoPackageExportSpec | None = None) -> GeoPackageExportJobContext:
    return GeoPackageExportJobContext(
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
        properties={"number": number, "nested": {"value": number}},
    )


class FakePageReader:
    def __init__(self, features: dict[str, list[VectorFeature]]) -> None:
        self.features = features
        self.calls: list[tuple[str, uuid.UUID | None, int]] = []

    def get_page(
        self,
        *,
        context: GeoPackageExportJobContext,
        layer_id: str,
        after: uuid.UUID | None,
        limit: int,
    ) -> VectorPage:
        self.calls.append((layer_id, after, limit))
        rows = [
            feature
            for feature in self.features.get(layer_id, [])
            if after is None or feature.id > after
        ]
        selected = rows[:limit]
        truncated = len(rows) > limit
        return VectorPage(
            project_id=context.project_id,
            layer_id=layer_id,
            definition_version="1",
            dataset_version_id=(
                context.spec.dataset_version_id
                if layer_id.startswith("source.")
                else None
            ),
            run_id=(
                context.spec.run_id
                if not layer_id.startswith("source.")
                else None
            ),
            working_srid=3857,
            query_bbox=context.spec.bbox,
            presentation_crs="EPSG:4326",
            simplify_m=0.0,
            limit=limit,
            truncated=truncated,
            next_after=selected[-1].id if truncated else None,
            features=tuple(selected),
        )


def test_spec_canonicalizes_multi_owner_selection_and_bounds() -> None:
    first = GeoPackageExportSpec(
        layer_ids=("generated.roads", "source.roads"),
        dataset_version_id=DATASET,
        run_id=RUN,
        bbox=BBOX,
        max_features_per_layer=10,
        max_total_features=20,
    )
    reordered = GeoPackageExportSpec(
        layer_ids=("source.roads", "generated.roads"),
        dataset_version_id=DATASET,
        run_id=RUN,
        bbox=BBOX,
        max_features_per_layer=10,
        max_total_features=20,
    )
    assert first.layer_ids == ("source.roads", "generated.roads")
    assert first.idempotency_key == reordered.idempotency_key

    invalid_requests = (
        {"layer_ids": ()},
        {"layer_ids": ("source.roads", "source.roads")},
        {"layer_ids": ("validation.violations",), "dataset_version_id": None, "run_id": RUN},
        {"layer_ids": ("project.boundary",), "dataset_version_id": None},
        {"layer_ids": ("generated.roads",), "dataset_version_id": DATASET, "run_id": RUN},
        {"dataset_version_id": None},
        {"run_id": RUN},
        {"bbox": (-181.0, 0.0, 1.0, 1.0)},
        {"max_features_per_layer": 100001},
        {"max_total_features": 500001},
    )
    for values in invalid_requests:
        with pytest.raises(GeoPackageExportError):
            _spec(**values)


def test_pipeline_writes_multiple_layers_and_recovers_ready_artifact(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    reader = FakePageReader(
        {
            "source.roads": [_feature(1), _feature(2)],
            "source.buildings": [_feature(3)],
        }
    )
    pipeline = StreamingGeoPackageExportPipeline(store=store, page_reader=reader)
    context = _context()

    first = pipeline.execute(context)
    assert first.artifact.ref.state is ArtifactState.READY
    assert first.artifact.content_type == GEOPACKAGE_EXPORT_CONTENT_TYPE
    assert first.layer_counts == (
        GeoPackageLayerCount("source.buildings", 1),
        GeoPackageLayerCount("source.roads", 2),
    )

    local = tmp_path / "result.gpkg"
    with store.open(first.artifact.ref) as source, local.open("wb") as target:
        shutil.copyfileobj(source, target)
    assert {str(row[0]) for row in pyogrio.list_layers(local)} == {
        "source_buildings",
        "source_roads",
    }
    roads = pyogrio.read_dataframe(local, layer="source_roads")
    assert list(roads["feature_id"]) == [str(uuid.UUID(int=1)), str(uuid.UUID(int=2))]
    assert json.loads(roads["properties_json"].iloc[1]) == {
        "nested": {"value": 2},
        "number": 2,
    }

    reader.calls.clear()
    second = pipeline.execute(context)
    assert second.artifact.checksum == first.artifact.checksum
    assert second.layer_counts == first.layer_counts
    assert reader.calls == []


def test_pipeline_keeps_empty_selected_layer_and_rejects_cap_overflow(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "store")
    empty_reader = FakePageReader({"source.roads": [_feature(1)]})
    pipeline = StreamingGeoPackageExportPipeline(
        store=store,
        page_reader=empty_reader,
    )
    first = pipeline.execute(_context())
    assert first.layer_counts[0] == GeoPackageLayerCount("source.buildings", 0)

    overflow_store = LocalArtifactStore(tmp_path / "overflow")
    overflow = StreamingGeoPackageExportPipeline(
        store=overflow_store,
        page_reader=FakePageReader(
            {"source.roads": [_feature(1), _feature(2), _feature(3)]}
        ),
    )
    spec = _spec(
        layer_ids=("source.roads",),
        max_features_per_layer=2,
        max_total_features=20,
    )
    with pytest.raises(DataError, match="feature cap"):
        overflow.execute(_context(spec))

    ref = ArtifactRef(
        f"exports/{PROJECT}/{JOB}/urban-layers-44444444.gpkg",
        ArtifactState.READY,
    )
    with pytest.raises(KeyError):
        overflow_store.stat(ref)


@dataclass
class FakeExecutionRepository:
    claim_value: GeoPackageExportClaim
    retryable: bool = False
    completed: list[GeoPackageExportPipelineResult] | None = None
    failures: list[Exception] | None = None

    def __post_init__(self) -> None:
        self.completed = []
        self.failures = []

    def claim(self, *, job_id: uuid.UUID) -> GeoPackageExportClaim:
        assert job_id == JOB
        return self.claim_value

    def complete(
        self,
        *,
        context: GeoPackageExportJobContext,
        result: GeoPackageExportPipelineResult,
    ) -> None:
        assert self.completed is not None
        self.completed.append(result)

    def fail(self, *, context: GeoPackageExportJobContext, error) -> bool:
        assert self.failures is not None
        self.failures.append(error)
        return self.retryable


class FakePipeline:
    def __init__(self, outcome: GeoPackageExportPipelineResult | Exception) -> None:
        self.outcome = outcome

    def execute(
        self,
        context: GeoPackageExportJobContext,
    ) -> GeoPackageExportPipelineResult:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_job_service_persists_success_and_transient_retry() -> None:
    context = _context()
    stat = ArtifactStat(
        ref=ArtifactRef(
            f"exports/{PROJECT}/{JOB}/urban-layers-44444444.gpkg",
            ArtifactState.READY,
        ),
        size_bytes=123,
        checksum="sha256:" + "a" * 64,
        content_type=GEOPACKAGE_EXPORT_CONTENT_TYPE,
    )
    pipeline_result = GeoPackageExportPipelineResult(
        artifact=stat,
        layer_counts=(
            GeoPackageLayerCount("source.buildings", 1),
            GeoPackageLayerCount("source.roads", 2),
        ),
    )
    claim = GeoPackageExportClaim(
        disposition=GeoPackageExportClaimDisposition.STARTED,
        attempt_count=1,
        max_attempts=3,
        context=context,
    )
    repository = FakeExecutionRepository(claim)
    success = GeoPackageExportJobService(
        repository=repository,
        pipeline=FakePipeline(pipeline_result),
    ).run(job_id=JOB)
    assert success.status is GeoPackageExportRunStatus.SUCCEEDED
    assert success.total_feature_count == 3
    assert repository.completed == [pipeline_result]

    retry_repository = FakeExecutionRepository(claim, retryable=True)
    failed = GeoPackageExportJobService(
        repository=retry_repository,
        pipeline=FakePipeline(TransientError("storage unavailable")),
    ).run(job_id=JOB)
    assert failed.status is GeoPackageExportRunStatus.FAILED
    assert failed.retryable is True
    assert retry_repository.failures is not None
    assert isinstance(retry_repository.failures[0], TransientError)


def test_worker_registers_geopackage_export_task() -> None:
    assert run_geopackage_export in WorkerSettings.functions
