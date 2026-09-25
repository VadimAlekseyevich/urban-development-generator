from __future__ import annotations

import uuid
from dataclasses import dataclass

import networkx as nx
import pytest
from alembic import command
from alembic.config import Config
from shapely.geometry import Point, box
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.db.generated_infrastructure_writer import (
    GeneratedInfrastructureWriteResult,
    SqlAlchemyGeneratedInfrastructureWriter,
)
from backend.app.db.session import SessionLocal, engine
from backend.app.models.generated_entity import GeneratedInfrastructure
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from core.urban_generator.demography import (
    BlockDemographicDemand,
    DemographicDemandCategory,
    DemographicDemandProfile,
    DemographicDemandSignal,
    DemographicDemandTotals,
    DemographicDemandUnit,
)
from core.urban_generator.domain import NetworkPoint, RawMetricId, WorkingCRS
from core.urban_generator.infrastructure import (
    ExistingInfrastructureDiagnostics,
    ExistingInfrastructureFacility,
    ExistingInfrastructureFacilityRef,
    ExistingInfrastructureFacilitySnapInput,
    ExistingInfrastructureResult,
    InfrastructureAccessibilityBatchResult,
    InfrastructureCandidateAccessibilityPolicy,
    InfrastructureCandidateGeometry,
    InfrastructureCandidateGeometryDiagnostics,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidateGeometryResult,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateRef,
    InfrastructureCandidateSnapInput,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureDemandRef,
    InfrastructureDemandSnapInput,
    InfrastructureFeasibilityResult,
    InfrastructureGreedyPlacementPolicy,
    InfrastructureGreedyPlacementState,
    InfrastructureMetricsBuilder,
    InfrastructureMetricsResult,
    InfrastructureNetworkSnapBatchResult,
    InfrastructureNetworkSnapPolicy,
    InfrastructurePlacementSelectionStatus,
    InfrastructureServedDemand,
    InfrastructureType,
    UnmetDemandCalculator,
    UnmetDemandResult,
    apply_infrastructure_greedy_selection,
    calculate_infrastructure_candidate_benefits,
    compute_candidate_site_accessibility_batch,
    compute_existing_facility_accessibility_batch,
    initialize_infrastructure_greedy_placement_state,
    select_infrastructure_greedy_candidate,
    snap_infrastructure_network_batch,
    validate_infrastructure_candidate_feasibility,
)
from core.urban_generator.metrics import (
    INFRASTRUCTURE_RAW_METRIC_IDS,
    InfrastructureMetricAdapter,
)
from core.urban_generator.roads import NetworkXBackend
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857
SNAPSHOT_ID = "roads:synthetic-town-v1"
TYPE_CODE = "school.general"


@dataclass(frozen=True, slots=True)
class SyntheticTownResult:
    run_id: uuid.UUID
    existing: ExistingInfrastructureResult
    gross_demand: UnmetDemandResult
    unmet_demand: UnmetDemandResult
    snaps: InfrastructureNetworkSnapBatchResult
    existing_accessibility: InfrastructureAccessibilityBatchResult
    candidate_accessibility: InfrastructureAccessibilityBatchResult
    placement: InfrastructureGreedyPlacementState
    persistence: GeneratedInfrastructureWriteResult
    metrics: InfrastructureMetricsResult


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


def _signals() -> tuple[DemographicDemandSignal, ...]:
    return (
        DemographicDemandSignal(
            category=DemographicDemandCategory.POPULATION,
            value=20.0,
            unit=DemographicDemandUnit.PEOPLE,
        ),
        DemographicDemandSignal(
            category=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            min_age=0,
            max_age=17,
            value=10.0,
            unit=DemographicDemandUnit.PEOPLE,
        ),
        DemographicDemandSignal(
            category=DemographicDemandCategory.AGE_GROUP,
            demographic_group="adult",
            min_age=18,
            max_age=None,
            value=10.0,
            unit=DemographicDemandUnit.PEOPLE,
        ),
        DemographicDemandSignal(
            category=DemographicDemandCategory.WORKFORCE,
            value=12.0,
            unit=DemographicDemandUnit.PEOPLE,
        ),
        DemographicDemandSignal(
            category=DemographicDemandCategory.JOBS,
            value=5.0,
            unit=DemographicDemandUnit.JOBS,
        ),
    )


