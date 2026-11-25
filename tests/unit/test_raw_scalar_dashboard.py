import pytest

from backend.app.application.metric_dashboard import MetricDashboardDataError, decode_raw_scalars
from backend.app.db.run_validation_writer import (
    RunValidationPersistenceError,
    scalar_metrics_payload,
)
from core.urban_generator.domain import RawMetricId
from core.urban_generator.metrics import CompositeScoreRawMetric


def test_raw_metrics_preserve_zero_and_missing_independently_of_score() -> None:
    metrics = (
        CompositeScoreRawMetric(RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M, None),
        CompositeScoreRawMetric(RawMetricId.DEMOGRAPHY_TOTAL_POPULATION, 0.0),
    )
    decoded = decode_raw_scalars({"raw_scalars": scalar_metrics_payload(metrics)})
    assert decoded is not None
    assert {row.metric_id: row.raw_value for row in decoded} == {
        RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M: None,
        RawMetricId.DEMOGRAPHY_TOTAL_POPULATION: 0.0,
    }
    assert decode_raw_scalars({"evaluation": {}}) is None


def test_duplicate_scalar_metrics_rejected() -> None:
    metric = CompositeScoreRawMetric(RawMetricId.DEMOGRAPHY_TOTAL_POPULATION, 10.0)
    with pytest.raises(RunValidationPersistenceError, match="duplicate"):
        scalar_metrics_payload((metric, metric))


def test_invalid_scalar_envelope_rejected() -> None:
    with pytest.raises(MetricDashboardDataError):
        decode_raw_scalars({"raw_scalars": {
            "schema_version": "1", "values": {"demography.total_population": float("nan")},
        }})
