import uuid

from shapely.geometry import LineString, box

from core.urban_generator.blocks import (
    BlockDevelopableArea,
    BlockFrontagePolicy,
    OversizedBlockSplitPolicy,
    ParcelSubdivisionPolicy,
    SliverCleanupPolicy,
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
from core.urban_generator.zoning import (
    GeneratedZoneRef,
    ZoneClass,
    generated_zone_uuid,
)

WORKING_SRID = 32637
RUN_ID = uuid.UUID("00000000-0000-0000-0000-000000000444")


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
        seed=7,
        working_srid=WORKING_SRID,
        config_refs=(ConfigRef(name="generation", ref="synthetic:v1"),),
        correlation=CorrelationMetadata(correlation_id="blocks-stage-test"),
    )


def _square_graph():
    parts = (
        LineString(((0.0, 0.0), (10.0, 0.0))),
        LineString(((10.0, 0.0), (10.0, 10.0))),
        LineString(((10.0, 10.0), (0.0, 10.0))),
        LineString(((0.0, 10.0), (0.0, 0.0))),
    )
    return RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (
            RoadGraphInput(
                road=NodedRoad(
                    road_id="generated:square",
                    parts=parts,
                ),
                state=WorldStateContract.generated_for(RUN_ID),
            ),
        )
    )


def _zone_ref() -> GeneratedZoneRef:
    geometry = box(0.0, 0.0, 10.0, 10.0)
    return GeneratedZoneRef(
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


def _config() -> BlocksAndParcelsStageConfig:
    return BlocksAndParcelsStageConfig(
        frontage_policy=BlockFrontagePolicy(
            access_tolerance_m=0.0,
            minimum_frontage_m=1.0,
        ),
        split_policy=OversizedBlockSplitPolicy(max_area_m2=200.0),
        sliver_policy=SliverCleanupPolicy(min_area_m2=1.0),
        parcel_policy=ParcelSubdivisionPolicy(
            target_frontage_m=5.0,
            minimum_frontage_m=1.0,
            minimum_parcel_area_m2=10.0,
        ),
    )


def test_blocks_stage_composes_existing_s07_capabilities_deterministically() -> None:
    stage = BlocksAndParcelsStage()
    snapshot = _snapshot()
    context = _context()
    stage_input = BlocksAndParcelsStageInput(
        road_graph=_square_graph(),
        developable_area=BlockDevelopableArea(
            project_boundary=box(0.0, 0.0, 10.0, 10.0),
            developable_mask=box(0.0, 0.0, 10.0, 10.0),
            working_srid=WORKING_SRID,
        ),
        generated_zone_refs=(_zone_ref(),),
    )

    result = stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=stage_input,
        config=_config(),
    )

    assert result.output.association.diagnostics.block_count == 1
    assert result.output.association.diagnostics.associated_block_count == 1
    assert result.output.subdivision.diagnostics.parcel_count >= 1
    assert all(
        parcel.zone_id == _zone_ref().zone_id
        for parcel in result.output.subdivision.parcels
    )

    repeated = stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=stage_input,
        config=_config(),
    )
    assert repeated.fingerprint == result.fingerprint
