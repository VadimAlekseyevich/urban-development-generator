import uuid

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, box
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.db.generated_demography_writer import (
    GeneratedDemographyImmutableError,
    GeneratedDemographyPersistenceError,
    SqlAlchemyGeneratedDemographyWriter,
)
from backend.app.db.session import engine
from backend.app.models.generated_entity import GeneratedBlock, GeneratedZone
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.demography import (
    AgeGroupPopulation,
    BlockDemographicAggregate,
    DemographicAggregationResult,
    DemographicAggregationTotals,
    ZoneDemographicAggregate,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857


def _migrate_to_head() -> None:
    command.upgrade(Config("alembic.ini"), "head")


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


def _groups(child: int, adult: int) -> tuple[AgeGroupPopulation, ...]:
    return (
        AgeGroupPopulation(code="child", min_age=0, max_age=17, residents=child),
        AgeGroupPopulation(code="adult", min_age=18, max_age=None, residents=adult),
    )


def _fixture(*, status: str = "running") -> tuple[uuid.UUID, uuid.UUID]:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="Demography persistence",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            run = GenerationRun(
                project_id=project.id,
                status=status,
                mode="EXPANSION",
                seed=42,
                working_srid=WORKING_SRID,
                config_json={},
                config_schema_version="1",
                commit_sha="a" * 40 if status == "succeeded" else None,
                metrics_json={"existing": {"value": 1}},
            )
            session.add(run)
            session.flush()

            zone_geometry = box(0, 0, 1000, 500)
            zone = GeneratedZone(
                run_id=run.id,
                zone_class=ZoneClass.RESIDENTIAL.value,
                area_m2=float(zone_geometry.area),
                attributes_json={},
                diagnostics_json={},
                geometry=from_shape(
                    MultiPolygon([zone_geometry]),
                    srid=WORKING_SRID,
                ),
            )
            session.add(zone)
            session.flush()

            for index, (key, geom) in enumerate(
                (
                    ("block-1", box(0, 0, 200, 500)),
                    ("block-2", box(200, 0, 1000, 500)),
                )
            ):
                session.add(
                    GeneratedBlock(
                        run_id=run.id,
                        block_key=key,
                        zone_id=zone.id,
                        area_m2=float(geom.area),
                        association_status="ASSOCIATED",
                        attributes_json={
                            "zone_class": ZoneClass.RESIDENTIAL.value,
                            "existing": index,
                        },
                        geometry=from_shape(geom, srid=WORKING_SRID),
                    )
                )
            return run.id, zone.id


def _aggregation(zone_id: uuid.UUID) -> DemographicAggregationResult:
    return DemographicAggregationResult(
        scenario_version="demography-v1",
        scenario_fingerprint="a" * 64,
        employment_config_version="employment-v1",
        employment_config_fingerprint="b" * 64,
        blocks=(
            BlockDemographicAggregate(
                block_id="block-1",
                zone_id=str(zone_id),
                zone_class=ZoneClass.RESIDENTIAL,
                building_count=1,
                population=20,
                age_groups=_groups(5, 15),
                jobs_estimate=2.0,
            ),
            BlockDemographicAggregate(
                block_id="block-2",
                zone_id=str(zone_id),
                zone_class=ZoneClass.RESIDENTIAL,
                building_count=1,
                population=80,
                age_groups=_groups(25, 55),
                jobs_estimate=8.0,
            ),
        ),
        zones=(
            ZoneDemographicAggregate(
                zone_id=str(zone_id),
                zone_class=ZoneClass.RESIDENTIAL,
                block_count=2,
                building_count=2,
                population=100,
                age_groups=_groups(30, 70),
                jobs_estimate=10.0,
            ),
        ),
        totals=DemographicAggregationTotals(
            building_count=2,
            block_count=2,
            zone_count=1,
            population=100,
            age_groups=_groups(30, 70),
            jobs_estimate=10.0,
        ),
    )


def test_writer_persists_block_and_run_demography_without_losing_json() -> None:
    run_id, zone_id = _fixture()

    result = SqlAlchemyGeneratedDemographyWriter().replace(
        run_id=run_id,
        aggregation=_aggregation(zone_id),
    )

    assert result.updated_block_rows == 2
    assert result.metrics.totals.population == 100
    with Session(engine) as session:
        run = session.get(GenerationRun, run_id)
        assert run is not None
        assert run.metrics_json is not None
        assert run.metrics_json["existing"]["value"] == 1
        totals = run.metrics_json["demography"]
        assert totals["population"] == 100
        assert totals["jobs_estimate"] == pytest.approx(10.0)
        assert totals["population_density_per_km2"] == pytest.approx(200.0)
        assert totals["age_groups"][0]["share"] == pytest.approx(0.3)

        blocks = session.scalars(
            select(GeneratedBlock)
            .where(GeneratedBlock.run_id == run_id)
            .order_by(GeneratedBlock.block_key)
        ).all()
        assert blocks[0].attributes_json["existing"] == 0
        assert blocks[0].attributes_json["demography"]["population"] == 20
        assert blocks[0].attributes_json["demography"][
            "population_density_per_km2"
        ] == pytest.approx(200.0)
        assert blocks[1].attributes_json["demography"]["jobs_estimate"] == pytest.approx(
            8.0
        )


def test_writer_rejects_block_id_mismatch_without_partial_update() -> None:
    run_id, zone_id = _fixture()
    aggregation = _aggregation(zone_id)
    bad = DemographicAggregationResult(
        scenario_version=aggregation.scenario_version,
        scenario_fingerprint=aggregation.scenario_fingerprint,
        employment_config_version=aggregation.employment_config_version,
        employment_config_fingerprint=aggregation.employment_config_fingerprint,
        blocks=(aggregation.blocks[0],),
        zones=(
            ZoneDemographicAggregate(
                zone_id=str(zone_id),
                zone_class=ZoneClass.RESIDENTIAL,
                block_count=1,
                building_count=1,
                population=20,
                age_groups=_groups(5, 15),
                jobs_estimate=2.0,
            ),
        ),
        totals=DemographicAggregationTotals(
            building_count=1,
            block_count=1,
            zone_count=1,
            population=20,
            age_groups=_groups(5, 15),
            jobs_estimate=2.0,
        ),
    )

    with pytest.raises(GeneratedDemographyPersistenceError, match="identical block ids"):
        SqlAlchemyGeneratedDemographyWriter().replace(
            run_id=run_id,
            aggregation=bad,
        )

    with Session(engine) as session:
        blocks = session.scalars(
            select(GeneratedBlock).where(GeneratedBlock.run_id == run_id)
        ).all()
        assert all("demography" not in block.attributes_json for block in blocks)


def test_writer_rejects_successful_run() -> None:
    run_id, zone_id = _fixture(status="succeeded")

    with pytest.raises(GeneratedDemographyImmutableError, match="immutable"):
        SqlAlchemyGeneratedDemographyWriter().replace(
            run_id=run_id,
            aggregation=_aggregation(zone_id),
        )
