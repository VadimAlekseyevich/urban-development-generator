from __future__ import annotations

from dataclasses import dataclass

from core.urban_generator.blocks import (
    BlockDevelopableArea,
    BlockDevelopableClippingResult,
    BlockFrontagePolicy,
    BlockFrontageValidationResult,
    BlockFrontageValidator,
    BlockHardConstraintLayer,
    BlockMetricsCalculator,
    BlockMetricsResult,
    BlockSliverCleaner,
    BlockZoneAssociationResult,
    BlockZoneAssociator,
    BlockZoneReference,
    DevelopableBlockClipper,
    OversizedBlockSplitPolicy,
    OversizedBlockSplitResult,
    OversizedBlockSplitter,
    ParcelSubdivisionPolicy,
    ParcelSubdivisionResult,
    RoadNetworkBlockPolygonizer,
    SimplifiedParcelSubdivider,
    SliverCleanupPolicy,
    SliverCleanupResult,
)
from core.urban_generator.domain import (
    RunContext,
    StageDiagnostic,
    StageDiagnosticLevel,
    StageFingerprint,
    StageResult,
    TerritorySnapshot,
    build_stage_fingerprint,
    require_stage_input,
)
from core.urban_generator.roads import RoadGraph
from core.urban_generator.stages.catalog import (
    BLOCKS_AND_PARCELS_STAGE,
    ROADS_STAGE,
)
from core.urban_generator.zoning import GeneratedZoneRef


@dataclass(frozen=True, slots=True)
class BlocksAndParcelsStageInput:
    road_graph: RoadGraph
    developable_area: BlockDevelopableArea
    generated_zone_refs: tuple[GeneratedZoneRef, ...]
    hard_constraints: tuple[BlockHardConstraintLayer, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.road_graph, RoadGraph):
            raise TypeError("road_graph must be RoadGraph")
        if not isinstance(self.developable_area, BlockDevelopableArea):
            raise TypeError("developable_area must be BlockDevelopableArea")
        if not isinstance(self.generated_zone_refs, tuple):
            raise TypeError("generated_zone_refs must be an immutable tuple")
        if any(
            not isinstance(item, GeneratedZoneRef)
            for item in self.generated_zone_refs
        ):
            raise TypeError(
                "generated_zone_refs must contain GeneratedZoneRef values"
            )
        if not isinstance(self.hard_constraints, tuple):
            raise TypeError("hard_constraints must be an immutable tuple")
        if any(
            not isinstance(item, BlockHardConstraintLayer)
            for item in self.hard_constraints
        ):
            raise TypeError(
                "hard_constraints must contain BlockHardConstraintLayer values"
            )


@dataclass(frozen=True, slots=True)
class BlocksAndParcelsStageConfig:
    frontage_policy: BlockFrontagePolicy
    split_policy: OversizedBlockSplitPolicy
    sliver_policy: SliverCleanupPolicy
    parcel_policy: ParcelSubdivisionPolicy

    def __post_init__(self) -> None:
        expected = (
            ("frontage_policy", BlockFrontagePolicy),
            ("split_policy", OversizedBlockSplitPolicy),
            ("sliver_policy", SliverCleanupPolicy),
            ("parcel_policy", ParcelSubdivisionPolicy),
        )
        for field_name, expected_type in expected:
            if not isinstance(getattr(self, field_name), expected_type):
                raise TypeError(f"{field_name} must be {expected_type.__name__}")


@dataclass(frozen=True, slots=True)
class BlocksAndParcelsStageOutput:
    clipping: BlockDevelopableClippingResult
    metrics: BlockMetricsResult
    frontage: BlockFrontageValidationResult
    split: OversizedBlockSplitResult
    cleanup: SliverCleanupResult
    association: BlockZoneAssociationResult
    subdivision: ParcelSubdivisionResult


