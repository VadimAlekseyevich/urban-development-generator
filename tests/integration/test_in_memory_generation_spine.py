import uuid

import numpy as np
import pytest
from shapely.geometry import LineString, MultiLineString, box

from core.urban_generator.blocks import (
    BlockDevelopableArea,
    BlockFrontagePolicy,
    OversizedBlockSplitPolicy,
    ParcelSubdivisionPolicy,
    SliverCleanupPolicy,
)
from core.urban_generator.buildings import (
    BuildingArchetype,
    BuildingArchetypeConfig,
    BuildingAttributeConfig,
    BuildingAttributeRule,
    BuildingConfig,
    BuildingFootprintStrategy,
    BuildingPlacementCandidatePolicy,
    BuildingPlacementScope,
    BuildingSpacingPolicy,
    BuildingUse,
    RectangularPointFootprintSpec,
)
from core.urban_generator.constraints import (
    AggregateConstraintSubject,
    AggregateMetricBound,
    AggregateMetricBoundConstraint,
    AggregateMetricValue,
    ConstraintRegistry,
    RegisteredConstraintEngine,
    SoftAggregatePreference,
    SoftAggregatePreferenceConstraint,
)
from core.urban_generator.demography import (
    AgeGroupShare,
    DemographicScenario,
    EmploymentConfig,
    JobDensityRule,
    PopulationTarget,
    PopulationTargetKind,
)
from core.urban_generator.domain import (
    ConfigRef,
    CorrelationMetadata,
    MetricDirection,
    ProjectRef,
    ProjectSettings,
    RawMetricId,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
    ValidationReport,
)
from core.urban_generator.metrics import (
    CompositeScoreConfig,
    CompositeScoreMetricWeight,
    CompositeScoreRawMetric,
    ConstraintMetricAdapter,
    DemographyMetricAdapter,
    LandBuildingMetricAdapter,
    MetricNormalizationPolicy,
    MetricNormalizationProfile,
    NormalizationClampPolicy,
    NormalizationMissingPolicy,
    RoadMetricAdapter,
    build_composite_score,
)
from core.urban_generator.roads import (
    CandidateRoadAnchorPolicy,
    LeastCostConnectorPolicy,
    RoadValidationPolicy,
    SemanticRoad,
)
from core.urban_generator.roads.endpoint_snapping import EndpointRoadSnappingPolicy
from core.urban_generator.roads.fixed_network_attachment import (
    FixedNetworkAttachmentPolicy,
)
from core.urban_generator.stages import (
    BlocksAndParcelsStage,
    BlocksAndParcelsStageConfig,
    BlocksAndParcelsStageInput,
    BuildingArchetypeStageSpec,
    BuildingStage,
    BuildingStageConfig,
    BuildingStageInput,
    BuildingZoneTarget,
    ConstraintMaskStage,
    ConstraintMaskStageConfig,
    ConstraintMaskStageInput,
    DemographyStage,
    DemographyStageConfig,
    DemographyStageInput,
    FixedRoadStageInput,
    RoadStage,
    RoadStageConfig,
    RoadStageInput,
    SuitabilityStage,
    SuitabilityStageConfig,
    SuitabilityStageInput,
    ZoningStage,
    ZoningStageConfig,
    ZoningStageInput,
)
from core.urban_generator.suitability import (
    HardExclusionBoundary,
    SuitabilityConfig,
    SuitabilityFactorConfig,
    SuitabilityFactorResult,
    SuitabilityGridSpec,
    SuitabilityNormalization,
    SuitabilityThresholds,
)
from core.urban_generator.zoning import ZoneClass, ZoneClassConfig, ZoningConfig

WORKING_SRID = 32637
RUN_ID = uuid.UUID("00000000-0000-0000-0000-00000000a005")


