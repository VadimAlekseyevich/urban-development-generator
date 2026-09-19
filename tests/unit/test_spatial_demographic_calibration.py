from __future__ import annotations

import pytest

from core.urban_generator.demography import (
    AgeGroupPopulation,
    BlockDemographicAggregate,
    DemographicAggregationResult,
    DemographicAggregationTotals,
    PopulationRasterSample,
    PopulationRasterSamplingResult,
    PopulationRasterValueKind,
    SpatialCalibrationError,
    SpatialCalibrationPolicy,
    SpatialDemographicCalibrator,
)
from core.urban_generator.zoning import ZoneClass


SCENARIO_FP = "a" * 64
EMPLOYMENT_FP = "b" * 64


def _groups(child: int, adult: int) -> tuple[AgeGroupPopulation, ...]:
    return (
        AgeGroupPopulation(
            code="child",
            min_age=0,
            max_age=17,
            residents=child,
        ),
        AgeGroupPopulation(
            code="adult",
            min_age=18,
            max_age=None,
            residents=adult,
        ),
    )


def _aggregation() -> DemographicAggregationResult:
    blocks = (
        BlockDemographicAggregate(
            block_id="block-1",
            zone_id="zone-1",
            zone_class=ZoneClass.RESIDENTIAL,
            building_count=2,
            population=8,
            age_groups=_groups(2, 6),
            jobs_estimate=1.0,
        ),
        BlockDemographicAggregate(
            block_id="block-2",
            zone_id="zone-1",
            zone_class=ZoneClass.RESIDENTIAL,
            building_count=1,
            population=2,
            age_groups=_groups(0, 2),
            jobs_estimate=3.0,
        ),
        BlockDemographicAggregate(
            block_id="block-3",
            zone_id="zone-2",
            zone_class=ZoneClass.MIXED,
            building_count=1,
            population=5,
            age_groups=_groups(1, 4),
            jobs_estimate=2.0,
        ),
    )
    zones = (
        ZoneDemographicAggregate(
            zone_id="zone-1",
            zone_class=ZoneClass.RESIDENTIAL,
            block_count=2,
            building_count=3,
            population=10,
            age_groups=_groups(2, 8),
            jobs_estimate=4.0,
        ),
        ZoneDemographicAggregate(
            zone_id="zone-2",
            zone_class=ZoneClass.MIXED,
            block_count=1,
            building_count=1,
            population=5,
            age_groups=_groups(1, 4),
            jobs_estimate=2.0,
        ),
    )
    return DemographicAggregationResult(
        scenario_version="demography-v1",
        scenario_fingerprint=SCENARIO_FP,
        employment_config_version="employment-v1",
        employment_config_fingerprint=EMPLOYMENT_FP,
        blocks=blocks,
        zones=zones,
        totals=DemographicAggregationTotals(
            building_count=4,
            block_count=3,
            zone_count=2,
            population=15,
            age_groups=_groups(3, 12),
            jobs_estimate=6.0,
        ),
    )


def _sample(
    subject_id: str,
    *,
    sampled_population: float,
    valid: int = 1,
    nodata: int = 0,
) -> PopulationRasterSample:
    requested = valid + nodata
    sampled_area = valid * 100.0
    density = (
        sampled_population / sampled_area * 1_000_000.0
        if sampled_area > 0.0
        else 0.0
    )
    return PopulationRasterSample(
        subject_id=subject_id,
        requested_cell_count=requested,
        valid_cell_count=valid,
        nodata_cell_count=nodata,
        sampled_area_m2=sampled_area,
        sampled_population=sampled_population,
        mean_density_per_km2=density,
    )


def _raster(
    samples: tuple[PopulationRasterSample, ...],
) -> PopulationRasterSamplingResult:
    return PopulationRasterSamplingResult(
        working_srid=3857,
        value_kind=PopulationRasterValueKind.POPULATION_PER_CELL,
        cell_area_m2=100.0,
        samples=tuple(sorted(samples, key=lambda item: item.subject_id)),
    )


def test_raster_redistributes_population_only_within_zone() -> None:
    aggregation = _aggregation()
    raster = _raster(
        (
            _sample("block-1", sampled_population=1.0),
            _sample("block-2", sampled_population=9.0),
            _sample("block-3", sampled_population=100.0),
        )
    )

    result = SpatialDemographicCalibrator().calibrate(
        aggregation,
        raster=raster,
    )

    assert [block.population for block in result.aggregation.blocks] == [1, 9, 5]
    assert result.aggregation.zones == aggregation.zones
    assert result.aggregation.totals == aggregation.totals
    assert [block.jobs_estimate for block in result.aggregation.blocks] == [
        1.0,
        3.0,
        2.0,
    ]
    assert [group.residents for group in result.aggregation.blocks[0].age_groups] == [
        0,
        1,
    ]
    assert [group.residents for group in result.aggregation.blocks[1].age_groups] == [
        2,
        7,
    ]
    assert result.diagnostics.moved_population == 7
    assert result.diagnostics.maximum_block_shift == 7


