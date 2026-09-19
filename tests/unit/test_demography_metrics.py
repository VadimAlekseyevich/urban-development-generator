from __future__ import annotations

import pytest

from core.urban_generator.demography import (
    AgeGroupPopulation,
    BlockDemographicAggregate,
    DemographicAggregationResult,
    DemographicAggregationTotals,
    DemographyBlockArea,
    DemographyMetricsBuilder,
    DemographyMetricsError,
    ZoneDemographicAggregate,
)
from core.urban_generator.zoning import ZoneClass


def _groups(child: int, adult: int) -> tuple[AgeGroupPopulation, ...]:
    return (
        AgeGroupPopulation(code="child", min_age=0, max_age=17, residents=child),
        AgeGroupPopulation(code="adult", min_age=18, max_age=None, residents=adult),
    )


def _aggregation() -> DemographicAggregationResult:
    return DemographicAggregationResult(
        scenario_version="demography-v1",
        scenario_fingerprint="a" * 64,
        employment_config_version="employment-v1",
        employment_config_fingerprint="b" * 64,
        blocks=(
            BlockDemographicAggregate(
                block_id="block-1",
                zone_id="zone-1",
                zone_class=ZoneClass.RESIDENTIAL,
                building_count=2,
                population=4,
                age_groups=_groups(1, 3),
                jobs_estimate=1.5,
            ),
            BlockDemographicAggregate(
                block_id="block-2",
                zone_id="zone-1",
                zone_class=ZoneClass.RESIDENTIAL,
                building_count=3,
                population=6,
                age_groups=_groups(2, 4),
                jobs_estimate=2.5,
            ),
        ),
        zones=(
            ZoneDemographicAggregate(
                zone_id="zone-1",
                zone_class=ZoneClass.RESIDENTIAL,
                block_count=2,
                building_count=5,
                population=10,
                age_groups=_groups(3, 7),
                jobs_estimate=4.0,
            ),
        ),
        totals=DemographicAggregationTotals(
            building_count=5,
            block_count=2,
            zone_count=1,
            population=10,
            age_groups=_groups(3, 7),
            jobs_estimate=4.0,
        ),
    )


def test_metrics_compute_density_totals_and_age_shares() -> None:
    result = DemographyMetricsBuilder().build(
        _aggregation(),
        block_areas=(
            DemographyBlockArea(block_id="block-1", area_m2=100_000.0),
            DemographyBlockArea(block_id="block-2", area_m2=300_000.0),
        ),
    )

    first, second = result.blocks
    assert first.population_density_per_km2 == pytest.approx(40.0)
    assert second.population_density_per_km2 == pytest.approx(20.0)
    assert first.age_groups[0].share == pytest.approx(0.25)
    assert first.age_groups[1].share == pytest.approx(0.75)

    assert result.totals.area_m2 == pytest.approx(400_000.0)
    assert result.totals.population == 10
    assert result.totals.population_density_per_km2 == pytest.approx(25.0)
    assert result.totals.jobs_estimate == pytest.approx(4.0)
    assert [item.share for item in result.totals.age_groups] == pytest.approx(
        [0.3, 0.7]
    )


def test_zero_population_produces_zero_density_and_shares() -> None:
    aggregation = DemographicAggregationResult(
        scenario_version="demography-v1",
        scenario_fingerprint="a" * 64,
        employment_config_version="employment-v1",
        employment_config_fingerprint="b" * 64,
        blocks=(
            BlockDemographicAggregate(
                block_id="block-1",
                zone_id="zone-1",
                zone_class=ZoneClass.RESIDENTIAL,
                building_count=1,
                population=0,
                age_groups=_groups(0, 0),
                jobs_estimate=0.0,
            ),
        ),
        zones=(
            ZoneDemographicAggregate(
                zone_id="zone-1",
                zone_class=ZoneClass.RESIDENTIAL,
                block_count=1,
                building_count=1,
                population=0,
                age_groups=_groups(0, 0),
                jobs_estimate=0.0,
            ),
        ),
        totals=DemographicAggregationTotals(
            building_count=1,
            block_count=1,
            zone_count=1,
            population=0,
            age_groups=_groups(0, 0),
            jobs_estimate=0.0,
        ),
    )

    result = DemographyMetricsBuilder().build(
        aggregation,
        block_areas=(
            DemographyBlockArea(block_id="block-1", area_m2=10_000.0),
        ),
    )

    assert result.blocks[0].population_density_per_km2 == 0.0
    assert [item.share for item in result.blocks[0].age_groups] == [0.0, 0.0]
    assert result.totals.population_density_per_km2 == 0.0


def test_block_areas_must_cover_exactly_aggregation_blocks() -> None:
    with pytest.raises(DemographyMetricsError, match="exactly"):
        DemographyMetricsBuilder().build(
            _aggregation(),
            block_areas=(
                DemographyBlockArea(block_id="block-1", area_m2=100.0),
            ),
        )


def test_metric_builder_is_input_order_independent() -> None:
    areas = (
        DemographyBlockArea(block_id="block-2", area_m2=300_000.0),
        DemographyBlockArea(block_id="block-1", area_m2=100_000.0),
    )

    result = DemographyMetricsBuilder().build(
        _aggregation(),
        block_areas=areas,
    )

    assert [item.block_id for item in result.blocks] == ["block-1", "block-2"]


def test_metric_builder_enforces_block_limit() -> None:
    with pytest.raises(DemographyMetricsError, match="limit exceeded"):
        DemographyMetricsBuilder(max_blocks=1).build(
            _aggregation(),
            block_areas=(
                DemographyBlockArea(block_id="block-1", area_m2=100.0),
                DemographyBlockArea(block_id="block-2", area_m2=100.0),
            ),
        )