class ConstantFactor:
    code = "constant"
    version = "1"

    def evaluate(
        self,
        *,
        grid: SuitabilityGridSpec,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> SuitabilityFactorResult:
        return SuitabilityFactorResult(
            code=self.code,
            version=self.version,
            grid=grid,
            values=np.ones(grid.shape, dtype=np.float64),
            valid_mask=np.ones(grid.shape, dtype=np.bool_),
        )


def _snapshot() -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-00000000b005"),
        project=ProjectRef(
            project_id=uuid.UUID("00000000-0000-0000-0000-00000000c005")
        ),
        settings=ProjectSettings(working_srid=WORKING_SRID),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:m0-spine:boundary:v1",
        ),
        roads=(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.ROADS,
                source_ref="synthetic:m0-spine:fixed-roads:v1",
            ),
        ),
    )


def _context() -> RunContext:
    return RunContext(
        run_id=RUN_ID,
        mode=RunMode.EXPANSION,
        seed=20260919,
        working_srid=WORKING_SRID,
        config_refs=(ConfigRef(name="m0-spine", ref="synthetic:v1"),),
        correlation=CorrelationMetadata(correlation_id="m0-in-memory-spine"),
    )


def _zoning_config() -> ZoningConfig:
    return ZoningConfig(
        version="m0-v1",
        zones=tuple(
            ZoneClassConfig(
                zone_class=zone_class,
                target_share=1.0 if zone_class is ZoneClass.RESIDENTIAL else 0.0,
                minimum_area_m2=0.1,
            )
            for zone_class in ZoneClass
        ),
    )


def _road_config() -> RoadStageConfig:
    return RoadStageConfig(
        endpoint_snapping=EndpointRoadSnappingPolicy(tolerance_m=0.05),
        anchor_policy=CandidateRoadAnchorPolicy(
            max_candidates=1,
            max_sampled_cells=100,
            allowed_zone_classes=(ZoneClass.RESIDENTIAL,),
        ),
        least_cost_policy=LeastCostConnectorPolicy(max_visited_cells=10_000),
        validation_policy=RoadValidationPolicy(
            max_component_count=1,
            max_dead_end_ratio=1.0,
        ),
        fixed_attachment_policy=FixedNetworkAttachmentPolicy(
            max_distance_m=20.0
        ),
    )


def _blocks_config() -> BlocksAndParcelsStageConfig:
    return BlocksAndParcelsStageConfig(
        frontage_policy=BlockFrontagePolicy(
            access_tolerance_m=0.0,
            minimum_frontage_m=1.0,
        ),
        split_policy=OversizedBlockSplitPolicy(max_area_m2=200.0),
        sliver_policy=SliverCleanupPolicy(min_area_m2=1.0),
        parcel_policy=ParcelSubdivisionPolicy(
            target_frontage_m=20.0,
            minimum_frontage_m=1.0,
            minimum_parcel_area_m2=10.0,
        ),
    )


def _building_config() -> BuildingStageConfig:
    return BuildingStageConfig(
        building=BuildingConfig(
            version="m0-buildings-v1",
            archetypes=(
                BuildingArchetypeConfig(
                    archetype=BuildingArchetype.POINT,
                    footprint_strategy=(
                        BuildingFootprintStrategy.RECTANGULAR_POINT
                    ),
                    placement_scope=BuildingPlacementScope.PARCEL,
                    allowed_zones=(ZoneClass.RESIDENTIAL,),
                ),
            ),
        ),
        attributes=BuildingAttributeConfig(
            version="m0-attributes-v1",
            rules=(
                BuildingAttributeRule(
                    zone_class=ZoneClass.RESIDENTIAL,
                    archetype=BuildingArchetype.POINT,
                    use=BuildingUse.RESIDENTIAL,
                    min_floors=2,
                    max_floors=2,
                ),
            ),
        ),
        archetype_specs=(
            BuildingArchetypeStageSpec(
                archetype=BuildingArchetype.POINT,
                footprint_spec=RectangularPointFootprintSpec.point(size_m=2.0),
                planning_floor_area_multiplier=2.0,
            ),
        ),
        zone_targets=(
            BuildingZoneTarget(
                zone_class=ZoneClass.RESIDENTIAL,
                target_coverage_ratio=0.16,
                target_far=0.32,
                coverage_tolerance=0.0,
                far_tolerance=0.0,
            ),
        ),
        candidate_policy=BuildingPlacementCandidatePolicy(
            grid_spacing_m=5.0,
            frontage_spacing_m=5.0,
            include_frontage=False,
        ),
        spacing_policy=BuildingSpacingPolicy(minimum_gap_m=0.0),
    )