def _profile() -> DemographicDemandProfile:
    blocks = tuple(
        BlockDemographicDemand(
            block_id=block_id,
            zone_id="zone-public",
            zone_class=ZoneClass.PUBLIC,
            signals=_signals(),
        )
        for block_id in ("block-a", "block-b")
    )
    totals = DemographicDemandTotals(
        block_count=2,
        signals=tuple(
            DemographicDemandSignal(
                category=signal.category,
                demographic_group=signal.demographic_group,
                min_age=signal.min_age,
                max_age=signal.max_age,
                value=signal.value * 2.0,
                unit=signal.unit,
            )
            for signal in _signals()
        ),
    )
    return DemographicDemandProfile(
        scenario_version="synthetic-town-v1",
        scenario_fingerprint="a" * 64,
        blocks=blocks,
        totals=totals,
    )


def _type() -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=TYPE_CODE,
        category=InfrastructureCategory.EDUCATION,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.AGE_GROUP,
            demographic_group="child",
            demand_rate=1.0,
        ),
        capacity=5.0,
        max_network_distance_m=250.0,
        allowed_zones=(ZoneClass.PUBLIC,),
        minimum_site_area_m2=100.0,
        target_site_area_m2=200.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,),
        ),
    )


def _existing() -> ExistingInfrastructureResult:
    facility = ExistingInfrastructureFacility(
        facility_id="dataset:facilities:v1:school-fixed",
        source_ref="dataset:facilities:v1",
        source_feature_id="school-fixed",
        infrastructure_type_code=TYPE_CODE,
        capacity=5.0,
        geometry=Point(0.0, 0.0),
        working_srid=WORKING_SRID,
        name="Fixed school",
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


def _candidate(
    candidate_id: str,
    *,
    x_m: float,
    block_id: str,
) -> InfrastructureCandidateGeometry:
    site = box(x_m - 10.0, -10.0, x_m + 10.0, 10.0)
    return InfrastructureCandidateGeometry(
        candidate_id=candidate_id,
        infrastructure_type_code=TYPE_CODE,
        source_kind=InfrastructureCandidateSource.PARCEL,
        source_id=f"parcel-{candidate_id}",
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(x_m, 0.0),
        kind=InfrastructureCandidateGeometryKind.SITE,
        site_geometry=site,
        site_area_m2=float(site.area),
        zone_id="zone-public",
        block_id=block_id,
    )


def _candidate_geometry() -> InfrastructureCandidateGeometryResult:
    candidates = (
        _candidate("candidate-a", x_m=200.0, block_id="block-a"),
        _candidate("candidate-b", x_m=400.0, block_id="block-b"),
    )
    return InfrastructureCandidateGeometryResult(
        infrastructure_type_code=TYPE_CODE,
        working_srid=WORKING_SRID,
        candidates=candidates,
        diagnostics=InfrastructureCandidateGeometryDiagnostics(
            candidate_count=2,
            polygon_site_count=2,
            host_building_count=0,
            clipped_site_count=0,
            full_source_site_count=2,
        ),
    )


def _network() -> NetworkXBackend:
    graph = nx.Graph()
    for index in range(5):
        graph.add_node(
            f"n{index}",
            x_m=float(index * 100),
            y_m=0.0,
        )
    for index in range(4):
        graph.add_edge(
            f"n{index}",
            f"n{index + 1}",
            length_m=100.0,
        )
    return NetworkXBackend(
        graph,
        snapshot_id=SNAPSHOT_ID,
        working_crs=WorkingCRS(srid=WORKING_SRID),
    )


def _snap_inputs(
    gross_demand: UnmetDemandResult,
    existing: ExistingInfrastructureResult,
    candidate_geometry: InfrastructureCandidateGeometryResult,
) -> tuple[
    InfrastructureDemandSnapInput
    | ExistingInfrastructureFacilitySnapInput
    | InfrastructureCandidateSnapInput,
    ...,
]:
    demand_points = {
        "block-a": NetworkPoint(x_m=100.0, y_m=0.0),
        "block-b": NetworkPoint(x_m=400.0, y_m=0.0),
    }
    inputs: list[
        InfrastructureDemandSnapInput
        | ExistingInfrastructureFacilitySnapInput
        | InfrastructureCandidateSnapInput
    ] = [
        InfrastructureDemandSnapInput(
            ref=InfrastructureDemandRef.from_demand(demand),
            point=demand_points[demand.block_id],
            working_srid=WORKING_SRID,
        )
        for demand in gross_demand.demands
    ]
    for facility in existing.facilities:
        assert isinstance(facility.geometry, Point)
        inputs.append(
            ExistingInfrastructureFacilitySnapInput(
                ref=ExistingInfrastructureFacilityRef.from_facility(facility),
                point=NetworkPoint(
                    x_m=float(facility.geometry.x),
                    y_m=float(facility.geometry.y),
                ),
                working_srid=WORKING_SRID,
            )
        )
    inputs.extend(
        InfrastructureCandidateSnapInput(
            ref=InfrastructureCandidateRef.from_candidate(candidate),
            point=NetworkPoint(
                x_m=float(candidate.anchor.x),
                y_m=float(candidate.anchor.y),
            ),
            working_srid=WORKING_SRID,
        )
        for candidate in candidate_geometry.candidates
    )
    return tuple(inputs)


def _served_by_existing(
    gross_demand: UnmetDemandResult,
    existing: ExistingInfrastructureResult,
    accessibility: InfrastructureAccessibilityBatchResult,
) -> tuple[InfrastructureServedDemand, ...]:
    demand_by_key = {item.key: item for item in gross_demand.demands}
    capacity_by_facility = {
        item.facility_id: item.capacity for item in existing.facilities
    }
    served: list[InfrastructureServedDemand] = []

    for outcome in accessibility.reachable:
        ref = outcome.facility_site_ref
        assert isinstance(ref, ExistingInfrastructureFacilityRef)
        demand = demand_by_key[outcome.demand_ref.key]
        remaining_capacity = capacity_by_facility[ref.facility_id]
        amount = min(demand.gross_demand, remaining_capacity)
        capacity_by_facility[ref.facility_id] = max(
            remaining_capacity - amount,
            0.0,
        )
        if amount <= 0.0:
            continue
        served.append(
            InfrastructureServedDemand(
                block_id=demand.block_id,
                infrastructure_type_code=demand.infrastructure_type_code,
                value=amount,
            )
        )
    return tuple(served)


def _placement(
    unmet_demand: UnmetDemandResult,
    candidate_geometry: InfrastructureCandidateGeometryResult,
    candidate_accessibility: InfrastructureAccessibilityBatchResult,
    infrastructure_type: InfrastructureType,
) -> InfrastructureGreedyPlacementState:
    refs = tuple(
        InfrastructureCandidateRef.from_candidate(candidate)
        for candidate in candidate_geometry.candidates
    )
    state = initialize_infrastructure_greedy_placement_state(
        unmet_demand.demands,
        refs,
        candidate_accessibility=candidate_accessibility,
    )
    geometry_by_key = {
        InfrastructureCandidateRef.from_candidate(item).key: item
        for item in candidate_geometry.candidates
    }
    policy = InfrastructureGreedyPlacementPolicy(
        max_facilities=4,
        max_iterations=4,
    )

    for iteration_index in range(policy.max_iterations):
        benefits = calculate_infrastructure_candidate_benefits(
            state,
            infrastructure_type=infrastructure_type,
        )
        feasibility: tuple[InfrastructureFeasibilityResult, ...] = tuple(
            validate_infrastructure_candidate_feasibility(
                geometry_by_key[item.candidate_ref.key],
                infrastructure_type=infrastructure_type,
                proposed_capacity=item.capacity,
            )
            for item in benefits
        )
        selection = select_infrastructure_greedy_candidate(
            state,
            benefits,
            iteration_index=iteration_index,
            feasibility_results=feasibility,
            policy=policy,
        )
        if selection.status is not InfrastructurePlacementSelectionStatus.SELECTED:
            return state
        state = apply_infrastructure_greedy_selection(
            state,
            selection,
            infrastructure_type=infrastructure_type,
        )
    return state


def _create_run() -> uuid.UUID:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = Project(
                name="S10 synthetic town",
                working_srid=WORKING_SRID,
                boundary_metadata={},
            )
            session.add(project)
            session.flush()
            run = GenerationRun(
                project_id=project.id,
                status="running",
                mode="EXPANSION",
                seed=42,
                working_srid=WORKING_SRID,
                config_json={},
                config_schema_version="synthetic-town-v1",
            )
            session.add(run)
            session.flush()
            return run.id


def _run_synthetic_town(
    *,
    existing: ExistingInfrastructureResult | None = None,
) -> SyntheticTownResult:
    infrastructure_type = _type()
    existing = existing or _existing()
    candidate_geometry = _candidate_geometry()
    backend = _network()
    demand_calculator = UnmetDemandCalculator()

    gross_demand = demand_calculator.calculate(
        _profile(),
        infrastructure_types=(infrastructure_type,),
        existing=existing,
    )
    snaps = snap_infrastructure_network_batch(
        backend,
        _snap_inputs(gross_demand, existing, candidate_geometry),
        policy=InfrastructureNetworkSnapPolicy(
            max_snap_distance_m=1.0,
            max_batch_size=10,
        ),
    )
    existing_accessibility = compute_existing_facility_accessibility_batch(
        backend,
        snaps,
        infrastructure_type=infrastructure_type,
    )
    served = _served_by_existing(
        gross_demand,
        existing,
        existing_accessibility,
    )
    unmet_demand = demand_calculator.calculate(
        _profile(),
        infrastructure_types=(infrastructure_type,),
        existing=existing,
        served=served,
    )
    candidate_accessibility = compute_candidate_site_accessibility_batch(
        backend,
        snaps,
        infrastructure_type=infrastructure_type,
        policy=InfrastructureCandidateAccessibilityPolicy(
            max_candidates=10,
            max_demands=10,
            demand_batch_size=10,
            max_routing_calls=10,
            max_results=10,
        ),
    )
    placement = _placement(
        unmet_demand,
        candidate_geometry,
        candidate_accessibility,
        infrastructure_type,
    )

    run_id = _create_run()
    persistence = SqlAlchemyGeneratedInfrastructureWriter(
        session_factory=SessionLocal,
    ).replace(
        run_id=run_id,
        state=placement,
        candidate_geometry=candidate_geometry,
        candidate_snaps=snaps,
        infrastructure_type=infrastructure_type,
    )
    metrics = InfrastructureMetricsBuilder().build(
        unmet_demand,
        infrastructure_types=(infrastructure_type,),
        placements=(placement,),
        existing_accessibility=(existing_accessibility,),
    )
    return SyntheticTownResult(
        run_id=run_id,
        existing=existing,
        gross_demand=gross_demand,
        unmet_demand=unmet_demand,
        snaps=snaps,
        existing_accessibility=existing_accessibility,
        candidate_accessibility=candidate_accessibility,
        placement=placement,
        persistence=persistence,
        metrics=metrics,
    )


def test_synthetic_town_runs_through_s10_demand_to_persistence_and_metrics() -> None:
    result = _run_synthetic_town()

    assert len(result.gross_demand.demands) == 2
    assert result.snaps.diagnostics.input_count == 5
    assert result.snaps.diagnostics.unsnapped_count == 0
    assert result.existing_accessibility.diagnostics.subject_count == 2
    assert result.candidate_accessibility.diagnostics.subject_count == 4
    assert len(result.placement.accepted_facilities) == 2
    assert result.persistence.inserted_rows == 2
    assert len(result.metrics.raw_metrics) == 6

    with Session(engine) as session:
        rows = session.scalars(
            select(GeneratedInfrastructure)
            .where(GeneratedInfrastructure.run_id == result.run_id)
            .order_by(GeneratedInfrastructure.acceptance_index)
        ).all()

    assert [row.candidate_id for row in rows] == [
        "candidate-a",
        "candidate-b",
    ]
    assert all(row.network_snapshot_id == SNAPSHOT_ID for row in rows)


def _scalar_metric(
    result: SyntheticTownResult,
    metric_id: RawMetricId,
) -> float:
    value = result.metrics.require(metric_id).scalar_value
    assert value is not None
    return value


def _fixed_signature(
    existing: ExistingInfrastructureResult,
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            facility.facility_id,
            facility.source_ref,
            facility.source_feature_id,
            facility.infrastructure_type_code,
            facility.capacity,
            facility.working_srid,
            facility.name,
            facility.geometry.wkb_hex,
        )
        for facility in existing.facilities
    )


