from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO, cast

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.adapters.pipeline_context import (
    PipelineContextAssemblyError,
    SqlAlchemyPipelineContextAdapter,
)
from backend.app.db.session import engine
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.domain import (
    ArtifactRef,
    ArtifactStat,
    ConfigRef,
    CorrelationMetadata,
    ResolvedConfigBinding,
    RunMode,
    SnapshotLayerKind,
)

WORKING_SRID = 32637


@dataclass(frozen=True, slots=True)
class DummyRoadConfig:
    version: str


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


class RecordingConfigResolver:
    def __init__(self) -> None:
        self.schema_version: str | None = None
        self.source: ConfigRef | None = None
        self.observed_target_population: object = None

    def resolve(
        self,
        *,
        config_json: Mapping[str, object],
        schema_version: str,
        source: ConfigRef,
    ) -> tuple[ResolvedConfigBinding, ...]:
        self.schema_version = schema_version
        self.source = source
        self.observed_target_population = config_json.get("target_population")
        mutable_config = cast(dict[str, object], config_json)
        mutable_config["mutated_by_resolver"] = True
        return (
            ResolvedConfigBinding(
                stage_name="roads",
                source=source,
                value=DummyRoadConfig(version="test-v1"),
            ),
        )


def _migrate_to_head() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")


def _truncate_state() -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    _migrate_to_head()


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    _truncate_state()
    yield
    _truncate_state()


def _project(
    session: Session,
    *,
    working_srid: int = WORKING_SRID,
    boundary_srid: int | None = WORKING_SRID,
) -> Project:
    boundary = None
    if boundary_srid is not None:
        boundary = WKTElement(
            "MULTIPOLYGON(((0 0, 100 0, 100 100, 0 100, 0 0)))",
            srid=boundary_srid,
        )
    project = Project(
        name="Pipeline context adapter",
        working_srid=working_srid,
        boundary=boundary,
        boundary_metadata={},
    )
    session.add(project)
    session.flush()
    return project


def _dataset_version(
    session: Session,
    project: Project,
    *,
    kind: str,
    version: int = 1,
    status: str = "ready",
) -> DatasetVersion:
    dataset = Dataset(project_id=project.id, kind=kind)
    session.add(dataset)
    session.flush()
    dataset_version = DatasetVersion(
        dataset_id=dataset.id,
        version=version,
        status=status,
        checksum_sha256="a" * 64,
        source_metadata={"source": f"{kind}.fixture"},
    )
    session.add(dataset_version)
    session.flush()
    return dataset_version


def _run(
    session: Session,
    project: Project,
    *,
    working_srid: int = WORKING_SRID,
    dataset_versions: tuple[DatasetVersion, ...] = (),
) -> GenerationRun:
    run = GenerationRun(
        project_id=project.id,
        status="queued",
        mode="EXPANSION",
        seed=2026,
        working_srid=working_srid,
        config_json={"target_population": 10_000},
        config_schema_version="test-v1",
    )
    run.dataset_versions.extend(dataset_versions)
    session.add(run)
    session.commit()
    return run


def _adapter(
    session: Session,
    resolver: RecordingConfigResolver | None = None,
) -> tuple[SqlAlchemyPipelineContextAdapter, RecordingConfigResolver]:
    selected_resolver = resolver or RecordingConfigResolver()
    return (
        SqlAlchemyPipelineContextAdapter(
            session=session,
            artifact_store=FakeArtifactStore(),
            config_resolver=selected_resolver,
        ),
        selected_resolver,
    )


def test_adapter_assembles_persisted_run_into_core_pipeline_context() -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session)
        roads = _dataset_version(session, project, kind="roads", version=2)
        dem = _dataset_version(session, project, kind="dem")
        run = _run(
            session,
            project,
            dataset_versions=(dem, roads),
        )
        adapter, resolver = _adapter(session)

        correlation = CorrelationMetadata(
            correlation_id="generation-job-1",
            job_id="job-1",
        )
        context = adapter.assemble(run.id, correlation=correlation)

        assert context.run.run_id == run.id
        assert context.run.mode is RunMode.EXPANSION
        assert context.run.seed == 2026
        assert context.run.working_srid == WORKING_SRID
        assert context.run.correlation == correlation

        assert context.snapshot.snapshot_id == run.id
        assert context.snapshot.project.project_id == project.id
        assert context.snapshot.settings.working_srid == WORKING_SRID
        assert context.snapshot.boundary.kind is SnapshotLayerKind.BOUNDARY
        assert context.snapshot.boundary.source_ref.startswith(
            f"project-boundary:{project.id}:sha256:"
        )
        first_boundary_ref = context.snapshot.boundary.source_ref
        assert len(context.snapshot.roads) == 1
        assert context.snapshot.roads[0].source_ref == (
            f"dataset-version:{roads.id}:v2"
        )
        assert len(context.snapshot.dem) == 1
        assert context.snapshot.dem[0].source_ref == (
            f"dataset-version:{dem.id}:v1"
        )

        assert context.configured_stage_names == ("roads",)
        assert context.require_config("roads", DummyRoadConfig) == (
            DummyRoadConfig(version="test-v1")
        )
        assert resolver.schema_version == "test-v1"
        assert resolver.source == context.run.config_refs[0]
        assert resolver.observed_target_population == 10_000

        session.expire(run, ["config_json"])
        assert run.config_json == {"target_population": 10_000}

        session.expire(project, ["boundary"])
        reloaded_context = adapter.assemble(run.id, correlation=correlation)
        assert reloaded_context.snapshot.boundary.source_ref == first_boundary_ref