def _demography_config() -> DemographyStageConfig:
    return DemographyStageConfig(
        scenario=DemographicScenario(
            version="m0-demography-v1",
            population_target=PopulationTarget(
                kind=PopulationTargetKind.TOTAL_POPULATION,
                value=8,
            ),
            occupancy_ratio=1.0,
            residential_area_per_person_m2=1.0,
            average_household_size=2.0,
            residential_gfa_share=1.0,
            age_groups=(
                AgeGroupShare(
                    code="child",
                    min_age=0,
                    max_age=17,
                    share=0.25,
                ),
                AgeGroupShare(
                    code="adult",
                    min_age=18,
                    max_age=None,
                    share=0.75,
                ),
            ),
            working_population_ratio=0.5,
        ),
        employment=EmploymentConfig(
            version="m0-jobs-v1",
            rules=(
                JobDensityRule(
                    use=BuildingUse.MIXED,
                    area_per_job_m2=50.0,
                ),
                JobDensityRule(
                    use=BuildingUse.PUBLIC,
                    area_per_job_m2=40.0,
                ),
                JobDensityRule(
                    use=BuildingUse.COMMERCIAL,
                    area_per_job_m2=30.0,
                ),
            ),
        ),
    )


def _execute_spine():
    snapshot = _snapshot()
    context = _context()
    boundary = box(0.0, 0.0, 10.0, 10.0)
    grid = SuitabilityGridSpec(
        working_srid=WORKING_SRID,
        bounds=(0.0, 0.0, 10.0, 10.0),
        width=10,
        height=10,
    )

    constraints = ConstraintMaskStage().execute(
        snapshot=snapshot,
        context=context,
        stage_input=ConstraintMaskStageInput(
            grid=grid,
            boundary=HardExclusionBoundary(
                geometry=boundary,
                working_srid=WORKING_SRID,
            ),
        ),
        config=ConstraintMaskStageConfig(),
    )
    suitability = SuitabilityStage(factors=(ConstantFactor(),)).execute(
        snapshot=snapshot,
        context=context,
        stage_input=SuitabilityStageInput(
            grid=grid,
            hard_mask=constraints.output,
        ),
        config=SuitabilityStageConfig(
            suitability=SuitabilityConfig(
                version="m0-v1",
                factors=(
                    SuitabilityFactorConfig(
                        code="constant",
                        weight=1.0,
                        normalization=SuitabilityNormalization.IDENTITY,
                    ),
                ),
                thresholds=SuitabilityThresholds(minimum_score=0.0),
            )
        ),
    )
    zoning = ZoningStage(
        constraint_engine=RegisteredConstraintEngine(ConstraintRegistry())
    ).execute(
        snapshot=snapshot,
        context=context,
        stage_input=ZoningStageInput(
            suitability=suitability.output,
            developable_area=boundary,
        ),
        config=ZoningStageConfig(
            zoning=_zoning_config(),
            seed_count=1,
            max_refinement_iterations=20,
        ),
    )

    fixed_square = SemanticRoad(
        road_id="fixed:square",
        geometry=MultiLineString(
            (
                LineString(((0.0, 0.0), (10.0, 0.0))),
                LineString(((10.0, 0.0), (10.0, 10.0))),
                LineString(((10.0, 10.0), (0.0, 10.0))),
                LineString(((0.0, 10.0), (0.0, 0.0))),
            )
        ),
    )
    roads = RoadStage().execute(
        snapshot=snapshot,
        context=context,
        stage_input=RoadStageInput(
            zoning=zoning.output,
            suitability=suitability.output,
            hard_mask=constraints.output,
            fixed_roads=(
                FixedRoadStageInput(
                    road=fixed_square,
                    existing_class="local",
                ),
            ),
        ),
        config=_road_config(),
    )
    blocks = BlocksAndParcelsStage().execute(
        snapshot=snapshot,
        context=context,
        stage_input=BlocksAndParcelsStageInput(
            road_graph=roads.output.graph,
            developable_area=BlockDevelopableArea(
                project_boundary=boundary,
                developable_mask=boundary,
                working_srid=WORKING_SRID,
            ),
            generated_zone_refs=zoning.output.generated_zone_refs,
        ),
        config=_blocks_config(),
    )
    buildings = BuildingStage().execute(
        snapshot=snapshot,
        context=context,
        stage_input=BuildingStageInput(blocks=blocks.output),
        config=_building_config(),
    )
    demography = DemographyStage().execute(
        snapshot=snapshot,
        context=context,
        stage_input=DemographyStageInput(
            buildings=buildings.output,
            baseline_population=0,
        ),
        config=_demography_config(),
    )
    return (
        constraints,
        suitability,
        zoning,
        roads,
        blocks,
        buildings,
        demography,
    )


