"""Real PostGIS -> immutable core hard-exclusion inputs with run authorization."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.adapters.hard_exclusion_source_reader import (
    HardExclusionSourceReadError,
    SqlAlchemyHardExclusionSourceReader,
)
from backend.app.adapters.pipeline_context import SqlAlchemyPipelineContextAdapter
from backend.app.db.session import engine
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceConstraint, SourceWater
from core.urban_generator.domain import (
    ProjectRef,
    ProjectSettings,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)
from core.urban_generator.suitability import (
    SuitabilityGridSpec,
    build_hard_exclusion_mask,
)

SRID = 32637
BOUNDARY_WKT = "MULTIPOLYGON(((0 0, 100 0, 100 100, 0 100, 0 0)))"


def _session() -> Session:
    return Session(engine, expire_on_commit=False)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _project(session: Session) -> Project:
    project = Project(
        name="Hard exclusion source reader",
        working_srid=SRID,
        boundary_metadata={},
        boundary=WKTElement(BOUNDARY_WKT, srid=SRID),
    )
    session.add(project)
    session.flush()
    return project


def _version(
    session: Session, project: Project, *, kind: str
) -> DatasetVersion:
    dataset = Dataset(project_id=project.id, kind=kind)
    session.add(dataset)
    session.flush()
    version = DatasetVersion(
        dataset_id=dataset.id,
        version=1,
        status="processing",
        checksum_sha256="b" * 64,
        source_metadata={"source": "synthetic-postgis-fixture"},
    )
    session.add(version)
    session.flush()
    return version


def _water(
    session: Session,
    version: DatasetVersion,
    *,
    feature_id: str,
    geometry: str,
    srid: int = SRID,
) -> None:
    session.add(
        SourceWater(
            dataset_version_id=version.id,
            source_feature_id=feature_id,
            water_class="water",
            attributes_json={"source": "fixture"},
            geometry=WKTElement(geometry, srid=srid),
        )
    )


def _constraint(
    session: Session,
    version: DatasetVersion,
    *,
    feature_id: str,
    severity: str = "HARD",
    scope: str = "TERRITORY",
    geometry: str = (
        "MULTIPOLYGON(((30 30, 40 30, 40 40, 30 40, 30 30)))"
    ),
) -> None:
    session.add(
        SourceConstraint(
            dataset_version_id=version.id,
            source_feature_id=feature_id,
            constraint_code="protected_area",
            severity=severity,
            scope=scope,
            attributes_json={},
            geometry=WKTElement(geometry, srid=SRID),
        )
    )


def _run(
    session: Session,
    project: Project,
    *versions: DatasetVersion,
    mode: str = "EXPANSION",
) -> GenerationRun:
    run = GenerationRun(
        project_id=project.id,
        mode=mode,
        status="queued",
        seed=2026,
        working_srid=SRID,
        config_json={},
        config_schema_version="fixture-v1",
    )
    run.dataset_versions.extend(versions)
    session.add(run)
    session.commit()
    return run


def _snapshot(
    project: Project,
    run: GenerationRun,
    *,
    water: tuple[DatasetVersion, ...] = (),
    constraints: tuple[DatasetVersion, ...] = (),
    boundary_source_ref: str | None = None,
) -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=run.id,
        project=ProjectRef(project_id=project.id),
        settings=ProjectSettings(working_srid=SRID),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref=(
                boundary_source_ref
                or SqlAlchemyPipelineContextAdapter._boundary_source_ref(project)
            ),
        ),
        water=tuple(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.WATER,
                source_ref=f"dataset-version:{version.id}:v{version.version}",
            )
            for version in water
        ),
        constraints=tuple(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.CONSTRAINTS,
                source_ref=f"dataset-version:{version.id}:v{version.version}",
            )
            for version in constraints
        ),
    )


def test_postgis_reader_resolves_ready_water_and_territory_hard_only() -> None:
    with _session() as session:
        project = _project(session)
        water = _version(session, project, kind="water")
        restricted = _version(session, project, kind="constraints")
        _water(
            session, water, feature_id="b-stream",
            geometry="LINESTRING(40 40, 70 70)",
        )
        _water(
            session, water, feature_id="a-lake",
            geometry="MULTIPOLYGON(((10 10, 20 10, 20 20, 10 20, 10 10)))",
        )
        _water(
            session, water, feature_id="outside",
            geometry="MULTIPOLYGON(((200 200, 210 200, 210 210, 200 210, 200 200)))",
        )
        _constraint(session, restricted, feature_id="a-protected")
        _constraint(
            session, restricted, feature_id="b-soft", severity="SOFT",
        )
        _constraint(
            session, restricted, feature_id="c-building",
            severity="HARD", scope="BUILDING",
        )
        session.flush()
        water.status = "ready"
        restricted.status = "ready"
        session.flush()
        run = _run(session, project, water, restricted, mode="FROM_SCRATCH")
        snapshot = _snapshot(
            project, run, water=(water,), constraints=(restricted,)
        )

    reader = SqlAlchemyHardExclusionSourceReader(session_factory=_session)
    first = reader.read(snapshot=snapshot)
    second = reader.read(snapshot=snapshot)
    assert first == second
    assert first.source_feature_count == 3
    assert first.boundary.working_srid == SRID
    assert first.boundary.geometry.area == 10_000
    assert len(first.geometry_layers) == 2
    water_layer, hard_layer = first.geometry_layers
    assert water_layer.code == f"water.{water.id.hex}"
    assert hard_layer.code == f"constraints.{restricted.id.hex}"
    assert water_layer.working_srid == hard_layer.working_srid == SRID
    assert tuple(g.geom_type for g in water_layer.geometries) == (
        "MultiPolygon", "LineString"
    )
    assert len(hard_layer.geometries) == 1

    # The types are directly accepted by the canonical core hard mask.
    grid = SuitabilityGridSpec(
        working_srid=SRID, bounds=(0, 0, 100, 100), width=10, height=10
    )
    mask = build_hard_exclusion_mask(
        grid=grid, boundary=first.boundary,
        geometry_layers=first.geometry_layers,
    )
    assert mask.excluded_count > 0
    assert mask.developable_count > 0
    assert set(mask.source_codes) == {
        "boundary", water_layer.code, hard_layer.code
    }


def test_empty_source_vectors_keep_real_boundary_only() -> None:
    with _session() as session:
        project = _project(session)
        run = _run(session, project)
        snapshot = _snapshot(project, run)
    result = SqlAlchemyHardExclusionSourceReader(
        session_factory=_session
    ).read(snapshot=snapshot)
    assert result.source_feature_count == 0
    assert result.geometry_layers == ()
    assert result.boundary.geometry.area == 10_000


def test_reader_rejects_foreign_or_unlinked_ready_dataset_version() -> None:
    with _session() as session:
        project = _project(session)
        linked = _version(session, project, kind="water")
        unlinked = _version(session, project, kind="water")
        _water(
            session, linked, feature_id="linked",
            geometry="LINESTRING(0 0, 20 20)",
        )
        _water(
            session, unlinked, feature_id="unlinked",
            geometry="LINESTRING(0 0, 20 20)",
        )
        session.flush()
        linked.status = "ready"
        unlinked.status = "ready"
        session.flush()
        run = _run(session, project, linked)
        snapshot = _snapshot(project, run, water=(unlinked,))

    with pytest.raises(HardExclusionSourceReadError, match="not linked to run"):
        SqlAlchemyHardExclusionSourceReader(
            session_factory=_session
        ).read(snapshot=snapshot)


def test_reader_refuses_forged_boundary_or_duplicate_source_reference() -> None:
    with _session() as session:
        project = _project(session)
        version = _version(session, project, kind="water")
        _water(
            session, version, feature_id="inside",
            geometry="LINESTRING(10 10, 20 20)",
        )
        session.flush()
        version.status = "ready"
        session.flush()
        run = _run(session, project, version)
        bad_boundary = _snapshot(
            project, run, water=(version,),
            boundary_source_ref=f"project-boundary:{project.id}:sha256:{'0' * 64}",
        )
        repeated = _snapshot(
            project, run, water=(version, version)
        )

    reader = SqlAlchemyHardExclusionSourceReader(session_factory=_session)
    with pytest.raises(HardExclusionSourceReadError, match="fingerprint differs"):
        reader.read(snapshot=bad_boundary)
    with pytest.raises(HardExclusionSourceReadError, match="duplicate water"):
        reader.read(snapshot=repeated)


def test_reader_rejects_wrong_kind_and_processing_version() -> None:
    with _session() as session:
        project = _project(session)
        processing = _version(session, project, kind="water")
        wrong_kind = _version(session, project, kind="landuse")
        _water(
            session, processing, feature_id="pending",
            geometry="LINESTRING(10 10, 20 20)",
        )
        _water(
            session, wrong_kind, feature_id="incorrect-label",
            geometry="LINESTRING(10 10, 20 20)",
        )
        session.flush()
        wrong_kind.status = "ready"
        session.flush()
        run = _run(session, project, processing, wrong_kind)
        pending = _snapshot(project, run, water=(processing,))
        invalid_kind = _snapshot(project, run, water=(wrong_kind,))

    reader = SqlAlchemyHardExclusionSourceReader(session_factory=_session)
    with pytest.raises(HardExclusionSourceReadError, match="not ready"):
        reader.read(snapshot=pending)
    with pytest.raises(HardExclusionSourceReadError, match="wrong source kind"):
        reader.read(snapshot=invalid_kind)


def test_reader_fails_closed_on_total_cap_and_wrong_geometry_srid() -> None:
    with _session() as session:
        project = _project(session)
        water = _version(session, project, kind="water")
        _water(
            session, water, feature_id="one",
            geometry="LINESTRING(10 10, 20 20)",
        )
        _water(
            session, water, feature_id="two",
            geometry="LINESTRING(30 30, 40 40)",
        )
        session.flush()
        water.status = "ready"
        session.flush()
        run = _run(session, project, water)
        snapshot = _snapshot(project, run, water=(water,))

    reader = SqlAlchemyHardExclusionSourceReader(
        session_factory=_session, max_geometries=1
    )
    with pytest.raises(HardExclusionSourceReadError, match="configured limit"):
        reader.read(snapshot=snapshot)

    with _session() as session:
        project = _project(session)
        version = _version(session, project, kind="water")
        _water(
            session, version, feature_id="wrong-srid",
            geometry="LINESTRING(10 10, 20 20)", srid=3857,
        )
        session.flush()
        version.status = "ready"
        session.flush()
        run = _run(session, project, version)
        snapshot = _snapshot(project, run, water=(version,))
    with pytest.raises(HardExclusionSourceReadError, match="incompatible metric SRID"):
        SqlAlchemyHardExclusionSourceReader(
            session_factory=_session
        ).read(snapshot=snapshot)


def test_reader_rejects_ambiguous_point_hard_exclusion() -> None:
    with _session() as session:
        project = _project(session)
        water = _version(session, project, kind="water")
        _water(
            session, water, feature_id="point",
            geometry="POINT(10 10)",
        )
        session.flush()
        water.status = "ready"
        session.flush()
        run = _run(session, project, water)
        snapshot = _snapshot(project, run, water=(water,))

    with pytest.raises(HardExclusionSourceReadError, match="unsupported or invalid"):
        SqlAlchemyHardExclusionSourceReader(
            session_factory=_session
        ).read(snapshot=snapshot)


@pytest.mark.parametrize("value", [0, -1, True, 100_001, 1.0])
def test_reader_rejects_unsafe_feature_budget(value: object) -> None:
    with pytest.raises(ValueError, match="max_geometries"):
        SqlAlchemyHardExclusionSourceReader(max_geometries=value)  # type: ignore[arg-type]
