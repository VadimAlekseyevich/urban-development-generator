import uuid

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.shape import from_shape
from shapely.geometry import Point, box
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.db.infrastructure_ui_read_model_writer import (
    InfrastructureUiReadModelImmutableError,
    SqlAlchemyInfrastructureUiReadModelWriter,
)
from backend.app.db.session import SessionLocal, engine
from backend.app.models.generated_entity import GeneratedBlock, GeneratedInfrastructure
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import NetworkNodeRef, RawMetricId
from core.urban_generator.infrastructure import (
    BlockInfrastructureDemand,
    ExistingInfrastructureDiagnostics,
    ExistingInfrastructureFacility,
    ExistingInfrastructureFacilityRef,
    ExistingInfrastructureResult,
    InfrastructureAcceptedFacility,
    InfrastructureAccessibilityBatchResult,
    InfrastructureAccessibilityDiagnostics,
    InfrastructureAccessibilityMode,
    InfrastructureAccessibilityResult,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateRef,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureCoverageCacheEntry,
    InfrastructureDemandModel,
    InfrastructureDemandRef,
    InfrastructureDemandSummary,
    InfrastructureGreedyPlacementState,
    InfrastructurePlacementDemandState,
    InfrastructureType,
    UnmetDemandDiagnostics,
    UnmetDemandResult,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 32637
SNAPSHOT_ID = "roads:ui-read-model-v1"
TYPE_CODE = "school.general"


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


def _type() -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=TYPE_CODE,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=0.1,
        ),
        capacity=5.0,
        max_network_distance_m=1_000.0,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=100.0,
        target_site_area_m2=200.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,),
        ),
    )


def _unmet() -> UnmetDemandResult:
    demand = BlockInfrastructureDemand(
        block_id="block-a",
        zone_id="zone-a",
        zone_class=ZoneClass.PUBLIC,
        infrastructure_type_code=TYPE_CODE,
        infrastructure_category=InfrastructureCategory.EDUCATION,
        demographic_signal=DemographicDemandCategory.AGE_GROUP,
        demographic_group="child",
        source_signal_value=100.0,
        demand_rate=0.1,
        gross_demand=10.0,
        served_demand=2.0,
        unmet_demand=8.0,
    )
    return UnmetDemandResult(
        scenario_version="scenario-v1",
        scenario_fingerprint="a" * 64,
        demands=(demand,),
        summaries=(
            InfrastructureDemandSummary(
                infrastructure_type_code=TYPE_CODE,
                infrastructure_category=InfrastructureCategory.EDUCATION,
                demographic_signal=DemographicDemandCategory.AGE_GROUP,
                demographic_group="child",
                gross_demand=10.0,
                served_demand=2.0,
                unmet_demand=8.0,
                existing_capacity=4.0,
            ),
        ),
        diagnostics=UnmetDemandDiagnostics(
            block_count=1,
            infrastructure_type_count=1,
            demand_item_count=1,
            served_assignment_count=1,
            existing_facility_count=1,
        ),
    )


def _existing() -> ExistingInfrastructureResult:
    facility = ExistingInfrastructureFacility(
        facility_id="facility-a",
        source_ref="dataset:facilities-v1",
        source_feature_id="source-facility-a",
        infrastructure_type_code=TYPE_CODE,
        capacity=4.0,
        geometry=Point(0.0, 0.0),
        working_srid=WORKING_SRID,
        name="Existing school",
    )
    return ExistingInfrastructureResult(
        snapshot_id=SNAPSHOT_ID,
        facilities=(facility,),
        diagnostics=ExistingInfrastructureDiagnostics(
            input_count=1,
            mapped_count=1,
            skipped_unmapped_count=0,
            source_layer_count=1,
            infrastructure_type_count=1,
        ),
    )