def test_adapter_default_correlation_is_stable_and_infrastructure_neutral() -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session)
        run = _run(session, project)
        adapter, _resolver = _adapter(session)

        context = adapter.assemble(run.id)

        assert context.run.correlation == CorrelationMetadata(
            correlation_id=f"generation-run:{run.id}"
        )


@pytest.mark.parametrize(
    ("kind", "expected_kind"),
    [
        ("buildings", SnapshotLayerKind.BUILDINGS),
        ("facilities", SnapshotLayerKind.FACILITIES),
        ("landuse", SnapshotLayerKind.LANDUSE),
        ("zones", SnapshotLayerKind.ZONES),
        ("water", SnapshotLayerKind.WATER),
        ("constraints", SnapshotLayerKind.CONSTRAINTS),
        ("demography", SnapshotLayerKind.DEMOGRAPHY),
    ],
)
def test_adapter_maps_supported_dataset_kinds_to_core_snapshot_layers(
    kind: str,
    expected_kind: SnapshotLayerKind,
) -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session)
        version = _dataset_version(session, project, kind=kind)
        run = _run(session, project, dataset_versions=(version,))
        adapter, _resolver = _adapter(session)

        context = adapter.assemble(run.id)

        matching = tuple(
            ref
            for ref in context.snapshot.layer_refs
            if ref.kind is expected_kind
        )
        assert len(matching) == 1
        assert matching[0].source_ref == f"dataset-version:{version.id}:v1"


def test_adapter_rejects_unready_or_unknown_dataset_versions() -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session)
        unready = _dataset_version(
            session,
            project,
            kind="roads",
            status="processing",
        )
        run = _run(session, project, dataset_versions=(unready,))
        adapter, _resolver = _adapter(session)

        with pytest.raises(
            PipelineContextAssemblyError,
            match="dataset version is not ready",
        ):
            adapter.assemble(run.id)

    _truncate_state()

    with Session(engine, expire_on_commit=False) as session:
        project = _project(session)
        unknown = _dataset_version(session, project, kind="unknown-layer")
        run = _run(session, project, dataset_versions=(unknown,))
        adapter, _resolver = _adapter(session)

        with pytest.raises(
            PipelineContextAssemblyError,
            match="unsupported Dataset.kind",
        ):
            adapter.assemble(run.id)


def test_adapter_rejects_project_snapshot_crs_inconsistencies() -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session, working_srid=WORKING_SRID)
        run = _run(session, project, working_srid=3857)
        adapter, _resolver = _adapter(session)

        with pytest.raises(
            PipelineContextAssemblyError,
            match="working_srid does not match project",
        ):
            adapter.assemble(run.id)

    _truncate_state()

    with Session(engine, expire_on_commit=False) as session:
        project = _project(
            session,
            working_srid=WORKING_SRID,
            boundary_srid=3857,
        )
        run = _run(session, project)
        adapter, _resolver = _adapter(session)

        with pytest.raises(
            PipelineContextAssemblyError,
            match="project boundary SRID",
        ):
            adapter.assemble(run.id)


def test_adapter_requires_persisted_project_boundary_and_known_run() -> None:
    with Session(engine, expire_on_commit=False) as session:
        project = _project(session, boundary_srid=None)
        run = _run(session, project)
        adapter, _resolver = _adapter(session)

        with pytest.raises(
            PipelineContextAssemblyError,
            match="project boundary is required",
        ):
            adapter.assemble(run.id)

        missing_run_id = uuid.uuid4()
        with pytest.raises(
            PipelineContextAssemblyError,
            match=f"generation run does not exist: {missing_run_id}",
        ):
            adapter.assemble(missing_run_id)
