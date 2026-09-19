from __future__ import annotations

from dataclasses import dataclass
from math import fsum

from shapely.geometry.base import BaseGeometry

from core.urban_generator.domain import (
    RunContext,
    RunMode,
    StageDiagnostic,
    StageDiagnosticLevel,
    StageFingerprint,
    StageResult,
    TerritorySnapshot,
    WorldStateContract,
    build_stage_fingerprint,
    require_stage_input,
)
from core.urban_generator.roads import (
    AnchorConnectivityResult,
    CandidateRoadAnchorPolicy,
    CandidateRoadAnchorResult,
    CandidateRoadAnchorSampler,
    LeastCostConnectorPolicy,
    MSTBaselineConnector,
    MSTBaselineConnectorPolicy,
    RoadClassificationOrigin,
    RoadClassificationPolicy,
    RoadClassificationResult,
    RoadClassificationSubject,
    RoadGraph,
    RoadGraphBuilder,
    RoadGraphCleaner,
    RoadGraphCleanupPolicy,
    RoadGraphInput,
    RoadGrowthIntent,
    RoadMetrics,
    RoadMetricsCalculator,
    RoadMetricsPolicy,
    RoadNetworkValidator,
    RoadValidationPolicy,
    RoadValidationResult,
    RuleBasedRoadClassifier,
    RuleBasedRoadGrower,
    RuleBasedRoadGrowthPolicy,
    RuleBasedRoadGrowthResult,
    SemanticNoder,
    SemanticRoad,
)
from core.urban_generator.roads.endpoint_snapping import (
    EndpointRoadSnapper,
    EndpointRoadSnappingPolicy,
)
from core.urban_generator.roads.fixed_network_attachment import (
    FixedNetworkAttachmentConnector,
    FixedNetworkAttachmentPolicy,
    FixedNetworkAttachmentResult,
)
from core.urban_generator.stages.catalog import ROADS_STAGE, ZONING_STAGE
from core.urban_generator.stages.zoning import ZoningStageOutput
from core.urban_generator.suitability import HardExclusionMask, WeightedSuitabilityResult


@dataclass(frozen=True, slots=True)
class FixedRoadStageInput:
    road: SemanticRoad
    existing_class: str

    def __post_init__(self) -> None:
        if not isinstance(self.road, SemanticRoad):
            raise TypeError("fixed road must be a SemanticRoad")
        if not isinstance(self.existing_class, str) or not self.existing_class.strip():
            raise ValueError("existing_class must be a non-empty string")


@dataclass(frozen=True, slots=True)
class RoadStageInput:
    zoning: ZoningStageOutput
    suitability: WeightedSuitabilityResult
    hard_mask: HardExclusionMask
    fixed_roads: tuple[FixedRoadStageInput, ...] = ()
    forbidden_geometries: tuple[BaseGeometry, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.fixed_roads, tuple):
            raise TypeError("fixed_roads must be an immutable tuple")
        if any(not isinstance(item, FixedRoadStageInput) for item in self.fixed_roads):
            raise TypeError("fixed_roads must contain FixedRoadStageInput values")
        if not isinstance(self.forbidden_geometries, tuple):
            raise TypeError("forbidden_geometries must be an immutable tuple")


@dataclass(frozen=True, slots=True)
class RoadStageConfig:
    endpoint_snapping: EndpointRoadSnappingPolicy
    anchor_policy: CandidateRoadAnchorPolicy = CandidateRoadAnchorPolicy()
    least_cost_policy: LeastCostConnectorPolicy = LeastCostConnectorPolicy()
    mst_policy: MSTBaselineConnectorPolicy = MSTBaselineConnectorPolicy()
    growth_policy: RuleBasedRoadGrowthPolicy = RuleBasedRoadGrowthPolicy()
    classification_policy: RoadClassificationPolicy = RoadClassificationPolicy()
    cleanup_policy: RoadGraphCleanupPolicy = RoadGraphCleanupPolicy()
    validation_policy: RoadValidationPolicy = RoadValidationPolicy()
    metrics_policy: RoadMetricsPolicy = RoadMetricsPolicy()
    fixed_attachment_policy: FixedNetworkAttachmentPolicy | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint_snapping, EndpointRoadSnappingPolicy):
            raise TypeError("endpoint_snapping must be EndpointRoadSnappingPolicy")
        expected = (
            ("anchor_policy", CandidateRoadAnchorPolicy),
            ("least_cost_policy", LeastCostConnectorPolicy),
            ("mst_policy", MSTBaselineConnectorPolicy),
            ("growth_policy", RuleBasedRoadGrowthPolicy),
            ("classification_policy", RoadClassificationPolicy),
            ("cleanup_policy", RoadGraphCleanupPolicy),
            ("validation_policy", RoadValidationPolicy),
            ("metrics_policy", RoadMetricsPolicy),
        )
        for field_name, expected_type in expected:
            if not isinstance(getattr(self, field_name), expected_type):
                raise TypeError(f"{field_name} must be {expected_type.__name__}")
        if (
            self.fixed_attachment_policy is not None
            and not isinstance(
                self.fixed_attachment_policy,
                FixedNetworkAttachmentPolicy,
            )
        ):
            raise TypeError(
                "fixed_attachment_policy must be FixedNetworkAttachmentPolicy or None"
            )


