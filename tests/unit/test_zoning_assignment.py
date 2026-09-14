from __future__ import annotations

import pytest
from shapely.geometry import box

from core.urban_generator.zoning import (
    SuitabilityTargetShareAssigner,
    ZoneAssignmentError,
    ZoneAssignmentResult,
    ZoneClass,
    ZoneClassConfig,
    ZoningConfig,
    ZoningPartitionCell,
    ZoningPartitionResult,
    ZoningSeed,
)


def make_config(
    *,
    residential: float = 0.5,
    mixed: float = 0.25,
    public: float = 0.25,
    recreation: float = 0.0,
    version: str = "assignment-test-v1",
) -> ZoningConfig:
    return ZoningConfig(
        version=version,
        zones=(
            ZoneClassConfig(ZoneClass.RESIDENTIAL, residential, 1.0),
            ZoneClassConfig(ZoneClass.MIXED, mixed, 1.0),
            ZoneClassConfig(ZoneClass.PUBLIC, public, 1.0),
            ZoneClassConfig(ZoneClass.RECREATION, recreation, 1.0),
        ),
    )


def make_partition(
    scores: tuple[float, ...],
    *,
    widths: tuple[float, ...] | None = None,
) -> ZoningPartitionResult:
    if widths is None:
        widths = tuple(1.0 for _score in scores)
    assert len(widths) == len(scores)

    cells: list[ZoningPartitionCell] = []
    min_x = 0.0
    for index, (score, width) in enumerate(zip(scores, widths, strict=True)):
        max_x = min_x + width
        geometry = box(min_x, 0.0, max_x, 1.0)
        seed = ZoningSeed(
            row=0,
            col=index,
            x_m=(min_x + max_x) / 2.0,
            y_m=0.5,
            suitability_score=score,
        )
        cells.append(
            ZoningPartitionCell(
                seed_index=index,
                seed=seed,
                geometry=geometry,
                area_m2=float(geometry.area),
                validity_repaired=False,
            )
        )
        min_x = max_x

    developable = box(0.0, 0.0, min_x, 1.0)
    return ZoningPartitionResult(
        working_srid=32637,
        developable_area=developable,
        cells=tuple(cells),
        developable_area_m2=float(developable.area),
        covered_area_m2=float(developable.area),
        uncovered_area_m2=0.0,
        overlap_area_m2=0.0,
        developable_validity_repaired=False,
        repaired_cell_count=0,
    )


def assigned_classes(result: ZoneAssignmentResult) -> tuple[ZoneClass, ...]:
    return tuple(assignment.zone_class for assignment in result.assignments)


def test_assignment_uses_suitability_priority_and_target_area_deficit() -> None:
    partition = make_partition((0.1, 0.9, 0.2, 0.8))
    config = make_config()

    result = SuitabilityTargetShareAssigner().assign(partition=partition, config=config)

    assert assigned_classes(result) == (
        ZoneClass.PUBLIC,
        ZoneClass.RESIDENTIAL,
        ZoneClass.MIXED,
        ZoneClass.RESIDENTIAL,
    )
    assert result.share(ZoneClass.RESIDENTIAL).assigned_area_m2 == pytest.approx(2.0)
    assert result.share(ZoneClass.RESIDENTIAL).target_area_m2 == pytest.approx(2.0)
    assert result.share(ZoneClass.MIXED).assigned_area_m2 == pytest.approx(1.0)
    assert result.share(ZoneClass.PUBLIC).assigned_area_m2 == pytest.approx(1.0)
    assert result.share(ZoneClass.RECREATION).assigned_area_m2 == pytest.approx(0.0)
    assert result.absolute_target_error_m2 == pytest.approx(0.0)
    assert not hasattr(result.assignments[0], "geometry")


