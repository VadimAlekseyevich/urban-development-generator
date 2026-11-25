"""Prepare the shared bounded metric grid from a real fixed boundary."""

import math
from dataclasses import dataclass

from core.urban_generator.domain import (
    RunContext,
    RunMode,
    StageDiagnostic,
    StageFingerprintWriter,
    StageResult,
    TerritorySnapshot,
    require_stage_input,
)
from core.urban_generator.stages.catalog import PREPARE_SNAPSHOT_STAGE
from core.urban_generator.stages.constraints import ConstraintMaskStageInput
from core.urban_generator.suitability import (
    HardExclusionBoundary,
    HardExclusionGeometryLayer,
    SuitabilityGridSpec,
)

MAX_GRID_CELLS = 25_000_000


@dataclass(frozen=True, slots=True)
class PrepareSnapshotStageConfig:
    cell_size_m: float
    max_cells: int = MAX_GRID_CELLS

    def __post_init__(self) -> None:
        size = self.cell_size_m
        if (
            isinstance(size, bool) or not isinstance(size, (int, float))
            or not math.isfinite(size) or not 1 <= size <= 1000
        ):
            raise ValueError("cell_size_m must be finite and in 1..1000 metres")
        if type(self.max_cells) is not int or not 4 <= self.max_cells <= MAX_GRID_CELLS:
            raise ValueError("max_cells must be in 4..25000000")


@dataclass(frozen=True, slots=True)
class PrepareSnapshotStageInput:
    boundary: HardExclusionBoundary
    geometry_layers: tuple[HardExclusionGeometryLayer, ...] = ()


class PrepareSnapshotStage:
    """Resolve one grid for all later stages; never allocate a raster here."""

    name = PREPARE_SNAPSHOT_STAGE
    version = "1.0.0"
    dependencies: tuple[str, ...] = ()

    def validate_input(self, value: object) -> PrepareSnapshotStageInput:
        return require_stage_input(value, PrepareSnapshotStageInput, stage_name=self.name)

    def execute(
        self, *, snapshot: TerritorySnapshot, context: RunContext,
        stage_input: PrepareSnapshotStageInput, config: PrepareSnapshotStageConfig,
    ) -> StageResult[ConstraintMaskStageInput]:
        value = self.validate_input(stage_input)
        if not isinstance(config, PrepareSnapshotStageConfig):
            raise TypeError("prepare_snapshot requires PrepareSnapshotStageConfig")
        if (
            snapshot.snapshot_id != context.run_id
            or snapshot.settings.working_srid != context.working_srid
            or value.boundary.working_srid != context.working_srid
        ):
            raise ValueError("snapshot run or metric CRS mismatch")
        if context.mode is RunMode.FROM_SCRATCH and snapshot.has_fixed_urban_state:
            raise ValueError("FROM_SCRATCH cannot consume fixed urban state")
        if not isinstance(value.geometry_layers, tuple) or any(
            not isinstance(layer, HardExclusionGeometryLayer)
            or layer.working_srid != context.working_srid
            for layer in value.geometry_layers
        ):
            raise ValueError("invalid metric constraint layer")
        codes = tuple(layer.code for layer in value.geometry_layers)
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate constraint layer code")
        x0, y0, x1, y1 = value.boundary.geometry.bounds
        size = float(config.cell_size_m)
        dx, dy = x1 - x0, y1 - y0
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1, dx, dy)) or min(dx, dy) <= 0:
            raise ValueError("invalid boundary bounds")
        width = max(2, math.ceil(dx / size))
        height = max(2, math.ceil(dy / size))
        if width * height > config.max_cells:
            raise ValueError("prepared grid exceeds cell budget")
        grid = SuitabilityGridSpec(
            working_srid=context.working_srid,
            bounds=(x0, y1 - height * size, x0 + width * size, y1),
            width=width, height=height,
        )
        fingerprint = StageFingerprintWriter()
        for part in (
            self.name, self.version, str(snapshot.snapshot_id), context.mode.value,
            str(grid.working_srid), repr(grid.bounds), str(width), str(height),
            repr(size), str(config.max_cells),
        ):
            fingerprint.add_text(part)
        for ref in snapshot.layer_refs:
            fingerprint.add_text(ref.kind.value)
            fingerprint.add_text(ref.source_ref)
        fingerprint.add_bytes(value.boundary.geometry.wkb)
        for layer in value.geometry_layers:
            fingerprint.add_text(layer.code)
            for geometry in layer.geometries:
                fingerprint.add_bytes(geometry.wkb)
        return StageResult(
            output=ConstraintMaskStageInput(
                grid=grid, boundary=value.boundary, geometry_layers=value.geometry_layers,
            ),
            fingerprint=fingerprint.finalize(),
            diagnostics=(StageDiagnostic(
                code="snapshot.prepared",
                message=f"Prepared {width} x {height} grid at {size:g} m resolution",
            ),),
        )