def test_zero_raster_evidence_falls_back_to_baseline_distribution() -> None:
    aggregation = _aggregation()
    raster = _raster(
        tuple(
            _sample(block.block_id, sampled_population=0.0)
            for block in aggregation.blocks
        )
    )

    result = SpatialDemographicCalibrator().calibrate(
        aggregation,
        raster=raster,
    )

    assert result.aggregation == aggregation
    assert result.diagnostics.fallback_zone_count == 2
    assert result.diagnostics.evidence_block_count == 0
    assert result.diagnostics.moved_population == 0


def test_all_nodata_sample_is_safe_fallback() -> None:
    aggregation = _aggregation()
    raster = _raster(
        (
            _sample("block-1", sampled_population=0.0, valid=0, nodata=1),
            _sample("block-2", sampled_population=0.0, valid=0, nodata=1),
            _sample("block-3", sampled_population=0.0, valid=0, nodata=1),
        )
    )

    result = SpatialDemographicCalibrator().calibrate(
        aggregation,
        raster=raster,
    )

    assert result.aggregation == aggregation
    assert result.diagnostics.fallback_zone_count == 2


def test_minimum_valid_fraction_filters_weak_evidence() -> None:
    raster = _raster(
        (
            _sample(
                "block-1",
                sampled_population=100.0,
                valid=1,
                nodata=1,
            ),
            _sample("block-2", sampled_population=10.0),
            _sample("block-3", sampled_population=10.0),
        )
    )
    calibrator = SpatialDemographicCalibrator(
        policy=SpatialCalibrationPolicy(
            raster_weight=1.0,
            minimum_valid_fraction=0.75,
        )
    )

    result = calibrator.calibrate(_aggregation(), raster=raster)

    assert [block.population for block in result.aggregation.blocks[:2]] == [
        0,
        10,
    ]
    audit = {
        item.block_id: item for item in result.block_calibrations
    }
    assert audit["block-1"].raster_evidence_used is False
    assert audit["block-2"].raster_evidence_used is True


def test_partial_raster_weight_blends_baseline_and_evidence() -> None:
    raster = _raster(
        (
            _sample("block-1", sampled_population=1.0),
            _sample("block-2", sampled_population=9.0),
            _sample("block-3", sampled_population=5.0),
        )
    )
    calibrator = SpatialDemographicCalibrator(
        policy=SpatialCalibrationPolicy(raster_weight=0.5)
    )

    result = calibrator.calibrate(_aggregation(), raster=raster)

    assert [block.population for block in result.aggregation.blocks[:2]] == [
        5,
        5,
    ]
    assert result.aggregation.zones[0].population == 10


def test_raster_samples_must_cover_exactly_all_blocks() -> None:
    raster = _raster(
        (
            _sample("block-1", sampled_population=1.0),
            _sample("block-2", sampled_population=9.0),
        )
    )

    with pytest.raises(SpatialCalibrationError, match="exactly"):
        SpatialDemographicCalibrator().calibrate(
            _aggregation(),
            raster=raster,
        )


def test_zero_raster_weight_preserves_baseline() -> None:
    aggregation = _aggregation()
    raster = _raster(
        (
            _sample("block-1", sampled_population=1.0),
            _sample("block-2", sampled_population=9.0),
            _sample("block-3", sampled_population=5.0),
        )
    )
    calibrator = SpatialDemographicCalibrator(
        policy=SpatialCalibrationPolicy(raster_weight=0.0)
    )

    result = calibrator.calibrate(aggregation, raster=raster)

    assert result.aggregation == aggregation
    assert result.diagnostics.moved_population == 0


def test_calibration_enforces_block_limit() -> None:
    aggregation = _aggregation()
    raster = _raster(
        tuple(
            _sample(block.block_id, sampled_population=1.0)
            for block in aggregation.blocks
        )
    )
    calibrator = SpatialDemographicCalibrator(
        policy=SpatialCalibrationPolicy(max_blocks=2)
    )

    with pytest.raises(SpatialCalibrationError, match="limit exceeded"):
        calibrator.calibrate(aggregation, raster=raster)