def test_expansion_spine_executes_through_demography_without_infrastructure() -> None:
    results = _execute_spine()
    constraints, suitability, zoning, roads, blocks, buildings, demography = results

    assert constraints.output.excluded_count == 0
    assert suitability.output.valid_count == 100
    assert len(zoning.output.generated_zone_refs) == 1
    assert zoning.output.generated_zone_refs[0].zone_class is ZoneClass.RESIDENTIAL

    assert roads.output.fixed_attachment is not None
    assert roads.output.fixed_attachment.complete is True
    assert any(edge.is_fixed for edge in roads.output.graph.edges)
    assert any(not edge.is_fixed for edge in roads.output.graph.edges)

    assert blocks.output.association.diagnostics.block_count >= 1
    assert blocks.output.association.diagnostics.associated_block_count >= 1
    assert blocks.output.subdivision.diagnostics.parcel_count >= 1

    assert buildings.output.area_subjects
    assert buildings.output.fixed_building_refs == ()
    assert demography.output.population.diagnostics.allocated_generated_population == 8
    assert demography.output.population.diagnostics.unmet_generated_population == 0
    assert demography.output.fixed_demography_refs == ()


def test_expansion_spine_is_deterministic_end_to_end() -> None:
    first = _execute_spine()
    second = _execute_spine()

    assert tuple(item.fingerprint for item in first) == tuple(
        item.fingerprint for item in second
    )
    assert first[-1].output.demand_profile == second[-1].output.demand_profile


def _required_scalar(result, metric_id: RawMetricId) -> float:
    value = result.require(metric_id).scalar_value
    assert value is not None
    return value


def _reference_validation_report(
    *,
    coverage: float,
    far: float,
    density: float,
) -> ValidationReport:
    subject = AggregateConstraintSubject(
        values=(
            AggregateMetricValue(
                metric_id=RawMetricId.BUILDINGS_COVERAGE_RATIO,
                value=coverage,
            ),
            AggregateMetricValue(
                metric_id=RawMetricId.BUILDINGS_FAR,
                value=far,
            ),
            AggregateMetricValue(
                metric_id=RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
                value=density,
            ),
            AggregateMetricValue(
                metric_id=RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
                value=1.0,
            ),
        )
    )
    hard_bounds = (
        AggregateMetricBound(
            metric_id=RawMetricId.BUILDINGS_COVERAGE_RATIO,
            minimum=0.10,
            maximum=0.22,
        ),
        AggregateMetricBound(
            metric_id=RawMetricId.BUILDINGS_FAR,
            minimum=0.20,
            maximum=0.45,
        ),
        AggregateMetricBound(
            metric_id=RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
            minimum=50_000.0,
            maximum=120_000.0,
        ),
        AggregateMetricBound(
            metric_id=RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
            minimum=0.80,
            maximum=1.0,
        ),
    )
    hard_results = tuple(
        AggregateMetricBoundConstraint(bound).evaluate(
            subject=subject,
            snapshot=_snapshot(),
            context=_context(),
        )
        for bound in hard_bounds
    )
    soft_result = SoftAggregatePreferenceConstraint(
        SoftAggregatePreference(
            metric_id=RawMetricId.BUILDINGS_COVERAGE_RATIO,
            minimum=0.30,
            weight=0.5,
        )
    ).evaluate(
        subject=subject,
        snapshot=_snapshot(),
        context=_context(),
    )
    return ValidationReport(results=hard_results + (soft_result,))


