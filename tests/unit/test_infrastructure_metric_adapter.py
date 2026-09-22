import pytest

from core.urban_generator.domain import (
    CANONICAL_METRIC_REGISTRY,
    MetricSource,
    RawMetricId,
)
from core.urban_generator.infrastructure.metrics import (
    InfrastructureAgeCoverage,
    InfrastructureMetricsBuilder,
    InfrastructureMetricsDiagnostics,
    InfrastructureMetricsResult,
    InfrastructureRawMetricValue,
)
from core.urban_generator.metrics import (
    INFRASTRUCTURE_RAW_METRIC_IDS,
    InfrastructureMetricAdapter,
)


def _result(*, empty_distances: bool = False) -> InfrastructureMetricsResult:
    return InfrastructureMetricsResult(
        raw_metrics=(
            InfrastructureRawMetricValue(
                metric_id=(
                    RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO
                ),
                scalar_value=0.75,
            ),
            InfrastructureRawMetricValue(
                metric_id=RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE,
                age_coverage=(
                    InfrastructureAgeCoverage(
                        demographic_group="child",
                        population=40.0,
                        covered_population=30.0,
                        coverage_ratio=0.75,
                    ),
                ),
            ),
            InfrastructureRawMetricValue(
                metric_id=RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M,
                scalar_value=None if empty_distances else 100.0,
            ),
            InfrastructureRawMetricValue(
                metric_id=RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M,
                scalar_value=None if empty_distances else 250.0,
            ),
            InfrastructureRawMetricValue(
                metric_id=RawMetricId.INFRASTRUCTURE_UNMET_DEMAND,
                scalar_value=20.0,
            ),
            InfrastructureRawMetricValue(
                metric_id=RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
                scalar_value=0.8,
            ),
        ),
        diagnostics=InfrastructureMetricsDiagnostics(
            infrastructure_type_count=1,
            demand_item_count=2,
            accepted_generated_facility_count=1,
            existing_distance_sample_count=1,
            generated_distance_sample_count=1,
        ),
    )


def test_adapter_uses_canonical_infrastructure_registry_order() -> None:
    expected = tuple(
        definition.metric_id
        for definition in CANONICAL_METRIC_REGISTRY.definitions_for(
            source=MetricSource.INFRASTRUCTURE
        )
    )

    assert INFRASTRUCTURE_RAW_METRIC_IDS == expected
    assert INFRASTRUCTURE_RAW_METRIC_IDS == (
        RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
        RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE,
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M,
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M,
        RawMetricId.INFRASTRUCTURE_UNMET_DEMAND,
        RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
    )


def test_adapter_reuses_existing_s10_result_by_identity() -> None:
    source = _result()

    adapted = InfrastructureMetricAdapter().adapt(metrics=source)

    assert adapted is source
    assert adapted.raw_metrics is source.raw_metrics
    assert adapted.diagnostics is source.diagnostics
    assert adapted.require(
        RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO
    ).scalar_value == 0.75
    assert adapted.require(
        RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE
    ).age_coverage[0].demographic_group == "child"


def test_adapter_preserves_missing_distance_policy() -> None:
    adapted = InfrastructureMetricAdapter().adapt(
        metrics=_result(empty_distances=True)
    )

    assert adapted.require(
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M
    ).scalar_value is None
    assert adapted.require(
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M
    ).scalar_value is None


def test_adapter_does_not_rebuild_infrastructure_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _result()

    def _unexpected_build(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "infrastructure metric rebuild is forbidden in S11-T08"
        )

    monkeypatch.setattr(
        InfrastructureMetricsBuilder,
        "build",
        _unexpected_build,
    )

    assert InfrastructureMetricAdapter().adapt(metrics=source) is source


def test_adapter_requires_typed_infrastructure_metrics_result() -> None:
    with pytest.raises(
        ValueError,
        match="InfrastructureMetricsResult",
    ):
        InfrastructureMetricAdapter().adapt(metrics=object())
