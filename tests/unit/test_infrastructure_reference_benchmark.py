from __future__ import annotations

import pytest

from benchmarks.infrastructure_reference import (
    REFERENCE_FIXTURE_NAME,
    InfrastructureBenchmarkConfig,
    run_reference_infrastructure_benchmark,
)


def test_reference_infrastructure_fixture_counts_one_t07_routing_pass() -> None:
    config = InfrastructureBenchmarkConfig(
        demand_count=12,
        candidate_count=4,
        demand_batch_size=3,
        capacity=2.0,
        max_facilities=3,
    )

    result = run_reference_infrastructure_benchmark(config)

    assert result.fixture_name == REFERENCE_FIXTURE_NAME
    assert result.demand_count == 12
    assert result.candidate_count == 4
    assert result.subject_count == 48
    assert result.reachable_count == 48
    assert result.expected_routing_call_count == 16
    assert result.routing_call_count == 16
    assert result.routing_call_count_after_greedy == 16
    assert result.greedy_iteration_count == 3
    assert result.accepted_facility_count == 3
    assert result.remaining_demand == pytest.approx(6.0)
    assert result.accessibility_ms >= 0.0
    assert result.greedy_ms >= 0.0
    assert len(result.deterministic_digest) == 64


def test_reference_infrastructure_fixture_is_deterministic() -> None:
    config = InfrastructureBenchmarkConfig(
        demand_count=12,
        candidate_count=4,
        demand_batch_size=3,
        capacity=2.0,
        max_facilities=3,
    )

    first = run_reference_infrastructure_benchmark(config)
    second = run_reference_infrastructure_benchmark(config)

    assert first.deterministic_digest == second.deterministic_digest
    assert first.routing_call_count == second.routing_call_count
    assert first.routing_call_count_after_greedy == second.routing_call_count_after_greedy
    assert first.remaining_demand == pytest.approx(second.remaining_demand)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("demand_count", 0),
        ("candidate_count", 0),
        ("demand_batch_size", 0),
        ("max_facilities", 0),
        ("capacity", 0.0),
        ("spacing_m", float("inf")),
    ),
)
def test_reference_infrastructure_config_rejects_invalid_values(
    field_name: str,
    value: int | float,
) -> None:
    with pytest.raises(ValueError):
        InfrastructureBenchmarkConfig(**{field_name: value})


def test_reference_infrastructure_config_rejects_inconsistent_bounds() -> None:
    with pytest.raises(ValueError, match="demand_batch_size"):
        InfrastructureBenchmarkConfig(
            demand_count=4,
            candidate_count=4,
            demand_batch_size=5,
        )
    with pytest.raises(ValueError, match="max_facilities"):
        InfrastructureBenchmarkConfig(
            demand_count=8,
            candidate_count=2,
            max_facilities=3,
        )
    with pytest.raises(ValueError, match="positive demand"):
        InfrastructureBenchmarkConfig(
            demand_count=8,
            candidate_count=4,
            capacity=4.0,
            max_facilities=3,
        )
