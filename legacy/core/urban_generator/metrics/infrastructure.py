from __future__ import annotations

from core.urban_generator.domain import (
    CANONICAL_METRIC_REGISTRY,
    MetricSource,
    RawMetricId,
)
from core.urban_generator.infrastructure.metrics import (
    InfrastructureMetricsResult,
)


class InfrastructureMetricAdapterError(ValueError):
    """Raised when S11-T08 receives a non-canonical S10 metric result."""


INFRASTRUCTURE_RAW_METRIC_IDS = tuple(
    definition.metric_id
    for definition in CANONICAL_METRIC_REGISTRY.definitions_for(
        source=MetricSource.INFRASTRUCTURE
    )
)


class InfrastructureMetricAdapter:
    """Validate and reuse the canonical S10 infrastructure metric result as-is."""

    version = "1"

    def adapt(
        self,
        *,
        metrics: InfrastructureMetricsResult,
    ) -> InfrastructureMetricsResult:
        if not isinstance(metrics, InfrastructureMetricsResult):
            raise InfrastructureMetricAdapterError(
                "metrics must be InfrastructureMetricsResult"
            )
        actual_ids = tuple(item.metric_id for item in metrics.raw_metrics)
        if actual_ids != INFRASTRUCTURE_RAW_METRIC_IDS:
            raise InfrastructureMetricAdapterError(
                "infrastructure metrics must match the canonical registry order"
            )
        return metrics


if INFRASTRUCTURE_RAW_METRIC_IDS != (
    RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
    RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE,
    RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M,
    RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M,
    RawMetricId.INFRASTRUCTURE_UNMET_DEMAND,
    RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
):
    raise InfrastructureMetricAdapterError(
        "canonical infrastructure registry membership changed unexpectedly"
    )
