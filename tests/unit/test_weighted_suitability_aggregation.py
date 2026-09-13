from __future__ import annotations

import numpy as np
import pytest

from core.urban_generator.suitability.aggregation import (
    WeightedSuitabilityError,
    WeightedSuitabilityResult,
    aggregate_weighted_suitability,
)
from core.urban_generator.suitability.config import (
    SuitabilityConfig,
    SuitabilityFactorConfig,
    SuitabilityNormalization,
    SuitabilityThresholds,
)
from core.urban_generator.suitability.factors import (
    SuitabilityFactorResult,
    SuitabilityGridSpec,
)
from core.urban_generator.suitability.hard_exclusion import HardExclusionMask


def _grid(*, width: int = 2, height: int = 2) -> SuitabilityGridSpec:
    return SuitabilityGridSpec(
        working_srid=32637,
        bounds=(0.0, 0.0, float(width * 10), float(height * 10)),
        width=width,
        height=height,
    )


def _config() -> SuitabilityConfig:
    return SuitabilityConfig(
        version="v1",
        factors=(
            SuitabilityFactorConfig(
                code="slope",
                weight=2.0,
                normalization=SuitabilityNormalization.INVERTED_MIN_MAX,
                raw_min=0.0,
                raw_max=20.0,
            ),
            SuitabilityFactorConfig(
                code="road_proximity",
                weight=1.0,
                normalization=SuitabilityNormalization.INVERTED_MIN_MAX,
                raw_min=0.0,
                raw_max=1000.0,
            ),
            SuitabilityFactorConfig(
                code="landuse",
                weight=1.0,
                normalization=SuitabilityNormalization.IDENTITY,
            ),
        ),
        thresholds=SuitabilityThresholds(minimum_score=0.45, preferred_score=0.75),
    )


def _result(
    *,
    grid: SuitabilityGridSpec,
    code: str,
    values: list[list[float]],
    valid_mask: list[list[bool]] | None = None,
    version: str = "v1",
) -> SuitabilityFactorResult:
    if valid_mask is None:
        valid_mask = [[True for _ in range(grid.width)] for _ in range(grid.height)]
    return SuitabilityFactorResult(
        code=code,
        version=version,
        grid=grid,
        values=np.asarray(values, dtype=np.float64),
        valid_mask=np.asarray(valid_mask, dtype=np.bool_),
    )


def _hard_mask(
    *,
    grid: SuitabilityGridSpec,
    excluded: list[list[bool]] | None = None,
) -> HardExclusionMask:
    if excluded is None:
        excluded = [[False for _ in range(grid.width)] for _ in range(grid.height)]
    return HardExclusionMask(
        grid=grid,
        excluded=np.asarray(excluded, dtype=np.bool_),
        source_codes=("boundary",),
    )


def test_aggregates_explicit_normalization_and_configured_weights() -> None:
    grid = _grid()
    result = aggregate_weighted_suitability(
        grid=grid,
        config=_config(),
        hard_mask=_hard_mask(grid=grid),
        factor_results=(
            _result(grid=grid, code="slope", values=[[0.0, 10.0], [20.0, 5.0]]),
            _result(
                grid=grid,
                code="road_proximity",
                values=[[0.0, 500.0], [1000.0, 250.0]],
            ),
            _result(grid=grid, code="landuse", values=[[1.0, 0.5], [0.0, 0.25]]),
        ),
    )

    np.testing.assert_allclose(result.scores, [[1.0, 0.5], [0.0, 0.625]])
    assert np.all(result.valid_mask)
    assert result.factor_versions == (
        ("slope", "v1"),
        ("road_proximity", "v1"),
        ("landuse", "v1"),
    )
    assert result.config_version == "v1"
    assert result.config_fingerprint == _config().fingerprint


def test_hard_mask_has_precedence_over_soft_score() -> None:
    grid = _grid()
    result = aggregate_weighted_suitability(
        grid=grid,
        config=_config(),
        hard_mask=_hard_mask(grid=grid, excluded=[[False, True], [False, False]]),
        factor_results=(
            _result(grid=grid, code="slope", values=[[0.0, 0.0], [0.0, 0.0]]),
            _result(
                grid=grid,
                code="road_proximity",
                values=[[0.0, 0.0], [0.0, 0.0]],
            ),
            _result(grid=grid, code="landuse", values=[[1.0, 1.0], [1.0, 1.0]]),
        ),
    )

    assert result.scores[0, 1] == 0.0
    assert not result.valid_mask[0, 1]
    assert result.hard_excluded_mask[0, 1]
    assert result.hard_excluded_count == 1
    assert result.valid_count == 3


def test_missing_factor_data_invalidates_cell_without_local_reweighting() -> None:
    grid = _grid()
    landuse = _result(
        grid=grid,
        code="landuse",
        values=[[1.0, np.nan], [1.0, 1.0]],
        valid_mask=[[True, False], [True, True]],
    )
    result = aggregate_weighted_suitability(
        grid=grid,
        config=_config(),
        hard_mask=_hard_mask(grid=grid),
        factor_results=(
            _result(grid=grid, code="slope", values=[[0.0, 0.0], [0.0, 0.0]]),
            _result(
                grid=grid,
                code="road_proximity",
                values=[[0.0, 0.0], [0.0, 0.0]],
            ),
            landuse,
        ),
    )

    assert not result.valid_mask[0, 1]
    assert result.scores[0, 1] == 0.0
    assert result.invalid_data_count == 1


