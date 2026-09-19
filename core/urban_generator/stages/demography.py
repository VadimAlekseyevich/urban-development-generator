from __future__ import annotations

from dataclasses import dataclass

from core.urban_generator.demography import (
    AgeGroupAllocationResult,
    AgeGroupAllocator,
    BuildingAggregationRef,
    DemographicAggregationResult,
    DemographicAggregator,
    DemographicDemandProfile,
    DemographicDemandProfileBuilder,
    DemographicScenario,
    DemographyBlockArea,
    DemographyMetricsBuilder,
    DemographyMetricsResult,
    EmploymentConfig,
    EmploymentEstimateResult,
    EmploymentEstimator,
    EmploymentSubject,
    PopulationAllocationResult,
    PopulationAllocator,
    PopulationRasterSamplingResult,
    ResidentialCapacityCalculator,
    ResidentialCapacityResult,
    ResidentialCapacitySubject,
    SpatialCalibrationPolicy,
    SpatialCalibrationResult,
    SpatialDemographicCalibrator,
)
from core.urban_generator.domain import (
    RunContext,
    RunMode,
    SnapshotLayerRef,
    StageDiagnostic,
    StageDiagnosticLevel,
    StageFingerprint,
    StageResult,
    TerritorySnapshot,
    build_stage_fingerprint,
    require_stage_input,
)
from core.urban_generator.stages.buildings import BuildingStageOutput
from core.urban_generator.stages.catalog import BUILDINGS_STAGE, DEMOGRAPHY_STAGE


@dataclass(frozen=True, slots=True)
class DemographyStageInput:
    buildings: BuildingStageOutput
    baseline_population: int | None = None
    population_raster: PopulationRasterSamplingResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.buildings, BuildingStageOutput):
            raise TypeError("buildings must be BuildingStageOutput")
        if self.baseline_population is not None:
            if (
                isinstance(self.baseline_population, bool)
                or not isinstance(self.baseline_population, int)
                or self.baseline_population < 0
            ):
                raise ValueError(
                    "baseline_population must be a non-negative integer or None"
                )
        if (
            self.population_raster is not None
            and not isinstance(
                self.population_raster,
                PopulationRasterSamplingResult,
            )
        ):
            raise TypeError(
                "population_raster must be PopulationRasterSamplingResult or None"
            )