@dataclass(frozen=True, slots=True)
class RoadStageOutput:
    anchors: CandidateRoadAnchorResult
    baseline: AnchorConnectivityResult
    growth: RuleBasedRoadGrowthResult
    fixed_attachment: FixedNetworkAttachmentResult | None
    graph: RoadGraph
    classification: RoadClassificationResult
    validation: RoadValidationResult
    metrics: RoadMetrics


class RoadStage:
    """Compose the existing S06 road capabilities behind the canonical Stage contract."""

    name = ROADS_STAGE
    version = "1.0.0"
    dependencies = (ZONING_STAGE,)

    def validate_input(self, value: object) -> RoadStageInput:
        return require_stage_input(value, RoadStageInput, stage_name=self.name)

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: RoadStageInput,
        config: RoadStageConfig,
    ) -> StageResult[RoadStageOutput]:
        value = self.validate_input(stage_input)
        if not isinstance(config, RoadStageConfig):
            raise TypeError("roads config must be RoadStageConfig")
        self._validate_alignment(
            snapshot=snapshot,
            context=context,
            value=value,
            config=config,
        )

        working_srid = context.working_srid
        fixed_by_id = {item.road.road_id: item for item in value.fixed_roads}
        fixed_semantic = tuple(item.road for item in value.fixed_roads)
        fixed_graph = self._build_graph(
            roads=fixed_semantic,
            states={
                road.road_id: WorldStateContract.fixed_source()
                for road in fixed_semantic
            },
            working_srid=working_srid,
            endpoint_policy=config.endpoint_snapping,
            cleanup_policy=config.cleanup_policy,
        )

        anchors = CandidateRoadAnchorSampler().sample(
            partition=value.zoning.partition,
            assignment=value.zoning.assignment,
            suitability=value.suitability,
            policy=config.anchor_policy,
        )
        baseline = MSTBaselineConnector(policy=config.mst_policy).connect(
            anchors=anchors.anchors,
            suitability=value.suitability,
            hard_mask=value.hard_mask,
            pair_policy=config.least_cost_policy,
        )
        growth = RuleBasedRoadGrower(policy=config.growth_policy).grow(
            anchors=anchors.anchors,
            baseline=baseline,
            suitability=value.suitability,
            hard_mask=value.hard_mask,
            pair_policy=config.least_cost_policy,
        )

        generated_roads, generated_origins, generated_growth_intents = (
            _generated_semantic_roads(baseline=baseline, growth=growth)
        )
        fixed_attachment: FixedNetworkAttachmentResult | None = None
        if (
            context.mode is RunMode.EXPANSION
            and fixed_graph is not None
            and anchors.anchors
        ):
            assert config.fixed_attachment_policy is not None
            fixed_attachment = FixedNetworkAttachmentConnector(
                policy=config.fixed_attachment_policy
            ).attach(
                anchors=anchors.anchors,
                connected_pairs=_connected_anchor_pairs(
                    baseline=baseline,
                    growth=growth,
                ),
                fixed_graph=fixed_graph,
                suitability=value.suitability,
                hard_mask=value.hard_mask,
                pair_policy=config.least_cost_policy,
            )
            for road_id, geometry in fixed_attachment.generated_geometries:
                generated_roads.append(
                    SemanticRoad(road_id=road_id, geometry=geometry)
                )
                generated_origins[road_id] = (
                    RoadClassificationOrigin.BASELINE_GENERATED
                )

        all_roads = fixed_semantic + tuple(generated_roads)
        states = {
            **{
                road.road_id: WorldStateContract.fixed_source()
                for road in fixed_semantic
            },
            **{
                road.road_id: WorldStateContract.generated_for(context.run_id)
                for road in generated_roads
            },
        }
        graph = self._build_graph(
            roads=all_roads,
            states=states,
            working_srid=working_srid,
            endpoint_policy=config.endpoint_snapping,
            cleanup_policy=config.cleanup_policy,
        )
        if graph is None:
            graph = RoadGraphBuilder(working_srid=working_srid).build(())

        classification = _classify_final_graph(
            graph=graph,
            fixed_by_id=fixed_by_id,
            generated_origins=generated_origins,
            generated_growth_intents=generated_growth_intents,
            policy=config.classification_policy,
        )
        validation = RoadNetworkValidator(
            policy=config.validation_policy
        ).validate(
            graph=graph,
            forbidden_geometries=value.forbidden_geometries,
        )
        metrics = RoadMetricsCalculator(policy=config.metrics_policy).calculate(
            graph=graph,
            analysis_area_m2=value.zoning.partition.developable_area_m2,
        )
        output = RoadStageOutput(
            anchors=anchors,
            baseline=baseline,
            growth=growth,
            fixed_attachment=fixed_attachment,
            graph=graph,
            classification=classification,
            validation=validation,
            metrics=metrics,
        )

        warnings: list[str] = []
        if not validation.passed:
            warnings.append(
                f"road validation produced {len(validation.issues)} issue(s)"
            )
        if fixed_attachment is not None and not fixed_attachment.complete:
            warnings.append(
                "one or more generated road components are not attached to the fixed network"
            )
        level = (
            StageDiagnosticLevel.WARNING
            if warnings
            else StageDiagnosticLevel.INFO
        )
        message = (
            f"roads completed: {len(graph.nodes)} nodes, {len(graph.edges)} edges, "
            f"{validation.diagnostics.component_count} component(s)"
        )
        if warnings:
            message += "; " + "; ".join(warnings)

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
                    code="roads.completed",
                    message=message,
                    level=level,
                ),
            ),
        )

    @staticmethod
    def _validate_alignment(
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        value: RoadStageInput,
        config: RoadStageConfig,
    ) -> None:
        working_srid = value.suitability.grid.working_srid
        if value.hard_mask.grid != value.suitability.grid:
            raise ValueError("road hard_mask must use exactly the suitability grid")
        if value.zoning.partition.working_srid != working_srid:
            raise ValueError("road zoning and suitability working_srid must match")
        if snapshot.settings.working_srid != working_srid:
            raise ValueError("road working_srid must match snapshot")
        if context.working_srid != working_srid:
            raise ValueError("road working_srid must match run context")

        fixed_ids = tuple(item.road.road_id for item in value.fixed_roads)
        if len(fixed_ids) != len(set(fixed_ids)):
            raise ValueError("fixed road ids must be unique")
        if any(road_id.startswith("generated:") for road_id in fixed_ids):
            raise ValueError("fixed road ids must not use the generated: namespace")

        if context.mode is RunMode.FROM_SCRATCH and value.fixed_roads:
            raise ValueError("FROM_SCRATCH road stage must not consume fixed roads")
        if context.mode is RunMode.EXPANSION:
            if snapshot.roads and not value.fixed_roads:
                raise ValueError(
                    "EXPANSION snapshot road refs require resolved fixed road inputs"
                )
            if value.fixed_roads and not snapshot.roads:
                raise ValueError(
                    "resolved fixed road inputs require fixed road refs in snapshot"
                )
            if value.fixed_roads and config.fixed_attachment_policy is None:
                raise ValueError(
                    "EXPANSION with fixed roads requires fixed_attachment_policy"
                )

    @staticmethod
    def _build_graph(
        *,
        roads: tuple[SemanticRoad, ...],
        states: dict[str, WorldStateContract],
        working_srid: int,
        endpoint_policy: EndpointRoadSnappingPolicy,
        cleanup_policy: RoadGraphCleanupPolicy,
    ) -> RoadGraph | None:
        if not roads:
            return None
        snapped = EndpointRoadSnapper(
            working_srid=working_srid,
            policy=endpoint_policy,
        ).snap(roads)
        noded = SemanticNoder(working_srid=working_srid).node(snapped.roads)
        graph_inputs = tuple(
            RoadGraphInput(road=road, state=states[road.road_id])
            for road in noded.roads
        )
        graph = RoadGraphBuilder(working_srid=working_srid).build(graph_inputs)
        return RoadGraphCleaner(cleanup_policy).cleanup(graph).graph