class BlocksAndParcelsStage:
    """Compose the existing S07 block/parcel capabilities behind one Stage."""

    name = BLOCKS_AND_PARCELS_STAGE
    version = "1.0.0"
    dependencies = (ROADS_STAGE,)

    def validate_input(self, value: object) -> BlocksAndParcelsStageInput:
        return require_stage_input(
            value,
            BlocksAndParcelsStageInput,
            stage_name=self.name,
        )

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: BlocksAndParcelsStageInput,
        config: BlocksAndParcelsStageConfig,
    ) -> StageResult[BlocksAndParcelsStageOutput]:
        value = self.validate_input(stage_input)
        if not isinstance(config, BlocksAndParcelsStageConfig):
            raise TypeError(
                "blocks_and_parcels config must be BlocksAndParcelsStageConfig"
            )
        self._validate_alignment(
            snapshot=snapshot,
            context=context,
            value=value,
        )
        working_srid = context.working_srid

        polygonization = RoadNetworkBlockPolygonizer(
            working_srid=working_srid
        ).polygonize(value.road_graph)
        clipping = DevelopableBlockClipper(
            working_srid=working_srid
        ).clip(
            polygonization,
            area=value.developable_area,
            hard_constraints=value.hard_constraints,
        )
        metrics = BlockMetricsCalculator(
            working_srid=working_srid
        ).calculate(clipping)
        frontage = BlockFrontageValidator(
            working_srid=working_srid,
            policy=config.frontage_policy,
        ).validate(
            metrics,
            road_graph=value.road_graph,
        )
        split = OversizedBlockSplitter(
            working_srid=working_srid,
            policy=config.split_policy,
        ).split(
            frontage,
            road_graph=value.road_graph,
        )
        cleanup = BlockSliverCleaner(
            working_srid=working_srid,
            policy=config.sliver_policy,
        ).cleanup(split)

        zones = tuple(
            BlockZoneReference(
                zone_id=ref.zone_id,
                zone_class=ref.zone_class,
                geometry=ref.geometry,
                working_srid=ref.working_srid,
            )
            for ref in value.generated_zone_refs
        )
        association = BlockZoneAssociator(
            working_srid=working_srid
        ).associate(
            cleanup,
            zones=zones,
        )
        subdivision = SimplifiedParcelSubdivider(
            working_srid=working_srid,
            policy=config.parcel_policy,
        ).subdivide(
            association,
            road_graph=value.road_graph,
        )
        output = BlocksAndParcelsStageOutput(
            clipping=clipping,
            metrics=metrics,
            frontage=frontage,
            split=split,
            cleanup=cleanup,
            association=association,
            subdivision=subdivision,
        )

        unassociated = (
            association.diagnostics.block_count
            - association.diagnostics.associated_block_count
        )
        level = (
            StageDiagnosticLevel.WARNING
            if unassociated
            else StageDiagnosticLevel.INFO
        )
        return StageResult(
            output=output,
            fingerprint=_fingerprint(
                snapshot=snapshot,
                context=context,
                config=config,
                output=output,
            ),
            diagnostics=(
                StageDiagnostic(
                    code="blocks_and_parcels.completed",
                    message=(
                        f"blocks/parcels completed: "
                        f"{association.diagnostics.block_count} blocks, "
                        f"{subdivision.diagnostics.parcel_count} parcels, "
                        f"{unassociated} unassociated blocks"
                    ),
                    level=level,
                ),
            ),
        )

    @staticmethod
    def _validate_alignment(
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        value: BlocksAndParcelsStageInput,
    ) -> None:
        working_srid = context.working_srid
        if snapshot.settings.working_srid != working_srid:
            raise ValueError("blocks working_srid must match snapshot")
        if value.road_graph.working_crs.srid != working_srid:
            raise ValueError("road graph working_srid must match run context")
        if value.developable_area.working_srid != working_srid:
            raise ValueError("developable area working_srid must match run context")
        for ref in value.generated_zone_refs:
            if ref.working_srid != working_srid:
                raise ValueError(
                    f"zone {ref.zone_id!r} working_srid must match run context"
                )
        for layer in value.hard_constraints:
            if layer.working_srid != working_srid:
                raise ValueError(
                    f"hard constraint {layer.code!r} working_srid must match run context"
                )


def _fingerprint(
    *,
    snapshot: TerritorySnapshot,
    context: RunContext,
    config: BlocksAndParcelsStageConfig,
    output: BlocksAndParcelsStageOutput,
) -> StageFingerprint:
    parts: list[str | bytes] = [
        BlocksAndParcelsStage.name,
        BlocksAndParcelsStage.version,
        str(snapshot.snapshot_id),
        str(context.seed),
        repr(config),
    ]
    for item in output.association.blocks:
        parts.extend(
            (
                item.cleaned_block.block_id,
                item.cleaned_block.geometry.wkb,
                item.association.status.value,
                item.association.zone_id or "",
                item.association.zone_class.value
                if item.association.zone_class is not None
                else "",
            )
        )
    for parcel in output.subdivision.parcels:
        parts.extend(
            (
                parcel.parcel_id,
                parcel.block_id,
                parcel.geometry.wkb,
                parcel.zone_id or "",
            )
        )
    return build_stage_fingerprint(*parts)