def test_raw_values_are_clipped_before_weighting() -> None:
    grid = _grid(width=2, height=1)
    config = SuitabilityConfig(
        version="v1",
        factors=(
            SuitabilityFactorConfig(
                code="distance",
                weight=1.0,
                normalization=SuitabilityNormalization.MIN_MAX,
                raw_min=10.0,
                raw_max=20.0,
            ),
        ),
        thresholds=SuitabilityThresholds(minimum_score=0.5),
    )
    result = aggregate_weighted_suitability(
        grid=grid,
        config=config,
        hard_mask=_hard_mask(grid=grid),
        factor_results=(
            _result(grid=grid, code="distance", values=[[0.0, 30.0]]),
        ),
    )

    np.testing.assert_array_equal(result.scores, [[0.0, 1.0]])


def test_identity_rejects_valid_values_outside_unit_interval() -> None:
    grid = _grid(width=1, height=1)
    config = SuitabilityConfig(
        version="v1",
        factors=(
            SuitabilityFactorConfig(
                code="landuse",
                weight=1.0,
                normalization=SuitabilityNormalization.IDENTITY,
            ),
        ),
        thresholds=SuitabilityThresholds(minimum_score=0.5),
    )

    with pytest.raises(WeightedSuitabilityError, match="outside 0..1"):
        aggregate_weighted_suitability(
            grid=grid,
            config=config,
            hard_mask=_hard_mask(grid=grid),
            factor_results=(
                _result(grid=grid, code="landuse", values=[[1.1]]),
            ),
        )


def test_positive_weight_factor_is_required_but_zero_weight_factor_may_be_omitted() -> None:
    grid = _grid(width=1, height=1)
    config = SuitabilityConfig(
        version="v1",
        factors=(
            SuitabilityFactorConfig(
                code="required",
                weight=1.0,
                normalization=SuitabilityNormalization.IDENTITY,
            ),
            SuitabilityFactorConfig(
                code="disabled",
                weight=0.0,
                normalization=SuitabilityNormalization.IDENTITY,
            ),
        ),
        thresholds=SuitabilityThresholds(minimum_score=0.5),
    )

    result = aggregate_weighted_suitability(
        grid=grid,
        config=config,
        hard_mask=_hard_mask(grid=grid),
        factor_results=(
            _result(grid=grid, code="required", values=[[0.75]]),
        ),
    )
    assert result.scores[0, 0] == 0.75

    with pytest.raises(WeightedSuitabilityError, match="missing positive-weight"):
        aggregate_weighted_suitability(
            grid=grid,
            config=config,
            hard_mask=_hard_mask(grid=grid),
            factor_results=(),
        )


def test_rejects_duplicate_unconfigured_or_wrong_grid_results() -> None:
    grid = _grid(width=1, height=1)
    factor = _result(grid=grid, code="slope", values=[[5.0]])
    with pytest.raises(WeightedSuitabilityError, match="duplicate factor result"):
        aggregate_weighted_suitability(
            grid=grid,
            config=_config(),
            hard_mask=_hard_mask(grid=grid),
            factor_results=(factor, factor),
        )

    extra = _result(grid=grid, code="extra", values=[[0.5]])
    with pytest.raises(WeightedSuitabilityError, match="not present"):
        aggregate_weighted_suitability(
            grid=grid,
            config=_config(),
            hard_mask=_hard_mask(grid=grid),
            factor_results=(extra,),
        )

    other_grid = _grid(width=2, height=1)
    wrong_grid = _result(grid=other_grid, code="slope", values=[[5.0, 5.0]])
    with pytest.raises(WeightedSuitabilityError, match="target grid"):
        aggregate_weighted_suitability(
            grid=grid,
            config=_config(),
            hard_mask=_hard_mask(grid=grid),
            factor_results=(wrong_grid,),
        )


def test_grid_limit_is_checked_before_aggregation() -> None:
    grid = _grid()
    with pytest.raises(WeightedSuitabilityError, match="cell limit exceeded"):
        aggregate_weighted_suitability(
            grid=grid,
            config=_config(),
            hard_mask=_hard_mask(grid=grid),
            factor_results=(),
            max_cells=3,
        )


def test_weighted_result_copies_arrays_and_makes_them_read_only() -> None:
    grid = _grid(width=1, height=1)
    scores = np.asarray([[0.5]], dtype=np.float64)
    valid = np.asarray([[True]], dtype=np.bool_)
    hard = np.asarray([[False]], dtype=np.bool_)
    result = WeightedSuitabilityResult(
        grid=grid,
        scores=scores,
        valid_mask=valid,
        hard_excluded_mask=hard,
        config_version="v1",
        config_fingerprint="a" * 64,
        factor_versions=(("factor", "v1"),),
    )

    scores[0, 0] = 0.1
    valid[0, 0] = False
    hard[0, 0] = True
    assert result.scores[0, 0] == 0.5
    assert result.valid_mask[0, 0]
    assert not result.hard_excluded_mask[0, 0]
    assert not result.scores.flags.writeable
    assert not result.valid_mask.flags.writeable
    assert not result.hard_excluded_mask.flags.writeable
