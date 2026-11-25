"""Canonical final validation and metric stages; no DB or worker dependencies."""

from __future__ import annotations

from dataclasses import dataclass, replace

from core.urban_generator.constraints import (
    AggregateConstraintSubject,
    AggregateMetricBound,
    AggregateMetricValue,
    ConstraintRegistry,
    GeometryExclusionConstraint,
    GeometryExclusionIndex,
    GeometryExclusionSubject,
    RegisteredConstraintEngine,
    SoftAggregatePreference,
    build_aggregate_bound_registrations,
    build_soft_preference_registrations,
)
from core.urban_generator.domain import (
    CANONICAL_METRIC_REGISTRY,
    ConstraintEntityRef,
    ConstraintProblemGeometry,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    MetricValueKind,
    RawMetricId,
    RunContext,
    StageDiagnostic,
    StageDiagnosticLevel,
    StageFingerprintWriter,
    StageResult,
    TerritorySnapshot,
    ValidationReport,
    aggregate_validation_reports,
    require_stage_input,
    serialize_validation_report,
)
from core.urban_generator.metrics import (
    CompositeScoreConfig,
    CompositeScoreRawMetric,
    CompositeScoreResult,
    ConstraintMetricAdapter,
    DemographyMetricAdapter,
    LandBuildingMetricAdapter,
    MetricNormalizationProfile,
    RoadMetricAdapter,
    build_composite_score,
)
from core.urban_generator.stages.blocks import BlocksAndParcelsStageOutput
from core.urban_generator.stages.buildings import BuildingStageOutput
from core.urban_generator.stages.catalog import (
    FINAL_VALIDATION_STAGE,
    INFRASTRUCTURE_STAGE,
    METRICS_STAGE,
)
from core.urban_generator.stages.constraints import ConstraintMaskStageInput
from core.urban_generator.stages.demography import DemographyStageOutput
from core.urban_generator.stages.infrastructure import InfrastructureStageOutput
from core.urban_generator.stages.roads import RoadStageOutput
from core.urban_generator.stages.zoning import ZoningStageOutput
from core.urban_generator.suitability import WeightedSuitabilityResult

_MAX_VALIDATION_RESULTS = 10_000


@dataclass(frozen=True, slots=True)
class FinalValidationStageConfig:
    hard_bounds: tuple[AggregateMetricBound, ...]
    soft_preferences: tuple[SoftAggregatePreference, ...] = ()
    max_results: int = _MAX_VALIDATION_RESULTS

    def __post_init__(self) -> None:
        # Use the canonical rule registrations to enforce exact aggregate IDs.
        build_aggregate_bound_registrations(self.hard_bounds)
        build_soft_preference_registrations(self.soft_preferences)
        if type(self.max_results) is not int or not (
            1 <= self.max_results <= _MAX_VALIDATION_RESULTS
        ):
            raise ValueError("final validation max_results is outside the bound")


@dataclass(frozen=True, slots=True)
class FinalValidationStageInput:
    prepared: ConstraintMaskStageInput
    suitability: WeightedSuitabilityResult
    zoning: ZoningStageOutput
    roads: RoadStageOutput
    blocks: BlocksAndParcelsStageOutput
    buildings: BuildingStageOutput
    demography: DemographyStageOutput
    infrastructure: InfrastructureStageOutput


@dataclass(frozen=True, slots=True)
class FinalValidationStageOutput:
    report: ValidationReport
    scalar_metrics: tuple[CompositeScoreRawMetric, ...]