def _existing_accessibility() -> InfrastructureAccessibilityBatchResult:
    demand_ref = InfrastructureDemandRef(
        block_id="block-a",
        infrastructure_type_code=TYPE_CODE,
    )
    return InfrastructureAccessibilityBatchResult(
        mode=InfrastructureAccessibilityMode.EXISTING_FACILITY,
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        reachable=(
            InfrastructureAccessibilityResult(
                snapshot_id=SNAPSHOT_ID,
                infrastructure_type_code=TYPE_CODE,
                demand_ref=demand_ref,
                facility_site_ref=ExistingInfrastructureFacilityRef(
                    facility_id="facility-a",
                    infrastructure_type_code=TYPE_CODE,
                ),
                demand_node=NetworkNodeRef("demand-node"),
                facility_site_node=NetworkNodeRef("existing-node"),
                max_network_distance_m=1_000.0,
                distance_m=100.0,
            ),
        ),
        unavailable=(),
        diagnostics=InfrastructureAccessibilityDiagnostics(
            subject_count=1,
            reachable_count=1,
            unavailable_count=0,
            demand_unsnapped_count=0,
            facility_site_unsnapped_count=0,
            both_unsnapped_count=0,
            no_path_within_max_distance_count=0,
            no_snapped_facility_site_count=0,
        ),
    )


def _placement() -> InfrastructureGreedyPlacementState:
    demand_ref = InfrastructureDemandRef(
        block_id="block-a",
        infrastructure_type_code=TYPE_CODE,
    )
    candidate_ref = InfrastructureCandidateRef(
        candidate_id="candidate-a",
        infrastructure_type_code=TYPE_CODE,
    )
    return InfrastructureGreedyPlacementState(
        snapshot_id=SNAPSHOT_ID,
        infrastructure_type_code=TYPE_CODE,
        remaining_demand=(
            InfrastructurePlacementDemandState(
                demand_ref=demand_ref,
                initial_demand=8.0,
                remaining_demand=3.0,
            ),
        ),
        accepted_facilities=(
            InfrastructureAcceptedFacility(
                candidate_ref=candidate_ref,
                acceptance_index=0,
            ),
        ),
        coverage_cache=(
            InfrastructureCoverageCacheEntry(
                snapshot_id=SNAPSHOT_ID,
                infrastructure_type_code=TYPE_CODE,
                candidate_ref=candidate_ref,
                accessibility=(
                    InfrastructureAccessibilityResult(
                        snapshot_id=SNAPSHOT_ID,
                        infrastructure_type_code=TYPE_CODE,
                        demand_ref=demand_ref,
                        facility_site_ref=candidate_ref,
                        demand_node=NetworkNodeRef("demand-node"),
                        facility_site_node=NetworkNodeRef("candidate-node"),
                        max_network_distance_m=1_000.0,
                        distance_m=300.0,
                    ),
                ),
            ),
        ),
        candidate_order=(candidate_ref,),
    )


def _seed(*, status: str = "running") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="Infrastructure UI read model",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()

            run = GenerationRun(
                project_id=project.id,
                status=status,
                mode="EXPANSION",
                seed=44,
                working_srid=WORKING_SRID,
                config_json={},
                config_schema_version="test-v1",
                commit_sha="b" * 40 if status == "succeeded" else None,
            )
            session.add(run)
            session.flush()

            block = GeneratedBlock(
                run_id=run.id,
                block_key="block-a",
                area_m2=10_000.0,
                association_status="ASSOCIATED",
                attributes_json={"demography": {"population": 100}},
                geometry=from_shape(
                    box(0.0, 0.0, 100.0, 100.0),
                    srid=WORKING_SRID,
                ),
            )
            session.add(block)

            generated = GeneratedInfrastructure(
                id=uuid.uuid5(
                    run.id,
                    "generated-infrastructure:school.general:candidate-a",
                ),
                run_id=run.id,
                geometry=from_shape(
                    box(200.0, 0.0, 220.0, 10.0),
                    srid=WORKING_SRID,
                ),
                candidate_id="candidate-a",
                infrastructure_type_code=TYPE_CODE,
                category="education",
                capacity=5.0,
                acceptance_index=0,
                geometry_kind="site",
                site_area_m2=200.0,
                network_snapshot_id=SNAPSHOT_ID,
                network_node_id="candidate-node",
                network_snap_distance_m=2.0,
                attributes_json={"source_kind": "parcel"},
            )
            session.add(generated)
            session.flush()
            return run.id, block.id, generated.id


def _write(run_id: uuid.UUID):
    return SqlAlchemyInfrastructureUiReadModelWriter(
        session_factory=SessionLocal
    ).replace(
        run_id=run_id,
        unmet_demand=_unmet(),
        placements=(_placement(),),
        existing_accessibility=(_existing_accessibility(),),
        existing=_existing(),
        infrastructure_types=(_type(),),
    )


