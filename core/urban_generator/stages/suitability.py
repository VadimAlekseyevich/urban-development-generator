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
    SUITABILITY_STAGE,
)
from core.urban_generator.suitability import (
    HardExclusionMask,
    SuitabilityConfig,
    SuitabilityFactor,
    SuitabilityGridSpec,
    WeightedSuitabilityResult,
    aggregate_weighted_suitability,
    validate_factor_result,
)


@dataclass(frozen=True, slots=True)
class SuitabilityStageInput:
    grid: SuitabilityGridSpec
    hard_mask: HardExclusionMask


@dataclass(frozen=True, slots=True)
class SuitabilityStageConfig:
    suitability: SuitabilityConfig
    max_cells: int = 25_000_000


class SuitabilityStage:
    """Compose existing suitability factors into the canonical weighted result."""

    name = SUITABILITY_STAGE
    version = "1.0.0"
    dependencies = (EVALUATE_CONSTRAINTS_STAGE,)

    def __init__(self, *, factors: tuple[SuitabilityFactor, ...]) -> None:
        if not isinstance(factors, tuple):
            raise TypeError("suitability factors must be an immutable tuple")
        factor_codes = tuple(factor.code for factor in factors)
        if len(factor_codes) != len(set(factor_codes)):
            raise ValueError("suitability factor codes must be unique")
        self._factors_by_code = {factor.code: factor for factor in factors}

    def validate_input(self, value: object) -> SuitabilityStageInput:
        return require_stage_input(value, SuitabilityStageInput, stage_name=self.name)

    def execute(
        self,
        *,
        snapshot: TerritorySnapshot,
        context: RunContext,
        stage_input: SuitabilityStageInput,
        config: SuitabilityStageConfig,
    ) -> StageResult[WeightedSuitabilityResult]:
        value = self.validate_input(stage_input)
        if not isinstance(config, SuitabilityStageConfig):
            raise TypeError("suitability config must be SuitabilityStageConfig")
        if value.hard_mask.grid != value.grid:
            raise ValueError("suitability hard mask must use exactly the target grid")
        if value.grid.working_srid != snapshot.settings.working_srid:
            raise ValueError("suitability grid working_srid must match snapshot")
        if value.grid.working_srid != context.working_srid:
            raise ValueError("suitability grid working_srid must match run context")

        factor_results = []
        fingerprint_parts: list[str | bytes] = [
            self.name,
            self.version,
            str(snapshot.snapshot_id),
            str(context.seed),
            config.suitability.fingerprint,
            value.hard_mask.excluded.tobytes(order="C"),
        ]
        for factor_config in config.suitability.factors:
            if factor_config.weight <= 0.0:
                continue
            factor = self._factors_by_code.get(factor_config.code)
            if factor is None:
                raise ValueError(
                    f"missing suitability factor implementation: {factor_config.code!r}"
                )
            result = factor.evaluate(
                grid=value.grid,
                snapshot=snapshot,
                context=context,
            )
            validate_factor_result(factor=factor, result=result, grid=value.grid)
            factor_results.append(result)
            fingerprint_parts.extend(
                (
                    result.code,
                    result.version,
                    result.values.tobytes(order="C"),
                    result.valid_mask.tobytes(order="C"),
                )
            )

        weighted = aggregate_weighted_suitability(
            grid=value.grid,
            config=config.suitability,
            hard_mask=value.hard_mask,
            factor_results=tuple(factor_results),
            max_cells=config.max_cells,
        )
        fingerprint_parts.extend(
            (
                weighted.scores.tobytes(order="C"),
                weighted.valid_mask.tobytes(order="C"),
            )
        )
        return StageResult(
            output=weighted,
            fingerprint=build_stage_fingerprint(*fingerprint_parts),
            diagnostics=(
                StageDiagnostic(
                    code="suitability.completed",
                    message=(
                        f"suitability completed: {weighted.valid_count} valid, "
                        f"{weighted.hard_excluded_count} hard-excluded, "
                        f"{weighted.invalid_data_count} invalid-data cells"
                    ),
                ),
            ),
        )