def _reference_score(
    *,
    coverage: float,
    far: float,
    density: float,
    hard_violation_count: float,
    weighted_soft_penalty: float,
):
    clamp = NormalizationClampPolicy.REJECT
    missing = NormalizationMissingPolicy.REJECT
    profile = MetricNormalizationProfile(
        profile_id="s11.reference",
        version="1",
        policies=(
            MetricNormalizationPolicy(
                metric_id=RawMetricId.BUILDINGS_COVERAGE_RATIO,
                direction=MetricDirection.TARGET,
                lower_bound=0.0,
                upper_bound=0.5,
                target_lower_bound=0.10,
                target_upper_bound=0.22,
                clamp_policy=clamp,
                missing_policy=missing,
                version="1",
            ),
            MetricNormalizationPolicy(
                metric_id=RawMetricId.BUILDINGS_FAR,
                direction=MetricDirection.TARGET,
                lower_bound=0.0,
                upper_bound=1.0,
                target_lower_bound=0.20,
                target_upper_bound=0.45,
                clamp_policy=clamp,
                missing_policy=missing,
                version="1",
            ),
            MetricNormalizationPolicy(
                metric_id=RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
                direction=MetricDirection.TARGET,
                lower_bound=0.0,
                upper_bound=200_000.0,
                target_lower_bound=50_000.0,
                target_upper_bound=120_000.0,
                clamp_policy=clamp,
                missing_policy=missing,
                version="1",
            ),
            MetricNormalizationPolicy(
                metric_id=RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT,
                direction=MetricDirection.LOWER_IS_BETTER,
                lower_bound=0.0,
                upper_bound=5.0,
                clamp_policy=clamp,
                missing_policy=missing,
                version="1",
            ),
            MetricNormalizationPolicy(
                metric_id=RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY,
                direction=MetricDirection.LOWER_IS_BETTER,
                lower_bound=0.0,
                upper_bound=2.0,
                clamp_policy=clamp,
                missing_policy=missing,
                version="1",
            ),
        ),
    )
    raw_metrics = (
        CompositeScoreRawMetric(
            metric_id=RawMetricId.BUILDINGS_COVERAGE_RATIO,
            raw_value=coverage,
        ),
        CompositeScoreRawMetric(
            metric_id=RawMetricId.BUILDINGS_FAR,
            raw_value=far,
        ),
        CompositeScoreRawMetric(
            metric_id=RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
            raw_value=density,
        ),
        CompositeScoreRawMetric(
            metric_id=RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT,
            raw_value=hard_violation_count,
        ),
        CompositeScoreRawMetric(
            metric_id=RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY,
            raw_value=weighted_soft_penalty,
        ),
    )
    config = CompositeScoreConfig(
        config_id="s11.reference",
        version="1",
        weights=tuple(
            CompositeScoreMetricWeight(metric_id=item.metric_id, weight=1.0)
            for item in raw_metrics
        ),
    )
    return build_composite_score(
        raw_metrics,
        normalization_profile=profile,
        config=config,
    )