def test_writer_persists_unmet_layer_accessibility_and_raw_metrics() -> None:
    run_id, block_id, generated_id = _seed()

    result = _write(run_id)

    assert result.updated_block_rows == 1
    assert result.updated_generated_facility_rows == 1
    assert result.existing_facility_summary_count == 1
    assert result.generated_facility_summary_count == 1
    assert result.metrics.require(
        RawMetricId.INFRASTRUCTURE_UNMET_DEMAND
    ).scalar_value == pytest.approx(3.0)

    with Session(engine) as session:
        block = session.get(GeneratedBlock, block_id)
        generated = session.get(GeneratedInfrastructure, generated_id)
        run = session.get(GenerationRun, run_id)

        assert block is not None
        infrastructure = block.attributes_json["infrastructure"]
        assert infrastructure["gross_demand"] == pytest.approx(10.0)
        assert infrastructure["served_demand"] == pytest.approx(7.0)
        assert infrastructure["final_unmet_demand"] == pytest.approx(3.0)
        assert infrastructure["coverage_ratio"] == pytest.approx(0.7)
        assert infrastructure["demands"][0]["existing_served_demand"] == pytest.approx(
            2.0
        )
        assert infrastructure["demands"][0]["generated_served_demand"] == pytest.approx(
            5.0
        )

        assert generated is not None
        accessibility = generated.attributes_json["accessibility"]
        assert accessibility["reachable_demand_count"] == 1
        assert accessibility["nearest_distance_m"] == pytest.approx(300.0)
        assert accessibility["farthest_distance_m"] == pytest.approx(300.0)
        assert accessibility["max_network_distance_m"] == pytest.approx(1_000.0)

        assert run is not None
        payload = run.metrics_json["infrastructure"]
        raw_by_id = {
            item["metric_id"]: item for item in payload["raw_metrics"]
        }
        assert raw_by_id["infrastructure.unmet_demand"]["scalar_value"] == pytest.approx(
            3.0
        )
        existing_summary = payload["facility_accessibility"]["existing"][0]
        assert existing_summary["source_feature_id"] == "source-facility-a"
        assert existing_summary["reachable_demand_count"] == 1
        assert existing_summary["nearest_distance_m"] == pytest.approx(100.0)


def test_writer_is_retry_safe_and_preserves_other_namespaces() -> None:
    run_id, block_id, generated_id = _seed()

    first = _write(run_id)
    second = _write(run_id)

    assert first.metrics == second.metrics
    with Session(engine) as session:
        block = session.get(GeneratedBlock, block_id)
        generated = session.get(GeneratedInfrastructure, generated_id)
        run = session.get(GenerationRun, run_id)

        assert block is not None
        assert block.attributes_json["demography"]["population"] == 100
        assert block.attributes_json["infrastructure"]["final_unmet_demand"] == pytest.approx(
            3.0
        )
        assert generated is not None
        assert generated.attributes_json["source_kind"] == "parcel"
        assert generated.attributes_json["accessibility"]["reachable_demand_count"] == 1
        assert run is not None
        assert run.metrics_json["infrastructure"]["read_model_version"] == (
            "infrastructure-ui-v1"
        )


def test_writer_rejects_successful_run_before_mutation() -> None:
    run_id, block_id, generated_id = _seed(status="succeeded")

    with pytest.raises(
        InfrastructureUiReadModelImmutableError,
        match="successful generation run",
    ):
        _write(run_id)

    with Session(engine) as session:
        block = session.get(GeneratedBlock, block_id)
        generated = session.get(GeneratedInfrastructure, generated_id)
        assert block is not None
        assert "infrastructure" not in block.attributes_json
        assert generated is not None
        assert "accessibility" not in generated.attributes_json


def test_writer_requires_persisted_generated_rows_to_match_accepted_facilities() -> None:
    run_id, _block_id, generated_id = _seed()
    with Session(engine) as session:
        row = session.get(GeneratedInfrastructure, generated_id)
        assert row is not None
        session.delete(row)
        session.commit()

    with pytest.raises(
        Exception,
        match="persisted generated infrastructure must match accepted facilities",
    ):
        _write(run_id)