def _generated_semantic_roads(
    *,
    baseline: AnchorConnectivityResult,
    growth: RuleBasedRoadGrowthResult,
) -> tuple[
    list[SemanticRoad],
    dict[str, RoadClassificationOrigin],
    dict[str, RoadGrowthIntent],
]:
    roads: list[SemanticRoad] = []
    origins: dict[str, RoadClassificationOrigin] = {}
    intents: dict[str, RoadGrowthIntent] = {}

    for edge in baseline.edges:
        if not edge.connected:
            continue
        assert edge.connection.geometry is not None
        road_id = f"generated:baseline:{edge.edge_index:06d}"
        roads.append(
            SemanticRoad(road_id=road_id, geometry=edge.connection.geometry)
        )
        origins[road_id] = RoadClassificationOrigin.BASELINE_GENERATED

    for attempt in growth.added_edges:
        assert attempt.connection.geometry is not None
        road_id = f"generated:growth:{attempt.attempt_index:06d}"
        roads.append(
            SemanticRoad(road_id=road_id, geometry=attempt.connection.geometry)
        )
        origins[road_id] = RoadClassificationOrigin.GROWTH_GENERATED
        intents[road_id] = attempt.intent

    return roads, origins, intents


def _connected_anchor_pairs(
    *,
    baseline: AnchorConnectivityResult,
    growth: RuleBasedRoadGrowthResult,
) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = [
        _canonical_anchor_pair(edge.start_anchor_id, edge.target_anchor_id)
        for edge in baseline.edges
        if edge.connected
    ]
    pairs.extend(
        _canonical_anchor_pair(
            attempt.start_anchor_id,
            attempt.target_anchor_id,
        )
        for attempt in growth.added_edges
    )
    return tuple(sorted(set(pairs)))


