import numpy as np
import pytest

from core.urban_generator.suitability import (
    SuitabilityConfig,
    SuitabilityConfigError,
    SuitabilityFactor,
    SuitabilityFactorConfig,
    SuitabilityFactorError,
    SuitabilityFactorResult,
    SuitabilityGridSpec,
    SuitabilityNormalization,
    SuitabilityThresholds,
    validate_factor_result,
)


def factor(
    code: str,
    weight: float,
    *,
    normalization: SuitabilityNormalization = SuitabilityNormalization.IDENTITY,
    raw_min: float | None = None,
    raw_max: float | None = None,
) -> SuitabilityFactorConfig:
    return SuitabilityFactorConfig(
        code=code,
        weight=weight,
        normalization=normalization,
        raw_min=raw_min,
        raw_max=raw_max,
    )


def make_grid() -> SuitabilityGridSpec:
    return SuitabilityGridSpec(
        working_srid=32637,
        bounds=(500_000.0, 6_000_000.0, 500_200.0, 6_000_100.0),
        width=4,
        height=2,
    )


def test_config_normalizes_weights_and_has_stable_fingerprint() -> None:
    config = SuitabilityConfig(
        version="v1",
        factors=(
            factor("slope", 2.0),
            factor("road_proximity", 1.0),
            factor("landuse", 1.0),
        ),
        thresholds=SuitabilityThresholds(minimum_score=0.45, preferred_score=0.75),
    )
    same = SuitabilityConfig(
        version="v1",
        factors=(
            factor("slope", 2),
            factor("road_proximity", 1),
            factor("landuse", 1),
        ),
        thresholds=SuitabilityThresholds(minimum_score=0.45, preferred_score=0.75),
    )

    assert config.total_weight == 4.0
    assert config.normalized_weights == (
        ("slope", 0.5),
        ("road_proximity", 0.25),
        ("landuse", 0.25),
    )
    assert config.factor("road_proximity").weight == 1.0
    assert config.fingerprint == same.fingerprint
    assert len(config.fingerprint) == 64


def test_factor_normalization_is_explicit_and_clipped() -> None:
    direct = factor("landuse", 1.0)
    slope = factor(
        "slope",
        1.0,
        normalization=SuitabilityNormalization.INVERTED_MIN_MAX,
        raw_min=0.0,
        raw_max=20.0,
    )
    proximity = factor(
        "road_proximity",
        1.0,
        normalization=SuitabilityNormalization.MIN_MAX,
        raw_min=0.0,
        raw_max=1000.0,
    )

    assert direct.normalize(0.6) == 0.6
    assert slope.normalize(-5.0) == 1.0
    assert slope.normalize(10.0) == 0.5
    assert slope.normalize(30.0) == 0.0
    assert proximity.normalize(500.0) == 0.5
    assert proximity.normalize(2000.0) == 1.0

    with pytest.raises(SuitabilityConfigError, match="inside 0..1"):
        direct.normalize(1.2)


def test_invalid_factor_config_is_rejected() -> None:
    with pytest.raises(SuitabilityConfigError, match="invalid suitability factor code"):
        factor("Road Proximity", 1.0)
    with pytest.raises(SuitabilityConfigError, match="non-negative"):
        factor("slope", -1.0)
    with pytest.raises(SuitabilityConfigError, match="requires raw_min and raw_max"):
        factor(
            "slope",
            1.0,
            normalization=SuitabilityNormalization.MIN_MAX,
        )
    with pytest.raises(SuitabilityConfigError, match="greater than raw_min"):
        factor(
            "slope",
            1.0,
            normalization=SuitabilityNormalization.MIN_MAX,
            raw_min=10.0,
            raw_max=10.0,
        )
    with pytest.raises(SuitabilityConfigError, match="must not define"):
        factor("slope", 1.0, raw_min=0.0, raw_max=1.0)


def test_config_rejects_duplicates_zero_total_weight_and_bad_version() -> None:
    thresholds = SuitabilityThresholds(minimum_score=0.5)

    with pytest.raises(SuitabilityConfigError, match="unique"):
        SuitabilityConfig(
            version="v1",
            factors=(factor("slope", 1.0), factor("slope", 2.0)),
            thresholds=thresholds,
        )
    with pytest.raises(SuitabilityConfigError, match="positive weight"):
        SuitabilityConfig(
            version="v1",
            factors=(factor("slope", 0.0), factor("landuse", 0.0)),
            thresholds=thresholds,
        )
    with pytest.raises(SuitabilityConfigError, match="invalid suitability config version"):
        SuitabilityConfig(
            version="version 1",
            factors=(factor("slope", 1.0),),
            thresholds=thresholds,
        )