def test_changing_suitability_changes_which_cells_receive_the_same_target_classes() -> None:
    config = make_config()
    assigner = SuitabilityTargetShareAssigner()

    first = assigner.assign(
        partition=make_partition((0.1, 0.9, 0.2, 0.8)),
        config=config,
    )
    second = assigner.assign(
        partition=make_partition((0.95, 0.1, 0.9, 0.2)),
        config=config,
    )

    first_residential = tuple(
        item.cell_index for item in first.assignments if item.zone_class is ZoneClass.RESIDENTIAL
    )
    second_residential = tuple(
        item.cell_index for item in second.assignments if item.zone_class is ZoneClass.RESIDENTIAL
    )
    assert first_residential == (1, 3)
    assert second_residential == (0, 2)
    assert tuple(item.assigned_area_m2 for item in first.shares) == tuple(
        item.assigned_area_m2 for item in second.shares
    )


def test_target_shares_control_area_allocation_without_class_specific_suitability_rules() -> None:
    partition = make_partition((0.1, 0.9, 0.2, 0.8))
    config = make_config(residential=0.25, mixed=0.5, public=0.25)

    result = SuitabilityTargetShareAssigner().assign(partition=partition, config=config)

    mixed_cells = tuple(
        item.cell_index for item in result.assignments if item.zone_class is ZoneClass.MIXED
    )
    assert mixed_cells == (1, 3)
    assert result.share(ZoneClass.MIXED).achieved_share == pytest.approx(0.5)
    assert result.share(ZoneClass.RECREATION).cell_count == 0


def test_unequal_cell_areas_report_unavoidable_target_share_error() -> None:
    partition = make_partition((0.9, 0.8, 0.7), widths=(3.0, 2.0, 1.0))
    config = make_config()

    result = SuitabilityTargetShareAssigner().assign(partition=partition, config=config)

    assert assigned_classes(result) == (
        ZoneClass.RESIDENTIAL,
        ZoneClass.MIXED,
        ZoneClass.PUBLIC,
    )
    assert result.share(ZoneClass.RESIDENTIAL).assigned_area_m2 == pytest.approx(3.0)
    assert result.share(ZoneClass.MIXED).assigned_area_m2 == pytest.approx(2.0)
    assert result.share(ZoneClass.PUBLIC).assigned_area_m2 == pytest.approx(1.0)
    assert result.share(ZoneClass.MIXED).absolute_area_error_m2 == pytest.approx(0.5)
    assert result.share(ZoneClass.PUBLIC).absolute_area_error_m2 == pytest.approx(0.5)
    assert result.absolute_target_error_m2 == pytest.approx(1.0)


def test_assignment_is_deterministic_and_output_is_in_partition_cell_order() -> None:
    partition = make_partition((0.5, 0.5, 0.5, 0.5))
    config = make_config(residential=0.25, mixed=0.25, public=0.25, recreation=0.25)
    assigner = SuitabilityTargetShareAssigner()

    first = assigner.assign(partition=partition, config=config)
    second = assigner.assign(partition=partition, config=config)

    assert first == second
    assert tuple(item.cell_index for item in first.assignments) == (0, 1, 2, 3)
    assert assigned_classes(first) == tuple(ZoneClass)


def test_assignment_propagates_config_provenance_and_cell_seed_references() -> None:
    partition = make_partition((0.9, 0.8, 0.7, 0.6))
    config = make_config(version="zoning-policy-v7")

    result = SuitabilityTargetShareAssigner().assign(partition=partition, config=config)

    assert result.zoning_config_version == "zoning-policy-v7"
    assert result.zoning_config_fingerprint == config.fingerprint
    assert result.strategy_version == "1"
    assert tuple(item.seed_index for item in result.assignments) == (0, 1, 2, 3)
    assert result.assignment_for_cell(2) is result.assignments[2]


def test_assignment_rejects_invalid_inputs_and_unknown_lookup() -> None:
    partition = make_partition((1.0,))
    config = make_config(residential=1.0, mixed=0.0, public=0.0, recreation=0.0)
    assigner = SuitabilityTargetShareAssigner()

    with pytest.raises(ZoneAssignmentError, match="partition"):
        assigner.assign(partition=object(), config=config)  # type: ignore[arg-type]
    with pytest.raises(ZoneAssignmentError, match="config"):
        assigner.assign(partition=partition, config=object())  # type: ignore[arg-type]

    result = assigner.assign(partition=partition, config=config)
    with pytest.raises(ZoneAssignmentError, match="unknown partition cell index"):
        result.assignment_for_cell(1)
