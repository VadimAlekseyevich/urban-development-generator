import uuid

from shapely.geometry import LineString, box

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
    PlacedBuildingFootprint,
    RectangularPointFootprintSpec,
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
    WorldStateContract,
)
from core.urban_generator.roads import NodedRoad, RoadGraphBuilder, RoadGraphInput
from core.urban_generator.stages.blocks import (
    BlocksAndParcelsStage,
    BlocksAndParcelsStageConfig,
    BlocksAndParcelsStageInput,
)
from core.urban_generator.stages.buildings import (
    BuildingArchetypeStageSpec,
    BuildingStage,
    BuildingStageConfig,
    BuildingStageInput,
    BuildingZoneTarget,
)
from core.urban_generator.zoning import (
    GeneratedZoneRef,
    ZoneClass,
    generated_zone_uuid,
)

WORKING_SRID = 32637
RUN_ID = uuid.UUID("00000000-0000-0000-0000-000000000555")


def _snapshot() -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000111"),
        project=ProjectRef(
            project_id=uuid.UUID("00000000-0000-0000-0000-000000000222")
        ),
        settings=ProjectSettings(working_srid=WORKING_SRID),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
    )


def _context() -> RunContext:
    return RunContext(
        run_id=RUN_ID,
        mode=RunMode.FROM_SCRATCH,
        seed=2026,
        working_srid=WORKING_SRID,
        config_refs=(ConfigRef(name="generation", ref="synthetic:v1"),),
        correlation=CorrelationMetadata(correlation_id="buildings-stage-test"),
    )


def _square_graph():
    return RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (
            RoadGraphInput(
                road=NodedRoad(
                    road_id="generated:square",
                    parts=(
                        LineString(((0.0, 0.0), (10.0, 0.0))),
                        LineString(((10.0, 0.0), (10.0, 10.0))),
                        LineString(((10.0, 10.0), (0.0, 10.0))),
                        LineString(((0.0, 10.0), (0.0, 0.0))),
                    ),
                ),
                state=WorldStateContract.generated_for(RUN_ID),
            ),
        )
    )


def _blocks_output(snapshot: TerritorySnapshot, context: RunContext):
    geometry = box(0.0, 0.0, 10.0, 10.0)
    zone_ref = GeneratedZoneRef(
        zone_id=str(
            generated_zone_uuid(
                RUN_ID,
                cell_index=0,
                seed_index=0,
            )
        ),
        cell_index=0,
        seed_index=0,
        zone_class=ZoneClass.RESIDENTIAL,
        geometry=geometry,
        area_m2=100.0,
        working_srid=WORKING_SRID,
    )
    return BlocksAndParcelsStage().execute(
        snapshot=snapshot,
        context=context,
        stage_input=BlocksAndParcelsStageInput(
            road_graph=_square_graph(),
            developable_area=BlockDevelopableArea(
                project_boundary=geometry,
                developable_mask=geometry,
                working_srid=WORKING_SRID,
            ),
            generated_zone_refs=(zone_ref,),
        ),
        config=BlocksAndParcelsStageConfig(
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
        ),
    ).output


def _config() -> BuildingStageConfig:
    return BuildingStageConfig(
        building=BuildingConfig(
            version="stage-v1",
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
            version="attrs-v1",
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
                footprint_spec=RectangularPointFootprintSpec.point(
                    size_m=2.0
                ),
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


def test_building_stage_composes_s08_pipeline_deterministically() -> None:
    snapshot = _snapshot()
    context = _context()
    stage_input = BuildingStageInput(
        blocks=_blocks_output(snapshot, context)
    )
    stage = BuildingStage()

    result = stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=stage_input,
        config=_config(),
    )

    assert len(result.output.sources) == 1
    assert len(result.output.accepted_proposals) == 4
    assert result.output.sources[0].archetype is BuildingArchetype.POINT
    assert result.output.sources[0].placement is not None
    assert result.output.sources[0].placement.diagnostics.targets_met is True
    assert result.output.area_metrics.summary.coverage_ratio == 0.16
    assert result.output.area_metrics.summary.far == 0.32
    assert all(
        item.attributes.floors == 2
        for item in result.output.area_subjects
    )
    assert result.output.fixed_building_refs == ()
    assert len(result.output.ownership) == 4
    assert {item.block_id for item in result.output.ownership} == {
        result.output.blocks[0].block_id
    }
    assert {item.zone_id for item in result.output.ownership} == {
        result.output.blocks[0].zone_id
    }

    repeated = stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=stage_input,
        config=_config(),
    )
    assert repeated.fingerprint == result.fingerprint
    assert tuple(
        item.building_id for item in repeated.output.area_subjects
    ) == tuple(item.building_id for item in result.output.area_subjects)



def test_building_stage_expansion_preserves_fixed_refs_and_spacing_state() -> None:
    snapshot = TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000112"),
        project=ProjectRef(
            project_id=uuid.UUID("00000000-0000-0000-0000-000000000222")
        ),
        settings=ProjectSettings(working_srid=WORKING_SRID),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
        buildings=(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.BUILDINGS,
                source_ref="synthetic:fixed-buildings:v1",
            ),
        ),
    )
    context = RunContext(
        run_id=RUN_ID,
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=WORKING_SRID,
        config_refs=(ConfigRef(name="generation", ref="synthetic:v1"),),
        correlation=CorrelationMetadata(
            correlation_id="buildings-stage-expansion-test"
        ),
    )
    fixed = PlacedBuildingFootprint(
        building_id="fixed:building:one",
        geometry=box(1.5, 1.5, 3.5, 3.5),
        working_srid=WORKING_SRID,
    )

    result = BuildingStage().execute(
        snapshot=snapshot,
        context=context,
        stage_input=BuildingStageInput(
            blocks=_blocks_output(snapshot, context),
            existing_footprints=(fixed,),
        ),
        config=_config(),
    )

    assert result.output.fixed_building_refs == snapshot.buildings
    assert all(
        proposal.geometry.intersection(fixed.geometry).area == 0.0
        for proposal in result.output.accepted_proposals
    )