def test_score_thresholds_are_bounded_and_ordered() -> None:
    assert SuitabilityThresholds(minimum_score=0.4).preferred_score is None

    with pytest.raises(SuitabilityConfigError, match="inside 0..1"):
        SuitabilityThresholds(minimum_score=-0.1)
    with pytest.raises(SuitabilityConfigError, match="greater than or equal"):
        SuitabilityThresholds(minimum_score=0.7, preferred_score=0.6)


def test_grid_spec_requires_metric_crs_and_has_deterministic_cell_size() -> None:
    grid = make_grid()

    assert grid.shape == (2, 4)
    assert grid.cell_count == 8
    assert grid.cell_width_m == 50.0
    assert grid.cell_height_m == 50.0

    with pytest.raises(ValueError, match="not projected"):
        SuitabilityGridSpec(
            working_srid=4326,
            bounds=(0.0, 0.0, 1.0, 1.0),
            width=10,
            height=10,
        )
    with pytest.raises(SuitabilityFactorError, match="positive width and height"):
        SuitabilityGridSpec(
            working_srid=32637,
            bounds=(0.0, 0.0, 0.0, 1.0),
            width=10,
            height=10,
        )


def test_factor_result_copies_arrays_and_enforces_grid_shape() -> None:
    grid = make_grid()
    values = np.arange(8, dtype=np.float64).reshape(2, 4)
    valid_mask = np.ones((2, 4), dtype=np.bool_)

    result = SuitabilityFactorResult(
        code="slope",
        version="v1",
        grid=grid,
        values=values,
        valid_mask=valid_mask,
        diagnostics=("synthetic factor",),
    )
    values[0, 0] = 999.0
    valid_mask[0, 0] = False

    assert result.values[0, 0] == 0.0
    assert bool(result.valid_mask[0, 0]) is True
    assert result.values.flags.writeable is False
    assert result.valid_mask.flags.writeable is False

    with pytest.raises(SuitabilityFactorError, match="shapes must match"):
        SuitabilityFactorResult(
            code="slope",
            version="v1",
            grid=grid,
            values=np.zeros((1, 4), dtype=np.float64),
            valid_mask=np.ones((1, 4), dtype=np.bool_),
        )


def test_factor_result_rejects_nonfinite_values_only_where_marked_valid() -> None:
    grid = make_grid()
    values = np.zeros(grid.shape, dtype=np.float64)
    values[0, 0] = np.nan
    mask = np.ones(grid.shape, dtype=np.bool_)

    with pytest.raises(SuitabilityFactorError, match="finite values"):
        SuitabilityFactorResult(
            code="slope",
            version="v1",
            grid=grid,
            values=values,
            valid_mask=mask,
        )

    mask[0, 0] = False
    result = SuitabilityFactorResult(
        code="slope",
        version="v1",
        grid=grid,
        values=values,
        valid_mask=mask,
    )
    assert bool(result.valid_mask[0, 0]) is False


class DummyFactor:
    code = "slope"
    version = "v1"

    def evaluate(self, *, grid, snapshot, context):  # noqa: ANN001, ANN201
        del snapshot, context
        return SuitabilityFactorResult(
            code=self.code,
            version=self.version,
            grid=grid,
            values=np.zeros(grid.shape, dtype=np.float64),
            valid_mask=np.ones(grid.shape, dtype=np.bool_),
        )


def test_factor_protocol_and_result_identity_guard() -> None:
    grid = make_grid()
    implementation = DummyFactor()
    assert isinstance(implementation, SuitabilityFactor)

    result = SuitabilityFactorResult(
        code="slope",
        version="v1",
        grid=grid,
        values=np.zeros(grid.shape, dtype=np.float64),
        valid_mask=np.ones(grid.shape, dtype=np.bool_),
    )
    validate_factor_result(factor=implementation, result=result, grid=grid)

    mismatched = SuitabilityFactorResult(
        code="landuse",
        version="v1",
        grid=grid,
        values=np.zeros(grid.shape, dtype=np.float64),
        valid_mask=np.ones(grid.shape, dtype=np.bool_),
    )
    with pytest.raises(SuitabilityFactorError, match="mismatched code"):
        validate_factor_result(factor=implementation, result=mismatched, grid=grid)
