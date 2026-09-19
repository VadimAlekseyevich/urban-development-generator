import uuid

import numpy as np
from shapely.geometry import box

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
    Stage,
    TerritorySnapshot,
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
        assert snapshot.settings.working_srid == grid.working_srid
        assert context.working_srid == grid.working_srid
        return SuitabilityFactorResult(
            code=self.code,
            version=self.version,
            grid=grid,
            values=np.ones(grid.shape, dtype=np.float64),
            valid_mask=np.ones(grid.shape, dtype=np.bool_),
        )


def make_snapshot() -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        project=ProjectRef(
            project_id=uuid.UUID("00000000-0000-0000-0000-000000000201")
        ),
        settings=ProjectSettings(working_srid=32637),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
        fixed_zones=(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.ZONES,
                source_ref="synthetic:fixed-zones:v1",
            ),
        ),
    )


def make_context() -> RunContext:
    return RunContext(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000301"),
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=32637,
        config_refs=(ConfigRef(name="generation", ref="synthetic:generation:v1"),),
        correlation=CorrelationMetadata(correlation_id="stage-spine-test"),
    )


def make_grid() -> SuitabilityGridSpec:
    return SuitabilityGridSpec(
        working_srid=32637,
        bounds=(0.0, 0.0, 4.0, 4.0),
        width=4,
        height=4,
    )


def make_suitability_config() -> SuitabilityConfig:
    return SuitabilityConfig(
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


def make_zoning_config() -> ZoningConfig:
    return ZoningConfig(
        version="1",
        zones=tuple(
            ZoneClassConfig(
                zone_class=zone_class,
                target_share=0.25,
                minimum_area_m2=0.1,
            )
            for zone_class in ZoneClass
        ),
    )


def test_constraints_suitability_zoning_form_typed_deterministic_spine() -> None:
    snapshot = make_snapshot()
    context = make_context()
    grid = make_grid()

    constraints_stage = ConstraintMaskStage()
    constraints_result = constraints_stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=ConstraintMaskStageInput(
            grid=grid,
            boundary=HardExclusionBoundary(
                geometry=box(*grid.bounds),
                working_srid=grid.working_srid,
            ),
        ),
        config=ConstraintMaskStageConfig(),
    )

    suitability_stage = SuitabilityStage(factors=(ConstantFactor(),))
    suitability_config = SuitabilityStageConfig(
        suitability=make_suitability_config(),
    )
    suitability_result = suitability_stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=SuitabilityStageInput(
            grid=grid,
            hard_mask=constraints_result.output,
        ),
        config=suitability_config,
    )

    zoning_stage = ZoningStage(
        constraint_engine=RegisteredConstraintEngine(ConstraintRegistry())
    )
    zoning_config = ZoningStageConfig(
        zoning=make_zoning_config(),
        seed_count=4,
        max_refinement_iterations=20,
    )
    zoning_result = zoning_stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=ZoningStageInput(
            suitability=suitability_result.output,
            developable_area=box(*grid.bounds),
        ),
        config=zoning_config,
    )

    assert isinstance(constraints_stage, Stage)
    assert isinstance(suitability_stage, Stage)
    assert isinstance(zoning_stage, Stage)
    assert constraints_stage.dependencies == ("prepare_snapshot",)
    assert suitability_stage.dependencies == ("evaluate_constraints",)
    assert zoning_stage.dependencies == ("suitability",)

    assert constraints_result.output.excluded_count == 0
    assert suitability_result.output.valid_count == grid.cell_count
    assert zoning_result.output.partition.coverage_ratio == 1.0
    assert zoning_result.output.constraints.invalid_zone_count == 0
    assert zoning_result.output.fixed_zone_refs == snapshot.fixed_zones

    repeated_suitability = suitability_stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=SuitabilityStageInput(
            grid=grid,
            hard_mask=constraints_result.output,
        ),
        config=suitability_config,
    )
    repeated_zoning = zoning_stage.execute(
        snapshot=snapshot,
        context=context,
        stage_input=ZoningStageInput(
            suitability=repeated_suitability.output,
            developable_area=box(*grid.bounds),
        ),
        config=zoning_config,
    )

    assert repeated_suitability.fingerprint == suitability_result.fingerprint
    assert repeated_zoning.fingerprint == zoning_result.fingerprint
    assert tuple(
        item.zone_class for item in repeated_zoning.output.assignment.assignments
    ) == tuple(item.zone_class for item in zoning_result.output.assignment.assignments)
