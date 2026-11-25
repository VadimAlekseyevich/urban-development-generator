"""A positive population target must not yield a silently empty city."""

from core.urban_generator.domain import ConstraintSeverity
from core.urban_generator.stages.final_evaluation import validate_positive_population


def test_zero_population_target_remains_allowed() -> None:
    assert validate_positive_population(target=0, allocated=0) is None


def test_empty_positive_population_is_hard_failure() -> None:
    result = validate_positive_population(target=8, allocated=0)
    assert result is not None
    assert not result.passed
    assert result.severity is ConstraintSeverity.HARD
    assert result.code == "demography.positive_population"


def test_nonzero_partial_population_is_not_a_hard_failure() -> None:
    result = validate_positive_population(target=8, allocated=3)
    assert result is not None
    assert result.passed
