"""Compose actual snapshot, hard-mask and DEM suitability inputs for canonical Stages.

This is a partial input resolver, deliberately not a deployable runtime factory.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict

from backend.app.adapters.dem_source_reader import SqlAlchemyDEMSourceReader
from backend.app.adapters.hard_exclusion_source_reader import SqlAlchemyHardExclusionSourceReader
from backend.app.application.generation import GenerationExecutionError, StageInvocation
from core.urban_generator.domain import PipelineContext, RunContext, TerritorySnapshot
from core.urban_generator.stages import (
    ConstraintMaskStage,
    ConstraintMaskStageConfig,
    ConstraintMaskStageInput,
    PrepareSnapshotStage,
    PrepareSnapshotStageConfig,
    PrepareSnapshotStageInput,
    SuitabilityStage,
    SuitabilityStageConfig,
    SuitabilityStageInput,
)
from core.urban_generator.stages.registry import StageAny
from core.urban_generator.suitability import (
    DEMSlopeFactor,
    HardExclusionMask,
    SuitabilityFactorResult,
    SuitabilityGridSpec,
)


class RunScopedDEMSlopeFactor:
    """Use the selected run's actual DEM, closing scratch data after each evaluation."""

    code = "slope"
    version = "v1"

    def __init__(self, reader: SqlAlchemyDEMSourceReader) -> None:
        self._reader = reader

    def evaluate(
        self, *, grid: SuitabilityGridSpec, snapshot: TerritorySnapshot, context: RunContext,
    ) -> SuitabilityFactorResult:
        with self._reader.open(snapshot=snapshot, grid=grid) as source:
            return DEMSlopeFactor(source=source).evaluate(
                grid=grid, snapshot=snapshot, context=context,
            )


class EarlyGenerationInputResolver:
    """Use the existing job-service input contract for the first three real stages."""

    def __init__(self, source_reader: SqlAlchemyHardExclusionSourceReader) -> None:
        self._sources = source_reader

    def resolve(
        self, *, stage: StageAny, context: PipelineContext, config: object,
        outputs: Mapping[str, object],
    ) -> StageInvocation:
        if stage.name == PrepareSnapshotStage.name and isinstance(
            config, PrepareSnapshotStageConfig,
        ):
            sources = self._sources.read(snapshot=context.snapshot)
            parts = (
                str(context.run.run_id),
                context.run.mode.value,
                *(ref.source_ref for ref in context.snapshot.layer_refs),
            )
            return StageInvocation(
                stage_input=PrepareSnapshotStageInput(
                    boundary=sources.boundary, geometry_layers=sources.geometry_layers,
                ),
                input_parts=parts,
                config_parts=(json.dumps(asdict(config), sort_keys=True, allow_nan=False),),
            )
        if stage.name == ConstraintMaskStage.name and isinstance(config, ConstraintMaskStageConfig):
            prepared = outputs.get(PrepareSnapshotStage.name)
            if not isinstance(prepared, ConstraintMaskStageInput):
                raise GenerationExecutionError("missing prepared snapshot output")
            if prepared.grid.cell_count > config.max_cells:
                raise GenerationExecutionError("constraint stage grid budget exceeded")
            return StageInvocation(
                stage_input=prepared, input_parts=(),
                config_parts=(json.dumps(asdict(config), sort_keys=True, allow_nan=False),),
            )
        if stage.name == SuitabilityStage.name and isinstance(config, SuitabilityStageConfig):
            mask = outputs.get(ConstraintMaskStage.name)
            if not isinstance(mask, HardExclusionMask):
                raise GenerationExecutionError("missing hard exclusion mask")
            return StageInvocation(
                stage_input=SuitabilityStageInput(grid=mask.grid, hard_mask=mask),
                input_parts=tuple(
                    ref.source_ref for ref in (
                        *context.snapshot.dem, *context.snapshot.landuse, *context.snapshot.roads,
                    )
                ),
                config_parts=(json.dumps(asdict(config), sort_keys=True, allow_nan=False),),
            )
        raise GenerationExecutionError(f"unsupported early stage or config: {stage.name}")
