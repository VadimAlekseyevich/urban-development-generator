import pytest

from core.urban_generator.domain.benchmarking import RawMetricId
from core.urban_generator.metrics.score import (
    CompositeScoreConfig,
    CompositeScoreMetricWeight,
)
from core.urban_generator.metrics.sensitivity import (
    ScoreSensitivityError,
    ScoreSensitivityMetricSnapshot,
    ScoreSensitivityRunSnapshot,
    ScoreSensitivityWeightFactor,
    ScoreWeightPerturbation,
    analyze_score_sensitivity,
    build_perturbed_score_config,
)

COVERAGE = RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO
CIRCUITY = RawMetricId.ROADS_CIRCUITY


def _metric(
    metric_id: RawMetricId,
    normalized: float | None,
    *,
    raw: float | None = 1.0,
    policy_version: str = "policy-v1",
) -> ScoreSensitivityMetricSnapshot:
    return ScoreSensitivityMetricSnapshot(
        metric_id=metric_id,
        raw_value=raw,
        normalized_value=normalized,
        normalization_policy_version=policy_version,
        was_clamped=False,
        was_missing=raw is None,
    )


def _run(
    run_ref: str,
    *,
    coverage: float,
    circuity: float | None,
    profile_version: str = "1",
    circuity_policy_version: str = "circuity-v1",
) -> ScoreSensitivityRunSnapshot:
    return ScoreSensitivityRunSnapshot(
        run_ref=run_ref,
        normalization_profile_id="evaluation-default",
        normalization_profile_version=profile_version,
        metrics=(
            _metric(COVERAGE, coverage, policy_version="coverage-v1"),
            _metric(
                CIRCUITY,
                circuity,
                raw=None if circuity is None else 1.2,
                policy_version=circuity_policy_version,
            ),
        ),
    )


def _config(
    *,
    coverage_weight: float = 1.0,
    circuity_weight: float = 1.0,
) -> CompositeScoreConfig:
    return CompositeScoreConfig(
        config_id="score-default",
        version="1",
        weights=(
            CompositeScoreMetricWeight(COVERAGE, coverage_weight),
            CompositeScoreMetricWeight(CIRCUITY, circuity_weight),
        ),
    )


def _perturbation(
    perturbation_id: str,
    *,
    coverage_factor: float,
    circuity_factor: float,
) -> ScoreWeightPerturbation:
    return ScoreWeightPerturbation(
        perturbation_id=perturbation_id,
        version="1",
        factors=(
            ScoreSensitivityWeightFactor(COVERAGE, coverage_factor),
            ScoreSensitivityWeightFactor(CIRCUITY, circuity_factor),
        ),
    )


def test_weight_perturbation_recalculates_ranking_from_persisted_values() -> None:
    result = analyze_score_sensitivity(
        (
            _run("run-a", coverage=0.9, circuity=0.5),
            _run("run-b", coverage=0.5, circuity=0.8),
        ),
        baseline_config=_config(),
        perturbations=(
            _perturbation(
                "favor-circuity",
                coverage_factor=0.25,
                circuity_factor=2.0,
            ),
        ),
    )

    assert [(item.run_ref, item.rank) for item in result.baseline_ranking] == [
        ("run-a", 1),
        ("run-b", 2),
    ]
    scenario = result.scenarios[0]
    assert scenario.perturbation_id == "favor-circuity"
    assert [(item.run_ref, item.rank) for item in scenario.ranking] == [
        ("run-b", 1),
        ("run-a", 2),
    ]
    assert scenario.ranking[0].rank_delta == 1
    assert scenario.ranking[1].rank_delta == -1