def _semantic_signature(result: SyntheticTownResult) -> tuple[object, ...]:
    return (
        result.gross_demand,
        result.unmet_demand,
        result.snaps,
        result.existing_accessibility,
        result.candidate_accessibility,
        result.placement,
        result.metrics,
        result.persistence.deleted_rows,
        result.persistence.inserted_rows,
        result.persistence.insert_statements,
        result.persistence.host_building_ref_count,
    )


def test_synthetic_town_expected_coverage_and_fixed_contribution() -> None:
    result = _run_synthetic_town()
    adapted = InfrastructureMetricAdapter().adapt(metrics=result.metrics)

    assert adapted is result.metrics
    assert tuple(item.metric_id for item in adapted.raw_metrics) == (
        INFRASTRUCTURE_RAW_METRIC_IDS
    )

    summary = result.unmet_demand.summaries[0]
    assert summary.gross_demand == pytest.approx(20.0)
    assert summary.served_demand == pytest.approx(5.0)
    assert summary.unmet_demand == pytest.approx(15.0)
    assert summary.existing_capacity == pytest.approx(5.0)

    final_remaining = sum(
        item.remaining_demand for item in result.placement.remaining_demand
    )
    assert final_remaining == pytest.approx(5.0)
    assert 0.70 <= _scalar_metric(
        result,
        RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
    ) <= 0.80
    assert _scalar_metric(
        result,
        RawMetricId.INFRASTRUCTURE_UNMET_DEMAND,
    ) == pytest.approx(5.0)
    assert _scalar_metric(
        result,
        RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
    ) == pytest.approx(1.0)
    assert _scalar_metric(
        result,
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M,
    ) == pytest.approx(100.0)
    p50 = _scalar_metric(
        result,
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M,
    )
    p90 = _scalar_metric(
        result,
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M,
    )
    assert p50 == pytest.approx(100.0)
    assert p90 == pytest.approx(100.0)
    assert 0.0 <= p50 <= p90 <= 250.0

    age_metric = result.metrics.require(
        RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE
    )
    assert len(age_metric.age_coverage) == 1
    assert age_metric.age_coverage[0].demographic_group == "child"
    assert age_metric.age_coverage[0].coverage_ratio == pytest.approx(0.75)