class FinalValidationStage:
    """Evaluate real canonical outputs and preserve spatial evidence of failures."""

    name = FINAL_VALIDATION_STAGE
    version = "1.0.0"
    dependencies = (INFRASTRUCTURE_STAGE,)

    def validate_input(self, value: object) -> FinalValidationStageInput:
        return require_stage_input(value, FinalValidationStageInput, stage_name=self.name)

    def execute(
        self, *, snapshot: TerritorySnapshot, context: RunContext,
        stage_input: FinalValidationStageInput, config: FinalValidationStageConfig,
    ) -> StageResult[FinalValidationStageOutput]:
        value = self.validate_input(stage_input)
        if not isinstance(config, FinalValidationStageConfig):
            raise TypeError("final_validation requires FinalValidationStageConfig")
        srid = context.working_srid
        if (
            snapshot.snapshot_id != context.run_id or snapshot.settings.working_srid != srid
            or value.prepared.grid != value.suitability.grid
            or value.prepared.grid.working_srid != srid
        ):
            raise ValueError("final validation run or grid mismatch")
        land = LandBuildingMetricAdapter().adapt(
            suitability=value.suitability, blocks=value.blocks, buildings=value.buildings,
        )
        roads = RoadMetricAdapter().adapt(roads=value.roads)
        population = DemographyMetricAdapter().adapt(demography=value.demography)
        scalars: dict[RawMetricId, float | None] = {}
        def add_scalar(metric_id: RawMetricId, scalar: float | None) -> None:
            if CANONICAL_METRIC_REGISTRY.get(metric_id).value_kind is MetricValueKind.SCALAR:
                if metric_id in scalars:
                    raise ValueError("duplicate authoritative scalar metric")
                scalars[metric_id] = scalar

        for land_metric in land.raw_metrics:
            add_scalar(land_metric.metric_id, land_metric.scalar_value)
        for road_metric in roads.raw_metrics:
            add_scalar(road_metric.metric_id, road_metric.scalar_value)
        for population_metric in population.raw_metrics:
            add_scalar(population_metric.metric_id, population_metric.scalar_value)
        for infrastructure_metric in value.infrastructure.metrics.raw_metrics:
            add_scalar(infrastructure_metric.metric_id, infrastructure_metric.scalar_value)
        subject = AggregateConstraintSubject(values=tuple(
            AggregateMetricValue(metric_id=metric_id, value=number)
            for metric_id, number in scalars.items() if number is not None
        ))
        engine = RegisteredConstraintEngine(ConstraintRegistry((
            *build_aggregate_bound_registrations(config.hard_bounds),
            *build_soft_preference_registrations(config.soft_preferences),
        )))
        aggregate = engine.evaluate(
            subject=subject, stage=self.name, scope=ConstraintScope.TERRITORY,
            snapshot=snapshot, context=context,
        )
        prior_count = len(value.zoning.constraints.report.results) + len(aggregate.results)
        if prior_count > config.max_results:
            raise ValueError("final validation report exceeds result budget")
        results: list[ConstraintResult] = []

        def append(result: ConstraintResult) -> None:
            if len(results) + prior_count >= config.max_results:
                raise ValueError("final validation report exceeds result budget")
            results.append(result)

        edges = {edge.edge_id: edge.geometry for edge in value.roads.graph.edges}
        for issue in value.roads.validation.issues:
            geometry = edges.get(issue.edge_id) if issue.edge_id is not None else None
            append(ConstraintResult(
                code=f"roads.{issue.code.value.lower()}", severity=ConstraintSeverity.HARD,
                scope=ConstraintScope.ROAD, passed=False, message=issue.message,
                entity_ref=ConstraintEntityRef(issue.edge_id) if issue.edge_id else None,
                problem_geometry=(
                    ConstraintProblemGeometry(geometry=geometry, working_srid=srid)
                    if geometry is not None and not geometry.is_empty else None
                ),
            ))

        exclusion = GeometryExclusionIndex(
            boundary=value.prepared.boundary.geometry, working_srid=srid,
            protected=tuple(
                geom for layer in value.prepared.geometry_layers for geom in layer.geometries
            ),
        )
        building_rule = GeometryExclusionConstraint(
            scope=ConstraintScope.BUILDING, index=exclusion,
        )
        site_rule = GeometryExclusionConstraint(
            scope=ConstraintScope.INFRASTRUCTURE, index=exclusion,
        )
        building_failures = 0
        for building in value.buildings.area_subjects:
            result = building_rule.evaluate(
                subject=GeometryExclusionSubject(building.geometry, building.working_srid),
                snapshot=snapshot, context=context,
            )
            if not result.passed:
                building_failures += 1
                append(replace(
                    result, entity_ref=ConstraintEntityRef(building.building_id),
                    problem_geometry=ConstraintProblemGeometry(building.geometry, srid),
                ))
        if not building_failures:
            append(ConstraintResult(
                code="buildings.geometry.exclusion", severity=ConstraintSeverity.HARD,
                scope=ConstraintScope.BUILDING, passed=True,
                message=f"Checked {len(value.buildings.area_subjects)} generated buildings",
            ))
        site_count = 0
        site_failures = 0
        for type_output in value.infrastructure.types:
            sites = {item.candidate_id: item for item in type_output.candidate_geometry.candidates}
            for accepted in type_output.placement.accepted_facilities:
                site = sites[accepted.candidate_ref.candidate_id]
                if site.site_geometry is None:
                    # Host-building geometry is covered by the building checks.
                    continue
                site_count += 1
                result = site_rule.evaluate(
                    subject=GeometryExclusionSubject(site.site_geometry, site.working_srid),
                    snapshot=snapshot, context=context,
                )
                if not result.passed:
                    site_failures += 1
                    append(replace(
                        result, entity_ref=ConstraintEntityRef(site.candidate_id),
                        problem_geometry=ConstraintProblemGeometry(site.site_geometry, srid),
                    ))
        if not site_failures:
            append(ConstraintResult(
                code="infrastructure.geometry.exclusion", severity=ConstraintSeverity.HARD,
                scope=ConstraintScope.INFRASTRUCTURE, passed=True,
                message=f"Checked {site_count} standalone generated facilities",
            ))
        report = aggregate_validation_reports((
            value.zoning.constraints.report, ValidationReport(results=tuple(results)), aggregate,
        ))
        for metric in ConstraintMetricAdapter().adapt(report).raw_metrics:
            scalars[metric.metric_id] = metric.scalar_value
        output = FinalValidationStageOutput(
            report=report,
            scalar_metrics=tuple(
                CompositeScoreRawMetric(metric_id=metric_id, raw_value=scalars[metric_id])
                for metric_id in CANONICAL_METRIC_REGISTRY.metric_ids if metric_id in scalars
            ),
        )
        fingerprint = StageFingerprintWriter()
        for text in (self.name, self.version, str(snapshot.snapshot_id)):
            fingerprint.add_text(text)
        fingerprint.add_bytes(serialize_validation_report(report))
        for raw in output.scalar_metrics:
            fingerprint.add_text(raw.metric_id.value)
            fingerprint.add_text(repr(raw.raw_value))
        return StageResult(
            output=output, fingerprint=fingerprint.finalize(),
            diagnostics=(StageDiagnostic(
                code="final_validation.completed",
                message=f"Validation found {len(report.hard_failures)} hard failures",
                level=(
                    StageDiagnosticLevel.INFO if report.is_valid else StageDiagnosticLevel.WARNING
                ),
            ),),
        )


