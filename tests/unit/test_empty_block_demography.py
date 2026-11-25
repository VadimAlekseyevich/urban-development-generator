"""Unbuilt zoned blocks are real zero-demand blocks, not missing rows."""

from dataclasses import replace

import pytest

from core.urban_generator.demography.aggregation import (
    BlockDemographicAggregate,
    DemographicAggregationResult,
    DemographicAggregationTotals,
    ZoneDemographicAggregate,
)
from core.urban_generator.demography.age_allocation import AgeGroupPopulation
from core.urban_generator.stages.buildings import BuildingStageBlockRef
from core.urban_generator.stages.demography import complete_associated_blocks
from core.urban_generator.zoning import ZoneClass


def _aggregation() -> DemographicAggregationResult:
    groups = (AgeGroupPopulation("all", 0, None, 10),)
    return DemographicAggregationResult(
        scenario_version="v1", scenario_fingerprint="a" * 64,
        employment_config_version="v1", employment_config_fingerprint="b" * 64,
        blocks=(BlockDemographicAggregate(
            "occupied", "zone-r", ZoneClass.RESIDENTIAL, 1, 10, groups, 2,
        ),),
        zones=(ZoneDemographicAggregate(
            "zone-r", ZoneClass.RESIDENTIAL, 1, 1, 10, groups, 2,
        ),),
        totals=DemographicAggregationTotals(1, 1, 1, 10, groups, 2),
    )


def test_all_associated_blocks_preserve_demographic_totals() -> None:
    original = _aggregation()
    refs = (
        BuildingStageBlockRef("occupied", "zone-r", ZoneClass.RESIDENTIAL, 100),
        BuildingStageBlockRef("empty", "zone-r", ZoneClass.RESIDENTIAL, 200),
        BuildingStageBlockRef("park", "zone-p", ZoneClass.RECREATION, 300),
    )
    result = complete_associated_blocks(original, refs, max_blocks=3)
    assert result.totals.block_count == 3
    assert result.totals.zone_count == 2
    assert result.totals.population == 10
    assert result.totals.jobs_estimate == 2
    for block in result.blocks:
        if block.block_id != "occupied":
            assert block.building_count == block.population == block.jobs_estimate == 0
            assert all(group.residents == 0 for group in block.age_groups)
    assert result.zones[0].building_count == 0
    assert result.zones[1].block_count == 2
    assert complete_associated_blocks(result, refs, max_blocks=3) == result


def test_empty_blocks_cannot_acquire_people_or_jobs() -> None:
    original = _aggregation()
    with pytest.raises(ValueError, match="empty buildings"):
        replace(original.blocks[0], building_count=0)
    with pytest.raises(ValueError, match="unknown blocks"):
        complete_associated_blocks(original, (), max_blocks=3)
    with pytest.raises(ValueError, match="block zone mismatch"):
        complete_associated_blocks(
            original, (BuildingStageBlockRef("occupied", "other", ZoneClass.MIXED, 100),),
            max_blocks=3,
        )