def test_s11_reference_fixture_preserves_metrics_validation_and_score_envelope() -> None:
    (
        _constraints,
        suitability,
        _zoning,
        roads,
        blocks,
        buildings,
        demography,
    ) = _execute_spine()

    land = LandBuildingMetricAdapter().adapt(
        suitability=suitability.output,
        blocks=blocks.output,
        buildings=buildings.output,
    )
    road = RoadMetricAdapter().adapt(roads=roads.output)
    population = DemographyMetricAdapter().adapt(demography=demography.output)

    developable_area = _required_scalar(
        land,
        RawMetricId.LAND_DEVELOPABLE_AREA_M2,
    )
    developed_area = _required_scalar(
        land,
        RawMetricId.LAND_DEVELOPED_AREA_M2,
    )
    coverage = _required_scalar(
        land,
        RawMetricId.BUILDINGS_COVERAGE_RATIO,
    )
    far = _required_scalar(land, RawMetricId.BUILDINGS_FAR)
    gfa = _required_scalar(land, RawMetricId.BUILDINGS_GFA_M2)
    density = _required_scalar(
        population,
        RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
    )

    assert developable_area == pytest.approx(100.0)
    assert 90.0 <= developed_area <= 100.0
    assert _required_scalar(
        land,
        RawMetricId.LAND_GREEN_RECREATION_SHARE,
    ) == pytest.approx(0.0)
    assert 0.10 <= coverage <= 0.22
    assert 0.20 <= far <= 0.45
    assert 20.0 <= gfa <= 45.0

    archetypes = land.require(
        RawMetricId.BUILDINGS_ARCHETYPE_DISTRIBUTION
    ).archetype_distribution
    assert sum(item.building_count for item in archetypes) > 0
    assert sum(item.share for item in archetypes) == pytest.approx(1.0)
    assert next(
        item for item in archetypes if item.archetype is BuildingArchetype.POINT
    ).share == pytest.approx(1.0)

    assert _required_scalar(
        road,
        RawMetricId.ROADS_CONNECTED_COMPONENTS,
    ) == pytest.approx(1.0)
    assert 300.0 <= _required_scalar(
        road,
        RawMetricId.ROADS_LENGTH_DENSITY_KM_PER_KM2,
    ) <= 1_500.0
    assert 1.5 <= _required_scalar(
        road,
        RawMetricId.ROADS_AVERAGE_DEGREE,
    ) <= 2.5
    assert 0.0 <= _required_scalar(
        road,
        RawMetricId.ROADS_INTERSECTION_DENSITY_PER_KM2,
    ) <= 30_000.0
    circuity = road.require(RawMetricId.ROADS_CIRCUITY).scalar_value
    assert circuity is not None
    assert 1.0 <= circuity <= 1.2
    assert 0.0 <= _required_scalar(
        road,
        RawMetricId.ROADS_DEAD_END_RATIO,
    ) <= 0.4

    assert _required_scalar(
        population,
        RawMetricId.DEMOGRAPHY_TOTAL_POPULATION,
    ) == pytest.approx(8.0)
    assert 50_000.0 <= density <= 120_000.0
    assert _required_scalar(
        population,
        RawMetricId.DEMOGRAPHY_JOBS_ESTIMATE,
    ) == pytest.approx(0.0)
    age_distribution = population.require(
        RawMetricId.DEMOGRAPHY_AGE_GROUP_DISTRIBUTION
    ).age_distribution
    assert sum(item.residents for item in age_distribution) == 8
    assert sum(item.share for item in age_distribution) == pytest.approx(1.0)

    validation = _reference_validation_report(
        coverage=coverage,
        far=far,
        density=density,
    )
    assert validation.is_valid is True
    assert len(validation.hard_failures) == 0
    assert len(validation.soft_violations) == 1

    constraint_metrics = ConstraintMetricAdapter().adapt(validation)
    hard_count = _required_scalar(
        constraint_metrics,
        RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT,
    )
    soft_penalty = _required_scalar(
        constraint_metrics,
        RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY,
    )
    assert hard_count == pytest.approx(0.0)
    assert _required_scalar(
        constraint_metrics,
        RawMetricId.CONSTRAINTS_AFFECTED_AREA_M2,
    ) == pytest.approx(0.0)
    assert soft_penalty == pytest.approx(0.5)

    score = _reference_score(
        coverage=coverage,
        far=far,
        density=density,
        hard_violation_count=hard_count,
        weighted_soft_penalty=soft_penalty,
    )
    assert score.score == pytest.approx(0.95)
    assert tuple(item.metric_id for item in score.metrics) == (
        RawMetricId.BUILDINGS_COVERAGE_RATIO,
        RawMetricId.BUILDINGS_FAR,
        RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
        RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT,
        RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY,
    )
    assert sum(item.contribution for item in score.metrics) == pytest.approx(
        score.score
    )
