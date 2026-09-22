import numpy as np
import pytest

from core.urban_generator.blocks import (
    SliverCleanupDiagnostics,
    SliverCleanupPolicy,
    SliverCleanupResult,
)
from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingArchetype,
    BuildingAreaCalculationResult,
    BuildingAreaMetrics,
    BuildingAreaSummary,
    BuildingAttributeAssignmentResult,
    BuildingUse,
)
from core.urban_generator.domain import (
    CANONICAL_METRIC_REGISTRY,
    MetricSource,
    RawMetricId,
    WorkingCRS,
)
from core.urban_generator.metrics import (
    LAND_BUILDING_RAW_METRIC_IDS,
    LandBuildingMetricAdapter,
    LandBuildingMetricAdapterError,
)
from core.urban_generator.stages.blocks import BlocksAndParcelsStageOutput
from core.urban_generator.stages.buildings import (
    BuildingStageBlockRef,
    BuildingStageOutput,
)
from core.urban_generator.suitability import (
    SuitabilityGridSpec,
    WeightedSuitabilityResult,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 32637


def _suitability(*, working_srid: int = WORKING_SRID) -> WeightedSuitabilityResult:
    grid = SuitabilityGridSpec(
        working_srid=working_srid,
        bounds=(0.0, 0.0, 100.0, 100.0),
        width=10,
        height=10,
    )
    valid = np.zeros((10, 10), dtype=np.bool_)
    valid[:8, :] = True
    hard = ~valid
    scores = np.zeros((10, 10), dtype=np.float64)
    scores[valid] = 0.75
    return WeightedSuitabilityResult(
        grid=grid,
        scores=scores,
        valid_mask=valid,
        hard_excluded_mask=hard,
        config_version="1",
        config_fingerprint="a" * 64,
        factor_versions=(("test.factor", "1"),),
    )


def _cleanup(
    *,
    working_srid: int = WORKING_SRID,
    developed_area_m2: float = 100.0,
) -> SliverCleanupResult:
    return SliverCleanupResult(
        working_crs=WorkingCRS(working_srid),
        policy=SliverCleanupPolicy(min_area_m2=1.0),
        blocks=(),
        events=(),
        diagnostics=SliverCleanupDiagnostics(
            input_block_count=0,
            input_sliver_count=0,
            output_block_count=0,
            merged_event_count=0,
            dropped_event_count=0,
            kept_unresolved_event_count=0,
            operation_count=0,
            spatial_candidate_pair_count=0,
            adjacency_pair_count=0,
            input_area_m2=developed_area_m2,
            output_area_m2=developed_area_m2,
            dropped_area_m2=0.0,
        ),
    )


def _blocks(
    *,
    working_srid: int = WORKING_SRID,
    developed_area_m2: float = 100.0,
) -> BlocksAndParcelsStageOutput:
    return BlocksAndParcelsStageOutput(
        clipping=object(),
        metrics=object(),
        frontage=object(),
        split=object(),
        cleanup=_cleanup(
            working_srid=working_srid,
            developed_area_m2=developed_area_m2,
        ),
        association=object(),
        subdivision=object(),
    )


def _building_attributes() -> BuildingAttributeAssignmentResult:
    return BuildingAttributeAssignmentResult(
        config_version="1",
        config_fingerprint="b" * 64,
        buildings=(
            AssignedBuildingAttributes(
                building_id="building:001",
                source_id="source:001",
                zone_class=ZoneClass.RESIDENTIAL,
                archetype=BuildingArchetype.BAR,
                use=BuildingUse.RESIDENTIAL,
                floors=2,
                config_version="1",
            ),
            AssignedBuildingAttributes(
                building_id="building:002",
                source_id="source:002",
                zone_class=ZoneClass.MIXED,
                archetype=BuildingArchetype.POINT,
                use=BuildingUse.MIXED,
                floors=2,
                config_version="1",
            ),
        ),
    )


def _area_metrics(
    *,
    working_srid: int = WORKING_SRID,
) -> BuildingAreaCalculationResult:
    buildings = (
        BuildingAreaMetrics(
            building_id="building:001",
            footprint_area_m2=10.0,
            floors=2,
            gfa_m2=20.0,
        ),
        BuildingAreaMetrics(
            building_id="building:002",
            footprint_area_m2=10.0,
            floors=2,
            gfa_m2=20.0,
        ),
    )
    return BuildingAreaCalculationResult(
        working_srid=working_srid,
        buildings=buildings,
        summary=BuildingAreaSummary(
            site_area_m2=100.0,
            baseline_footprint_area_m2=0.0,
            baseline_gfa_m2=0.0,
            generated_footprint_area_m2=20.0,
            generated_gfa_m2=40.0,
            total_footprint_area_m2=20.0,
            total_gfa_m2=40.0,
            coverage_ratio=0.2,
            far=0.4,
            building_count=2,
        ),
    )


def _buildings(
    *,
    working_srid: int = WORKING_SRID,
    recreation_area_m2: float = 30.0,
) -> BuildingStageOutput:
    return BuildingStageOutput(
        sources=(),
        accepted_proposals=(),
        ownership=(),
        blocks=(
            BuildingStageBlockRef(
                block_id="block:001",
                zone_id="zone:recreation",
                zone_class=ZoneClass.RECREATION,
                area_m2=recreation_area_m2,
            ),
            BuildingStageBlockRef(
                block_id="block:002",
                zone_id="zone:residential",
                zone_class=ZoneClass.RESIDENTIAL,
                area_m2=70.0,
            ),
        ),
        attributes=_building_attributes(),
        area_subjects=(),
        area_metrics=_area_metrics(working_srid=working_srid),
        fixed_building_refs=(),
    )


def _scalar(result, metric_id: RawMetricId) -> float:
    value = result.require(metric_id).scalar_value
    assert value is not None
    return value


def test_adapter_uses_canonical_land_building_registry_order() -> None:
    expected = tuple(
        definition.metric_id
        for definition in CANONICAL_METRIC_REGISTRY.definitions_for(
            source=MetricSource.LAND_BUILDING
        )
    )
    assert LAND_BUILDING_RAW_METRIC_IDS == expected
    assert LAND_BUILDING_RAW_METRIC_IDS == (
        RawMetricId.LAND_DEVELOPABLE_AREA_M2,
        RawMetricId.LAND_DEVELOPED_AREA_M2,
        RawMetricId.LAND_GREEN_RECREATION_SHARE,
        RawMetricId.BUILDINGS_COVERAGE_RATIO,
        RawMetricId.BUILDINGS_FAR,
        RawMetricId.BUILDINGS_GFA_M2,
        RawMetricId.BUILDINGS_ARCHETYPE_DISTRIBUTION,
    )


def test_adapter_projects_authoritative_results_without_spatial_recomputation() -> None:
    result = LandBuildingMetricAdapter().adapt(
        suitability=_suitability(),
        blocks=_blocks(),
        buildings=_buildings(),
    )

    assert tuple(item.metric_id for item in result.raw_metrics) == (
        LAND_BUILDING_RAW_METRIC_IDS
    )
    assert _scalar(result, RawMetricId.LAND_DEVELOPABLE_AREA_M2) == 8_000.0
    assert _scalar(result, RawMetricId.LAND_DEVELOPED_AREA_M2) == 100.0
    assert _scalar(result, RawMetricId.LAND_GREEN_RECREATION_SHARE) == 0.3
    assert _scalar(result, RawMetricId.BUILDINGS_COVERAGE_RATIO) == 0.2
    assert _scalar(result, RawMetricId.BUILDINGS_FAR) == 0.4
    assert _scalar(result, RawMetricId.BUILDINGS_GFA_M2) == 40.0

    distribution = result.require(
        RawMetricId.BUILDINGS_ARCHETYPE_DISTRIBUTION
    ).archetype_distribution
    by_archetype = {item.archetype: item for item in distribution}
    assert tuple(item.archetype for item in distribution) == tuple(
        BuildingArchetype
    )
    assert by_archetype[BuildingArchetype.BAR].building_count == 1
    assert by_archetype[BuildingArchetype.BAR].share == 0.5
    assert by_archetype[BuildingArchetype.POINT].building_count == 1
    assert by_archetype[BuildingArchetype.POINT].share == 0.5
    assert sum(item.building_count for item in distribution) == 2
    assert sum(item.share for item in distribution) == 1.0

    assert result.diagnostics.developable_cell_count == 80
    assert result.diagnostics.developed_block_area_m2 == 100.0
    assert result.diagnostics.recreation_block_area_m2 == 30.0
    assert result.diagnostics.generated_building_count == 2
    assert result.diagnostics.archetype_distribution_includes_fixed is False


def test_adapter_preserves_empty_archetype_distribution_policy() -> None:
    area_metrics = BuildingAreaCalculationResult(
        working_srid=WORKING_SRID,
        buildings=(),
        summary=BuildingAreaSummary(
            site_area_m2=100.0,
            baseline_footprint_area_m2=10.0,
            baseline_gfa_m2=20.0,
            generated_footprint_area_m2=0.0,
            generated_gfa_m2=0.0,
            total_footprint_area_m2=10.0,
            total_gfa_m2=20.0,
            coverage_ratio=0.1,
            far=0.2,
            building_count=0,
        ),
    )
    building_output = BuildingStageOutput(
        sources=(),
        accepted_proposals=(),
        ownership=(),
        blocks=(),
        attributes=BuildingAttributeAssignmentResult(
            config_version="1",
            config_fingerprint="c" * 64,
            buildings=(),
        ),
        area_subjects=(),
        area_metrics=area_metrics,
        fixed_building_refs=(),
    )

    result = LandBuildingMetricAdapter().adapt(
        suitability=_suitability(),
        blocks=_blocks(developed_area_m2=0.0),
        buildings=building_output,
    )

    distribution = result.require(
        RawMetricId.BUILDINGS_ARCHETYPE_DISTRIBUTION
    ).archetype_distribution
    assert tuple(item.archetype for item in distribution) == tuple(
        BuildingArchetype
    )
    assert all(item.building_count == 0 for item in distribution)
    assert all(item.share == 0.0 for item in distribution)
    assert _scalar(result, RawMetricId.LAND_GREEN_RECREATION_SHARE) == 0.0
    assert _scalar(result, RawMetricId.BUILDINGS_GFA_M2) == 20.0


def test_adapter_rejects_crs_or_building_identity_drift() -> None:
    with pytest.raises(
        LandBuildingMetricAdapterError,
        match="block cleanup working_srid",
    ):
        LandBuildingMetricAdapter().adapt(
            suitability=_suitability(),
            blocks=_blocks(working_srid=32636),
            buildings=_buildings(),
        )

    attributes = BuildingAttributeAssignmentResult(
        config_version="1",
        config_fingerprint="d" * 64,
        buildings=(
            AssignedBuildingAttributes(
                building_id="building:999",
                source_id="source:999",
                zone_class=ZoneClass.RESIDENTIAL,
                archetype=BuildingArchetype.BAR,
                use=BuildingUse.RESIDENTIAL,
                floors=2,
                config_version="1",
            ),
        ),
    )
    building_output = _buildings()
    building_output = BuildingStageOutput(
        sources=building_output.sources,
        accepted_proposals=building_output.accepted_proposals,
        ownership=building_output.ownership,
        blocks=building_output.blocks,
        attributes=attributes,
        area_subjects=building_output.area_subjects,
        area_metrics=building_output.area_metrics,
        fixed_building_refs=building_output.fixed_building_refs,
    )

    with pytest.raises(
        LandBuildingMetricAdapterError,
        match="IDs must match exactly",
    ):
        LandBuildingMetricAdapter().adapt(
            suitability=_suitability(),
            blocks=_blocks(),
            buildings=building_output,
        )


def test_adapter_rejects_recreation_area_above_developed_area() -> None:
    with pytest.raises(
        LandBuildingMetricAdapterError,
        match="recreation block area",
    ):
        LandBuildingMetricAdapter().adapt(
            suitability=_suitability(),
            blocks=_blocks(developed_area_m2=20.0),
            buildings=_buildings(recreation_area_m2=30.0),
        )