def test_sensitivity_is_invariant_to_run_and_metric_input_order() -> None:
    runs = (
        _run("run-b", coverage=0.5, circuity=0.9),
        _run("run-a", coverage=0.9, circuity=0.2),
    )
    permuted_runs = tuple(
        ScoreSensitivityRunSnapshot(
            run_ref=run.run_ref,
            normalization_profile_id=run.normalization_profile_id,
            normalization_profile_version=run.normalization_profile_version,
            metrics=tuple(reversed(run.metrics)),
        )
        for run in reversed(runs)
    )
    perturbations = (
        _perturbation(
            "favor-circuity",
            coverage_factor=0.5,
            circuity_factor=1.5,
        ),
    )

    expected = analyze_score_sensitivity(
        runs,
        baseline_config=_config(),
        perturbations=perturbations,
    )
    actual = analyze_score_sensitivity(
        permuted_runs,
        baseline_config=_config(),
        perturbations=perturbations,
    )

    assert actual == expected


def test_sensitivity_rejects_incompatible_normalization_provenance() -> None:
    with pytest.raises(ScoreSensitivityError, match="normalization profile"):
        analyze_score_sensitivity(
            (
                _run("run-a", coverage=0.8, circuity=0.7),
                _run(
                    "run-b",
                    coverage=0.7,
                    circuity=0.8,
                    profile_version="2",
                ),
            ),
            baseline_config=_config(),
            perturbations=(
                _perturbation(
                    "same",
                    coverage_factor=1.0,
                    circuity_factor=1.0,
                ),
            ),
        )

    with pytest.raises(ScoreSensitivityError, match="policy version"):
        analyze_score_sensitivity(
            (
                _run("run-a", coverage=0.8, circuity=0.7),
                _run(
                    "run-b",
                    coverage=0.7,
                    circuity=0.8,
                    circuity_policy_version="circuity-v2",
                ),
            ),
            baseline_config=_config(),
            perturbations=(
                _perturbation(
                    "same",
                    coverage_factor=1.0,
                    circuity_factor=1.0,
                ),
            ),
        )


def test_missing_persisted_value_requires_zero_effective_weight() -> None:
    runs = (
        _run("run-a", coverage=0.8, circuity=None),
        _run("run-b", coverage=0.7, circuity=None),
    )
    zero_circuity = _config(circuity_weight=0.0)

    result = analyze_score_sensitivity(
        runs,
        baseline_config=zero_circuity,
        perturbations=(
            _perturbation(
                "keep-missing-zero",
                coverage_factor=1.0,
                circuity_factor=3.0,
            ),
        ),
    )
    assert result.baseline_ranking[0].run_ref == "run-a"
    assert result.scenarios[0].weights[1].weight == 0.0

    with pytest.raises(
        ScoreSensitivityError,
        match="missing persisted normalized value",
    ):
        analyze_score_sensitivity(
            runs,
            baseline_config=_config(circuity_weight=1.0),
            perturbations=(
                _perturbation(
                    "same",
                    coverage_factor=1.0,
                    circuity_factor=1.0,
                ),
            ),
        )


def test_perturbation_requires_complete_ids_and_positive_total_weight() -> None:
    with pytest.raises(ScoreSensitivityError, match="factor IDs"):
        build_perturbed_score_config(
            _config(),
            perturbation=ScoreWeightPerturbation(
                perturbation_id="partial",
                version="1",
                factors=(
                    ScoreSensitivityWeightFactor(COVERAGE, 1.0),
                ),
            ),
        )

    with pytest.raises(ScoreSensitivityError, match="positive score weight"):
        build_perturbed_score_config(
            _config(),
            perturbation=_perturbation(
                "all-zero",
                coverage_factor=0.0,
                circuity_factor=0.0,
            ),
        )


def test_equal_scores_use_stable_run_ref_tie_break() -> None:
    result = analyze_score_sensitivity(
        (
            _run("run-z", coverage=0.8, circuity=0.6),
            _run("run-a", coverage=0.8, circuity=0.6),
        ),
        baseline_config=_config(),
        perturbations=(
            _perturbation(
                "same",
                coverage_factor=1.0,
                circuity_factor=1.0,
            ),
        ),
    )

    assert [item.run_ref for item in result.baseline_ranking] == [
        "run-a",
        "run-z",
    ]
