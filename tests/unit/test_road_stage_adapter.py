import uuid

import numpy as np
from shapely.geometry import LineString, box

from core.urban_generator.constraints import ConstraintRegistry, RegisteredConstraintEngine
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
    ConstraintMaskStage,
    ConstraintMaskStageConfig,
    ConstraintMaskStageInput,
    SuitabilityStage,
    SuitabilityStageConfig,
    SuitabilityStageInput,
    ZoningStage,
    ZoningStageConfig,
    ZoningStageInput,
)
from core.urban_generator.stages.roads import (
    FixedRoadStageInput,
    RoadStage,
    RoadStageConfig,
    RoadStageInput,
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


def _snapshot(mode: RunMode) -> TerritorySnapshot:
    roads = (
        (
            SnapshotLayerRef(
                kind=SnapshotLayerKind.ROADS,
                source_ref="synthetic:roads:v1",
            ),
        )
        if mode is RunMode.EXPANSION
        else ()
    )
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        project=ProjectRef(
            project_id=uuid.UUID("00000000-0000-0000-0000-000000000201")
        ),
        settings=ProjectSettings(working_srid=WORKING_SRID),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
        roads=roads,
    )


def _context(mode: RunMode) -> RunContext:
    return RunContext(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000301"),
        mode=mode,
        seed=2026,
        working_srid=WORKING_SRID,
        config_refs=(ConfigRef(name="generation", ref="synthetic:generation:v1"),),
        correlation=CorrelationMetadata(correlation_id="roads-stage-test"),
    )


def _upstream(snapshot: TerritorySnapshot, context: RunContext):
    grid = SuitabilityGridSpec(
        working_srid=WORKING_SRID,
        bounds=(0.0, 0.0, 6.0, 6.0),
        width=6,
        height=6,
    )
    constraints = ConstraintMaskStage().execute(
        snapshot=snapshot,
        context=context,
        stage_input=ConstraintMaskStageInput(
            grid=grid,
            boundary=HardExclusionBoundary(
                geometry=box(*grid.bounds),
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
                version="1",
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
            developable_area=box(*grid.bounds),
        ),
        config=ZoningStageConfig(
            zoning=ZoningConfig(
                version="1",
                zones=tuple(
                    ZoneClassConfig(
                        zone_class=zone_class,
                        target_share=0.25,
                        minimum_area_m2=0.1,
                    )
                    for zone_class in ZoneClass
                ),
            ),
            seed_count=4,
            max_refinement_iterations=20,
        ),
    )
    return constraints.output, suitability.output, zoning.output


def _config(*, expansion: bool) -> RoadStageConfig:
    return RoadStageConfig(
        endpoint_snapping=EndpointRoadSnappingPolicy(tolerance_m=0.05),
        anchor_policy=CandidateRoadAnchorPolicy(
            max_candidates=5,
            max_sampled_cells=36,
        ),
        least_cost_policy=LeastCostConnectorPolicy(max_visited_cells=5_000),
        validation_policy=RoadValidationPolicy(
            max_component_count=1,
            max_dead_end_ratio=1.0,
        ),
        fixed_attachment_policy=(
            FixedNetworkAttachmentPolicy(max_distance_m=20.0)
            if expansion
            else None
        ),
    )


def test_from_scratch_road_stage_builds_generated_graph_without_fixed_state() -> None:
    snapshot = _snapshot(RunMode.FROM_SCRATCH)
    context = _context(RunMode.FROM_SCRATCH)
    hard_mask, suitability, zoning = _upstream(snapshot, context)

    result = RoadStage().execute(
        snapshot=snapshot,
        context=context,
        stage_input=RoadStageInput(
            zoning=zoning,
            suitability=suitability,
            hard_mask=hard_mask,
        ),
        config=_config(expansion=False),
    )

    assert result.output.graph.edges
    assert all(not edge.is_fixed for edge in result.output.graph.edges)
    assert result.output.fixed_attachment is None
    assert result.output.metrics.generated_length_m > 0.0

    repeated = RoadStage().execute(
        snapshot=snapshot,
        context=context,
        stage_input=RoadStageInput(
            zoning=zoning,
            suitability=suitability,
            hard_mask=hard_mask,
        ),
        config=_config(expansion=False),
    )
    assert repeated.fingerprint == result.fingerprint


def test_expansion_road_stage_preserves_fixed_roads_and_attaches_generated_components() -> None:
    snapshot = _snapshot(RunMode.EXPANSION)
    context = _context(RunMode.EXPANSION)
    hard_mask, suitability, zoning = _upstream(snapshot, context)

    result = RoadStage().execute(
        snapshot=snapshot,
        context=context,
        stage_input=RoadStageInput(
            zoning=zoning,
            suitability=suitability,
            hard_mask=hard_mask,
            fixed_roads=(
                FixedRoadStageInput(
                    road=SemanticRoad(
                        road_id="fixed:main",
                        geometry=LineString(((0.0, 0.0), (6.0, 0.0))),
                    ),
                    existing_class="primary",
                ),
            ),
        ),
        config=_config(expansion=True),
    )

    assert result.output.fixed_attachment is not None
    assert result.output.fixed_attachment.complete is True
    assert any(edge.is_fixed for edge in result.output.graph.edges)
    assert any(not edge.is_fixed for edge in result.output.graph.edges)
    assert result.output.validation.diagnostics.component_count == 1
    classified = {item.road_id: item for item in result.output.classification.roads}
    assert classified["fixed:main"].road_class == "primary"
