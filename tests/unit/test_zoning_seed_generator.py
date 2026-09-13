import uuid

import numpy as np
import pytest

from core.urban_generator.domain import CorrelationMetadata, RunContext, RunMode
from core.urban_generator.suitability import SuitabilityGridSpec, WeightedSuitabilityResult
from core.urban_generator.zoning import DeterministicZoningSeedGenerator, ZoningSeedError


def make_context(*, seed: int, working_srid: int = 32637) -> RunContext:
    return RunContext(
        run_id=uuid.uuid4(),
        mode=RunMode.FROM_SCRATCH,
        seed=seed,
        working_srid=working_srid,
        config_refs=(),
        correlation=CorrelationMetadata(correlation_id=f"seed-{seed}"),
    )


def make_result(
    scores: np.ndarray,
    *,
    valid_mask: np.ndarray | None = None,
    hard_mask: np.ndarray | None = None,
    bounds: tuple[float, float, float, float] = (0.0, 0.0, 40.0, 40.0),
    working_srid: int = 32637,
) -> WeightedSuitabilityResult:
    values = np.asarray(scores, dtype=np.float64)
    height, width = values.shape
    valid = (
        np.ones(values.shape, dtype=np.bool_)
        if valid_mask is None
        else np.asarray(valid_mask, dtype=np.bool_)
    )
    hard = (
        np.zeros(values.shape, dtype=np.bool_)
        if hard_mask is None
        else np.asarray(hard_mask, dtype=np.bool_)
    )
    normalized = np.array(values, dtype=np.float64, copy=True)
    normalized[~valid] = 0.0
    return WeightedSuitabilityResult(
        grid=SuitabilityGridSpec(
            working_srid=working_srid,
            bounds=bounds,
            width=width,
            height=height,
        ),
        scores=normalized,
        valid_mask=valid,
        hard_excluded_mask=hard,
        config_version="test-v1",
        config_fingerprint="a" * 64,
        factor_versions=(("synthetic", "1"),),
    )


def test_same_run_seed_and_suitability_produce_same_seed_set() -> None:
    scores = np.arange(1, 26, dtype=np.float64).reshape(5, 5) / 25.0
    result = make_result(scores, bounds=(100.0, 200.0, 600.0, 700.0))
    first_context = make_context(seed=2026)
    second_context = make_context(seed=2026)
    generator = DeterministicZoningSeedGenerator()

    first = generator.generate(suitability=result, context=first_context, count=7)
    _ = first_context.rng("roads").random(20)
    second = generator.generate(suitability=result, context=second_context, count=7)
    repeated = generator.generate(suitability=result, context=first_context, count=7)

    assert first == second == repeated
    assert first.rng_namespace == "zoning.seeds.v1"
    assert first.rng_seed == first_context.rng_seed("zoning.seeds.v1")
    cells = [(seed.row, seed.col) for seed in first.seeds]
    assert cells == sorted(cells)
    assert len(cells) == len(set(cells)) == 7


def test_different_run_seed_changes_weighted_selection() -> None:
    result = make_result(np.ones((10, 10), dtype=np.float64))
    generator = DeterministicZoningSeedGenerator()

    first = generator.generate(suitability=result, context=make_context(seed=1), count=8)
    second = generator.generate(suitability=result, context=make_context(seed=2), count=8)

    assert first.seeds != second.seeds


def test_positive_suitability_is_preferred_before_zero_score_fallback() -> None:
    scores = np.array(
        [
            [0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ],
        dtype=np.float64,
    )
    result = make_result(scores, bounds=(0.0, 0.0, 30.0, 20.0))
    generator = DeterministicZoningSeedGenerator()

    one = generator.generate(suitability=result, context=make_context(seed=7), count=1)
    three = generator.generate(suitability=result, context=make_context(seed=7), count=3)

    assert [(seed.row, seed.col) for seed in one.seeds] == [(1, 1)]
    assert one.weighted_seed_count == 1
    assert one.uniform_fallback_count == 0
    assert (1, 1) in {(seed.row, seed.col) for seed in three.seeds}
    assert three.weighted_seed_count == 1
    assert three.uniform_fallback_count == 2


def test_invalid_and_hard_excluded_cells_are_never_selected() -> None:
    scores = np.array([[0.9, 0.0], [0.8, 0.7]], dtype=np.float64)
    valid = np.array([[True, False], [False, True]], dtype=np.bool_)
    hard = np.array([[False, True], [False, False]], dtype=np.bool_)
    result = make_result(scores, valid_mask=valid, hard_mask=hard)

    seeds = DeterministicZoningSeedGenerator().generate(
        suitability=result,
        context=make_context(seed=11),
        count=2,
    )

    assert {(seed.row, seed.col) for seed in seeds.seeds} == {(0, 0), (1, 1)}
    assert all(seed.suitability_score > 0.0 for seed in seeds.seeds)


def test_seed_coordinates_are_raster_cell_centers_in_metric_crs() -> None:
    scores = np.array([[0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]])
    result = make_result(scores, bounds=(0.0, 0.0, 40.0, 20.0))

    seed_set = DeterministicZoningSeedGenerator().generate(
        suitability=result,
        context=make_context(seed=3),
        count=1,
    )
    seed = seed_set.seeds[0]

    assert (seed.row, seed.col) == (1, 2)
    assert seed.x_m == pytest.approx(25.0)
    assert seed.y_m == pytest.approx(5.0)
    assert seed.suitability_score == pytest.approx(1.0)


def test_generator_rejects_too_many_seeds_or_crs_mismatch() -> None:
    result = make_result(np.ones((2, 2), dtype=np.float64))
    generator = DeterministicZoningSeedGenerator()

    with pytest.raises(ZoningSeedError, match="only 4 valid cells"):
        generator.generate(suitability=result, context=make_context(seed=1), count=5)

    with pytest.raises(ZoningSeedError, match="working_srid"):
        generator.generate(
            suitability=result,
            context=make_context(seed=1, working_srid=3857),
            count=1,
        )


def test_generator_rejects_non_positive_count() -> None:
    result = make_result(np.ones((2, 2), dtype=np.float64))

    with pytest.raises(ZoningSeedError, match="positive integer"):
        DeterministicZoningSeedGenerator().generate(
            suitability=result,
            context=make_context(seed=1),
            count=0,
        )