@dataclass(frozen=True, slots=True)
class DemographyStageConfig:
    scenario: DemographicScenario
    employment: EmploymentConfig
    calibration_policy: SpatialCalibrationPolicy = SpatialCalibrationPolicy()
    max_buildings: int = 100_000
    max_blocks: int = 100_000

    def __post_init__(self) -> None:
        if not isinstance(self.scenario, DemographicScenario):
            raise TypeError("scenario must be DemographicScenario")
        if not isinstance(self.employment, EmploymentConfig):
            raise TypeError("employment must be EmploymentConfig")
        if not isinstance(self.calibration_policy, SpatialCalibrationPolicy):
            raise TypeError(
                "calibration_policy must be SpatialCalibrationPolicy"
            )
        for field_name in ("max_buildings", "max_blocks"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class DemographyStageOutput:
    capacities: ResidentialCapacityResult
    population: PopulationAllocationResult
    age_groups: AgeGroupAllocationResult
    employment: EmploymentEstimateResult
    aggregation: DemographicAggregationResult
    calibration: SpatialCalibrationResult | None
    demand_profile: DemographicDemandProfile
    metrics: DemographyMetricsResult
    fixed_demography_refs: tuple[SnapshotLayerRef, ...]


class DemographyStage:
    """Compose the existing S09 demographic capabilities behind one Stage."""

    name = DEMOGRAPHY_STAGE
    version = "1.0.0"
    dependencies = (BUILDINGS_STAGE,)

    def validate_input(self, value: object) -> DemographyStageInput:
        return require_stage_input(
            value,
            DemographyStageInput,
            stage_name=self.name,
        )

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: DemographyStageInput,
        config: DemographyStageConfig,
    ) -> StageResult[DemographyStageOutput]:
        value = self.validate_input(stage_input)
        if not isinstance(config, DemographyStageConfig):
            raise TypeError("demography config must be DemographyStageConfig")
        self._validate_alignment(
            snapshot=snapshot,
            context=context,
            value=value,
        )

        metrics_by_id = {
            item.building_id: item
            for item in value.buildings.area_metrics.buildings
        }
        attributes_by_id = {
            item.building_id: item
            for item in value.buildings.attributes.buildings
        }
        building_ids = tuple(sorted(metrics_by_id))
        if set(building_ids) != set(attributes_by_id):
            raise ValueError(
                "demography requires exact building metrics/attributes alignment"
            )
        if len(building_ids) > config.max_buildings:
            raise ValueError(
                "demography building limit exceeded: "
                f"{len(building_ids)} > {config.max_buildings}"
            )

        capacity_subjects = tuple(
            ResidentialCapacitySubject(
                metrics=metrics_by_id[building_id],
                attributes=attributes_by_id[building_id],
            )
            for building_id in building_ids
        )
        capacities = ResidentialCapacityCalculator(
            max_subjects=config.max_buildings
        ).calculate(
            capacity_subjects,
            scenario=config.scenario,
        )
        population = PopulationAllocator(
            max_buildings=config.max_buildings
        ).allocate(
            capacities,
            scenario=config.scenario,
            baseline_population=value.baseline_population,
        )
        age_groups = AgeGroupAllocator(
            max_buildings=config.max_buildings
        ).allocate(
            population,
            scenario=config.scenario,
        )

        employment_subjects = tuple(
            EmploymentSubject(
                metrics=metrics_by_id[building_id],
                attributes=attributes_by_id[building_id],
            )
            for building_id in building_ids
        )
        employment = EmploymentEstimator(
            max_subjects=config.max_buildings
        ).estimate(
            employment_subjects,
            population=population,
            scenario=config.scenario,
            config=config.employment,
        )

        ownership_by_id = {
            item.building_id: item for item in value.buildings.ownership
        }
        if set(building_ids) != set(ownership_by_id):
            raise ValueError(
                "demography requires exact building ownership alignment"
            )
        refs = tuple(
            BuildingAggregationRef(
                building_id=building_id,
                block_id=ownership_by_id[building_id].block_id,
                zone_id=ownership_by_id[building_id].zone_id,
                zone_class=ownership_by_id[building_id].zone_class,
            )
            for building_id in building_ids
        )
        aggregation = DemographicAggregator(
            max_buildings=config.max_buildings
        ).aggregate(
            refs,
            population=population,
            age_groups=age_groups,
            employment=employment,
        )

        calibration: SpatialCalibrationResult | None = None
        effective_aggregation = aggregation
        if value.population_raster is not None:
            calibration = SpatialDemographicCalibrator(
                policy=config.calibration_policy
            ).calibrate(
                aggregation,
                raster=value.population_raster,
            )
            effective_aggregation = calibration.aggregation

        demand_profile = DemographicDemandProfileBuilder(
            max_blocks=config.max_blocks
        ).build(
            effective_aggregation,
            scenario=config.scenario,
        )

        block_ids = {item.block_id for item in effective_aggregation.blocks}
        block_area_by_id = {
            item.block_id: item.area_m2
            for item in value.buildings.blocks
        }
        missing = block_ids - set(block_area_by_id)
        if missing:
            raise ValueError(
                "demography missing block areas: "
                + ", ".join(sorted(missing))
            )
        block_areas = tuple(
            DemographyBlockArea(
                block_id=block_id,
                area_m2=block_area_by_id[block_id],
            )
            for block_id in sorted(block_ids)
        )
        if len(block_areas) > config.max_blocks:
            raise ValueError(
                "demography block limit exceeded: "
                f"{len(block_areas)} > {config.max_blocks}"
            )
        metrics = DemographyMetricsBuilder(
            max_blocks=config.max_blocks
        ).build(
            effective_aggregation,
            block_areas=block_areas,
        )
        output = DemographyStageOutput(
            capacities=capacities,
            population=population,
            age_groups=age_groups,
            employment=employment,
            aggregation=effective_aggregation,
            calibration=calibration,
            demand_profile=demand_profile,
            metrics=metrics,
            fixed_demography_refs=snapshot.demography,
        )
        unmet = population.diagnostics.unmet_generated_population
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
                    code="demography.completed",
                    message=(
                        f"demography completed: "
                        f"{population.diagnostics.allocated_generated_population} "
                        f"generated residents, "
                        f"{employment.summary.total_jobs_estimate:.6g} jobs, "
                        f"{len(effective_aggregation.blocks)} blocks, "
                        f"{unmet} unmet residents"
                    ),
                    level=(
                        StageDiagnosticLevel.WARNING
                        if unmet
                        else StageDiagnosticLevel.INFO
                    ),
                ),
            ),
        )

    @staticmethod
    def _validate_alignment(
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        value: DemographyStageInput,
    ) -> None:
        if snapshot.settings.working_srid != context.working_srid:
            raise ValueError("demography working_srid must match snapshot")
        if (
            value.buildings.area_metrics.working_srid
            != context.working_srid
        ):
            raise ValueError(
                "building metrics working_srid must match run context"
            )
        if context.mode is RunMode.FROM_SCRATCH:
            if snapshot.demography:
                raise ValueError(
                    "FROM_SCRATCH demography stage must not consume fixed demography refs"
                )
            if value.baseline_population not in (None, 0):
                raise ValueError(
                    "FROM_SCRATCH baseline_population must be absent or zero"
                )
        elif snapshot.demography and value.baseline_population is None:
            raise ValueError(
                "EXPANSION fixed demography refs require baseline_population"
            )


def _fingerprint(
    *,
    snapshot: TerritorySnapshot,
    context: RunContext,
    config: DemographyStageConfig,
    output: DemographyStageOutput,
) -> StageFingerprint:
    parts: list[str | bytes] = [
        DemographyStage.name,
        DemographyStage.version,
        str(snapshot.snapshot_id),
        str(context.seed),
        context.mode.value,
        config.scenario.fingerprint,
        config.employment.fingerprint,
        repr(config.calibration_policy),
        "|".join(ref.source_ref for ref in snapshot.demography),
        str(output.population.diagnostics.baseline_population),
        str(output.population.diagnostics.target_total_population),
        str(output.population.diagnostics.allocated_generated_population),
    ]
    for building in output.population.allocations:
        parts.extend(
            (
                building.building_id,
                str(building.residents),
                repr(building.resident_capacity),
            )
        )
    for block in output.aggregation.blocks:
        parts.extend(
            (
                block.block_id,
                block.zone_id,
                block.zone_class.value,
                str(block.population),
                repr(block.jobs_estimate),
            )
        )
        for group in block.age_groups:
            parts.extend((group.code, str(group.residents)))
    if output.calibration is not None:
        for item in output.calibration.block_calibrations:
            parts.extend(
                (
                    item.block_id,
                    str(item.original_population),
                    str(item.calibrated_population),
                    "1" if item.raster_evidence_used else "0",
                )
            )
    return build_stage_fingerprint(*parts)