def test_synthetic_town_is_deterministic_and_does_not_mutate_fixed_state() -> None:
    fixed = _existing()
    fixed_before = _fixed_signature(fixed)

    first = _run_synthetic_town(existing=fixed)
    second = _run_synthetic_town(existing=fixed)

    assert first.existing is fixed
    assert second.existing is fixed
    assert _fixed_signature(fixed) == fixed_before
    assert _semantic_signature(first) == _semantic_signature(second)

    with Session(engine) as session:
        persisted = session.scalars(
            select(GeneratedInfrastructure)
            .where(GeneratedInfrastructure.run_id.in_((first.run_id, second.run_id)))
            .order_by(
                GeneratedInfrastructure.run_id,
                GeneratedInfrastructure.acceptance_index,
            )
        ).all()

    by_run: dict[uuid.UUID, list[tuple[object, ...]]] = {}
    for row in persisted:
        by_run.setdefault(row.run_id, []).append(
            (
                row.candidate_id,
                row.infrastructure_type_code,
                row.category,
                row.capacity,
                row.acceptance_index,
                row.geometry_kind,
                row.site_area_m2,
                row.network_snapshot_id,
                row.network_node_id,
                row.network_snap_distance_m,
            )
        )
    assert by_run[first.run_id] == by_run[second.run_id]
