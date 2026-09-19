from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from core.urban_generator.domain import (
    ConstraintEngine,
    RunContext,
    SnapshotLayerRef,
    StageDiagnostic,
    StageDiagnosticLevel,
    StageResult,
    TerritorySnapshot,
    build_stage_fingerprint,
    require_stage_input,
)
from core.urban_generator.stages.catalog import SUITABILITY_STAGE, ZONING_STAGE
from core.urban_generator.suitability import WeightedSuitabilityResult
from core.urban_generator.zoning import (
    BaseZoningPartitioner,
    BoundedRegionRefiner,
    DeterministicZoningSeedGenerator,
    SuitabilityTargetShareAssigner,
    ZoneAssignmentResult,
    GeneratedZoneRef,
    ZoneConstraintEvaluationResult,
    ZoneConstraintEvaluator,
    ZoneRefinementResult,
    ZoningConfig,
    build_generated_zone_refs,
    ZoningPartitionResult,
)


@dataclass(frozen=True, slots=True)
class ZoningStageInput:
    suitability: WeightedSuitabilityResult
    developable_area: BaseGeometry


@dataclass(frozen=True, slots=True)
class ZoningStageConfig:
    zoning: ZoningConfig
    seed_count: int
    max_refinement_iterations: int = 100

    def __post_init__(self) -> None:
        if isinstance(self.seed_count, bool) or not isinstance(self.seed_count, int):
            raise TypeError("seed_count must be an integer")
        if self.seed_count <= 0:
            raise ValueError("seed_count must be positive")
        if (
            isinstance(self.max_refinement_iterations, bool)
            or not isinstance(self.max_refinement_iterations, int)
        ):
            raise TypeError("max_refinement_iterations must be an integer")
        if self.max_refinement_iterations <= 0:
            raise ValueError("max_refinement_iterations must be positive")


@dataclass(frozen=True, slots=True)
class ZoningStageOutput:
    partition: ZoningPartitionResult
    refinement: ZoneRefinementResult
    constraints: ZoneConstraintEvaluationResult
    generated_zone_refs: tuple[GeneratedZoneRef, ...]
    fixed_zone_refs: tuple[SnapshotLayerRef, ...]

    @property
    def assignment(self) -> ZoneAssignmentResult:
        return self.refinement.assignment


class ZoningStage:
    """Compose deterministic zoning algorithms while preserving fixed-zone references."""

    name = ZONING_STAGE
    version = "1.0.0"
    dependencies = (SUITABILITY_STAGE,)

    def __init__(
        self,
        *,
        constraint_engine: ConstraintEngine,
        seed_generator: DeterministicZoningSeedGenerator | None = None,
        partitioner: BaseZoningPartitioner | None = None,
        assigner: SuitabilityTargetShareAssigner | None = None,
        refiner: BoundedRegionRefiner | None = None,
        constraint_evaluator: ZoneConstraintEvaluator | None = None,
    ) -> None:
        if not callable(getattr(constraint_engine, "evaluate", None)):
            raise TypeError("constraint_engine must implement evaluate()")
        self._constraint_engine = constraint_engine
        self._seed_generator = seed_generator or DeterministicZoningSeedGenerator()
        self._partitioner = partitioner or BaseZoningPartitioner()
        self._assigner = assigner or SuitabilityTargetShareAssigner()
        self._refiner = refiner or BoundedRegionRefiner()
        self._constraint_evaluator = constraint_evaluator or ZoneConstraintEvaluator()

    def validate_input(self, value: object) -> ZoningStageInput:
        return require_stage_input(value, ZoningStageInput, stage_name=self.name)

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: ZoningStageInput,
        config: ZoningStageConfig,
    ) -> StageResult[ZoningStageOutput]:
        value = self.validate_input(stage_input)
        if not isinstance(config, ZoningStageConfig):
            raise TypeError("zoning config must be ZoningStageConfig")
        working_srid = value.suitability.grid.working_srid
        if working_srid != snapshot.settings.working_srid:
            raise ValueError("zoning suitability working_srid must match snapshot")
        if working_srid != context.working_srid:
            raise ValueError("zoning suitability working_srid must match run context")
        if not isinstance(value.developable_area, BaseGeometry):
            raise TypeError("developable_area must be a Shapely geometry")
        if value.developable_area.is_empty or not value.developable_area.is_valid:
            raise ValueError("developable_area must be non-empty and valid")
        if value.developable_area.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("developable_area must be polygonal")

        seeds = self._seed_generator.generate(
            suitability=value.suitability,
            context=context,
            count=config.seed_count,
        )
        partition = self._partitioner.partition(
            seeds=seeds,
            developable_area=value.developable_area,
            working_srid=working_srid,
        )
        initial_assignment = self._assigner.assign(
            partition=partition,
            config=config.zoning,
        )
        refinement = self._refiner.refine(
            partition=partition,
            assignment=initial_assignment,
            config=config.zoning,
            max_iterations=config.max_refinement_iterations,
        )
        constraints = self._constraint_evaluator.evaluate(
            partition=partition,
            assignment=refinement.assignment,
            snapshot=snapshot,
            context=context,
            engine=self._constraint_engine,
        )
        generated_zone_refs = build_generated_zone_refs(
            run_id=context.run_id,
            partition=partition,
            assignment=refinement.assignment,
        )
        output = ZoningStageOutput(
            partition=partition,
            refinement=refinement,
            constraints=constraints,
            generated_zone_refs=generated_zone_refs,
            fixed_zone_refs=snapshot.fixed_zones,
        )

        parts: list[str | bytes] = [
            self.name,
            self.version,
            str(snapshot.snapshot_id),
            str(context.seed),
            config.zoning.fingerprint,
            str(config.seed_count),
            str(config.max_refinement_iterations),
            "|".join(layer.source_ref for layer in snapshot.fixed_zones),
        ]
        for cell, assignment in zip(
            partition.cells,
            refinement.assignment.assignments,
            strict=True,
        ):
            parts.extend(
                (
                    str(cell.seed_index),
                    cell.geometry.wkb,
                    assignment.zone_class.value,
                    repr(assignment.area_m2),
                )
            )
        for zone_ref in generated_zone_refs:
            parts.extend(
                (
                    zone_ref.zone_id,
                    str(zone_ref.cell_index),
                    zone_ref.zone_class.value,
                )
            )
        for result in constraints.report.results:
            parts.extend(
                (
                    result.code,
                    result.severity.value,
                    result.scope.value,
                    "1" if result.passed else "0",
                    result.message,
                )
            )

        level = (
            StageDiagnosticLevel.WARNING
            if constraints.invalid_zone_count
            else StageDiagnosticLevel.INFO
        )
        return StageResult(
            output=output,
            fingerprint=build_stage_fingerprint(*parts),
            diagnostics=(
                StageDiagnostic(
                    code="zoning.completed",
                    message=(
                        f"zoning completed: {len(partition.cells)} zones, "
                        f"{constraints.invalid_zone_count} invalid, "
                        f"{refinement.iterations} refinement iterations"
                    ),
                    level=level,
                ),
            ),
        )
