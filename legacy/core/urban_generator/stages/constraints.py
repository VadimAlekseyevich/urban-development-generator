from dataclasses import dataclass

from core.urban_generator.domain import (
    RunContext,
    StageDiagnostic,
    StageResult,
    TerritorySnapshot,
    build_stage_fingerprint,
    require_stage_input,
)
from core.urban_generator.stages.catalog import (
    EVALUATE_CONSTRAINTS_STAGE,
    PREPARE_SNAPSHOT_STAGE,
)
from core.urban_generator.suitability import (
    ExclusionRasterizationPolicy,
    HardExclusionBoundary,
    HardExclusionGeometryLayer,
    HardExclusionMask,
    HardExclusionRasterLayer,
    SuitabilityGridSpec,
    build_hard_exclusion_mask,
)


@dataclass(frozen=True, slots=True)
class ConstraintMaskStageInput:
    grid: SuitabilityGridSpec
    boundary: HardExclusionBoundary
    geometry_layers: tuple[HardExclusionGeometryLayer, ...] = ()
    raster_layers: tuple[HardExclusionRasterLayer, ...] = ()


@dataclass(frozen=True, slots=True)
class ConstraintMaskStageConfig:
    exclusion_policy: ExclusionRasterizationPolicy = ExclusionRasterizationPolicy.ANY_TOUCH
    max_shapes: int = 100_000
    max_cells: int = 25_000_000


class ConstraintMaskStage:
    """Build the canonical hard-exclusion mask for later suitability evaluation."""

    name = EVALUATE_CONSTRAINTS_STAGE
    version = "1.0.0"
    dependencies = (PREPARE_SNAPSHOT_STAGE,)

    def validate_input(self, value: object) -> ConstraintMaskStageInput:
        return require_stage_input(value, ConstraintMaskStageInput, stage_name=self.name)

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: ConstraintMaskStageInput,
        config: ConstraintMaskStageConfig,
    ) -> StageResult[HardExclusionMask]:
        value = self.validate_input(stage_input)
        if not isinstance(config, ConstraintMaskStageConfig):
            raise TypeError("evaluate_constraints config must be ConstraintMaskStageConfig")
        if value.grid.working_srid != snapshot.settings.working_srid:
            raise ValueError("constraint grid working_srid must match snapshot")
        if value.grid.working_srid != context.working_srid:
            raise ValueError("constraint grid working_srid must match run context")

        mask = build_hard_exclusion_mask(
            grid=value.grid,
            boundary=value.boundary,
            geometry_layers=value.geometry_layers,
            raster_layers=value.raster_layers,
            exclusion_policy=config.exclusion_policy,
            max_shapes=config.max_shapes,
            max_cells=config.max_cells,
        )
        fingerprint = build_stage_fingerprint(
            self.name,
            self.version,
            str(snapshot.snapshot_id),
            str(context.seed),
            str(value.grid.working_srid),
            repr(value.grid.bounds),
            str(value.grid.width),
            str(value.grid.height),
            config.exclusion_policy.value,
            str(config.max_shapes),
            str(config.max_cells),
            "|".join(mask.source_codes),
            mask.excluded.tobytes(order="C"),
        )
        return StageResult(
            output=mask,
            fingerprint=fingerprint,
            diagnostics=(
                StageDiagnostic(
                    code="constraints.mask.completed",
                    message=(
                        f"hard exclusion mask completed: "
                        f"{mask.excluded_count}/{mask.grid.cell_count} cells excluded"
                    ),
                ),
            ),
        )
