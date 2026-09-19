import uuid

import numpy as np
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
from core.urban_generator.constraints import ConstraintRegistry, RegisteredConstraintEngine
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
    ProjectRef,
    ProjectSettings,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
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