def _canonical_anchor_pair(first: str, second: str) -> tuple[str, str]:
    return (first, second) if first <= second else (second, first)


def _classify_final_graph(
    *,
    graph: RoadGraph,
    fixed_by_id: dict[str, FixedRoadStageInput],
    generated_origins: dict[str, RoadClassificationOrigin],
    generated_growth_intents: dict[str, RoadGrowthIntent],
    policy: RoadClassificationPolicy,
) -> RoadClassificationResult:
    lengths: dict[str, list[float]] = {}
    for edge in graph.edges:
        lengths.setdefault(edge.road_id, []).append(edge.length_m)

    subjects: list[RoadClassificationSubject] = []
    for road_id in sorted(lengths):
        if road_id in fixed_by_id:
            subjects.append(
                RoadClassificationSubject(
                    road_id=road_id,
                    origin=RoadClassificationOrigin.EXISTING,
                    existing_class=fixed_by_id[road_id].existing_class,
                    length_m=fsum(lengths[road_id]),
                )
            )
            continue

        origin = generated_origins.get(road_id)
        if origin is None:
            raise ValueError(
                f"generated road {road_id!r} has no classification provenance"
            )
        subjects.append(
            RoadClassificationSubject(
                road_id=road_id,
                origin=origin,
                length_m=fsum(lengths[road_id]),
                growth_intent=generated_growth_intents.get(road_id),
            )
        )

    return RuleBasedRoadClassifier(policy=policy).classify(tuple(subjects))


def _fingerprint(
    *,
    snapshot: TerritorySnapshot,
    context: RunContext,
    config: RoadStageConfig,
    output: RoadStageOutput,
) -> StageFingerprint:
    parts: list[str | bytes] = [
        RoadStage.name,
        RoadStage.version,
        str(snapshot.snapshot_id),
        str(context.seed),
        context.mode.value,
        repr(config),
    ]
    for edge in output.graph.edges:
        parts.extend(
            (
                edge.edge_id,
                edge.road_id,
                edge.geometry.wkb,
                repr(edge.length_m),
                edge.state.origin.value,
                edge.state.ownership.value,
            )
        )
    for item in output.classification.roads:
        parts.extend((item.road_id, item.road_class, item.reason.value))
    parts.extend(
        (
            "validation:passed" if output.validation.passed else "validation:failed",
            repr(output.metrics.total_length_m),
            repr(output.metrics.generated_length_m),
        )
    )
    return build_stage_fingerprint(*parts)