@dataclass(frozen=True, slots=True)
class MetricsStageConfig:
    normalization_profile: MetricNormalizationProfile
    score: CompositeScoreConfig

    def __post_init__(self) -> None:
        if set(self.normalization_profile.metric_ids) != {
            item.metric_id for item in self.score.weights
        }:
            raise ValueError("score and normalization metric identities must match")


class MetricsStage:
    """Compute the existing canonical score from the actual final-validation scalars."""

    name = METRICS_STAGE
    version = "1.0.0"
    dependencies = (FINAL_VALIDATION_STAGE,)

    def validate_input(self, value: object) -> FinalValidationStageOutput:
        return require_stage_input(value, FinalValidationStageOutput, stage_name=self.name)

    def execute(
        self, *, snapshot: TerritorySnapshot, context: RunContext,
        stage_input: FinalValidationStageOutput, config: MetricsStageConfig,
    ) -> StageResult[CompositeScoreResult]:
        value = self.validate_input(stage_input)
        if not isinstance(config, MetricsStageConfig):
            raise TypeError("metrics requires MetricsStageConfig")
        if snapshot.snapshot_id != context.run_id:
            raise ValueError("metrics run identity mismatch")
        by_id = {item.metric_id: item for item in value.scalar_metrics}
        if len(by_id) != len(value.scalar_metrics):
            raise ValueError("duplicate scalar metric identities")
        requested = config.normalization_profile.metric_ids
        if not set(requested) <= set(by_id):
            raise ValueError("configured score metric has no authoritative source")
        score = build_composite_score(
            tuple(by_id[item] for item in requested),
            normalization_profile=config.normalization_profile, config=config.score,
        )
        fingerprint = StageFingerprintWriter()
        for part in (self.name, self.version, str(context.run_id), repr(score)):
            fingerprint.add_text(part)
        return StageResult(
            output=score, fingerprint=fingerprint.finalize(),
            diagnostics=(StageDiagnostic(
                code="metrics.completed", message=f"Computed {len(score.metrics)} score metrics",
            ),),
        )
