from __future__ import annotations

import pytest
from shapely.geometry import MultiPolygon, box

from core.urban_generator.zoning import (
    BoundedRegionRefiner,
    ZoneAdjacencyPolicy,
    ZoneAdjacencyRule,
    ZoneAssignment,
    ZoneAssignmentResult,
    ZoneClass,
    ZoneClassConfig,
    ZoneRefinementError,
    ZoneShareDiagnostic,
    ZoningConfig,
    ZoningPartitionCell,
    ZoningPartitionResult,
    ZoningSeed,
)


def make_config(
    *,
    shares: tuple[float, float, float, float],
    minimums: tuple[float, float, float, float],
    rules: tuple[ZoneAdjacencyRule, ...] = (),
    version: str = "refinement-test-v1",
) -> ZoningConfig:
    return ZoningConfig(
        version=version,
        zones=tuple(
            ZoneClassConfig(zone_class, share, minimum)
            for zone_class, share, minimum in zip(
                ZoneClass,
                shares,
                minimums,
                strict=True,
            )
        ),
        adjacency_rules=rules,
    )


def make_linear_partition(count: int) -> ZoningPartitionResult:
    cells: list[ZoningPartitionCell] = []
    for index in range(count):
        geometry = box(float(index), 0.0, float(index + 1), 1.0)
        seed = ZoningSeed(
            row=0,
            col=index,
            x_m=index + 0.5,
            y_m=0.5,
            suitability_score=1.0 - index / max(count, 1) / 2.0,
        )
        cells.append(
            ZoningPartitionCell(
                seed_index=index,
                seed=seed,
                geometry=geometry,
                area_m2=1.0,
                validity_repaired=False,
            )
        )
    developable = box(0.0, 0.0, float(count), 1.0)
    return ZoningPartitionResult(
        working_srid=32637,
        developable_area=developable,
        cells=tuple(cells),
        developable_area_m2=float(count),
        covered_area_m2=float(count),
        uncovered_area_m2=0.0,
        overlap_area_m2=0.0,
        developable_validity_repaired=False,
        repaired_cell_count=0,
    )


def make_assignment(
    partition: ZoningPartitionResult,
    labels: tuple[ZoneClass, ...],
    config: ZoningConfig,
) -> ZoneAssignmentResult:
    assert len(labels) == len(partition.cells)
    assignments = tuple(
        ZoneAssignment(
            cell_index=index,
            seed_index=cell.seed_index,
            zone_class=labels[index],
            area_m2=cell.area_m2,
            suitability_score=cell.seed.suitability_score,
        )
        for index, cell in enumerate(partition.cells)
    )
    assigned = {zone_class: 0.0 for zone_class in ZoneClass}
    counts = {zone_class: 0 for zone_class in ZoneClass}
    for item in assignments:
        assigned[item.zone_class] += item.area_m2
        counts[item.zone_class] += 1
    total = partition.developable_area_m2
    shares = tuple(
        ZoneShareDiagnostic(
            zone_class=zone.zone_class,
            target_share=zone.target_share,
            target_area_m2=total * zone.target_share,
            assigned_area_m2=assigned[zone.zone_class],
            achieved_share=assigned[zone.zone_class] / total,
            absolute_area_error_m2=abs(assigned[zone.zone_class] - total * zone.target_share),
            cell_count=counts[zone.zone_class],
        )
        for zone in config.zones
    )
    return ZoneAssignmentResult(
        assignments=assignments,
        shares=shares,
        total_area_m2=total,
        zoning_config_version=config.version,
        zoning_config_fingerprint=config.fingerprint,
        strategy_version="test-assignment-v1",
    )


def labels(result: object) -> tuple[ZoneClass, ...]:
    return tuple(item.zone_class for item in result.assignment.assignments)  # type: ignore[attr-defined]


def test_refinement_grows_neighbor_region_to_remove_undersized_island() -> None:
    partition = make_linear_partition(4)
    config = make_config(
        shares=(0.5, 0.5, 0.0, 0.0),
        minimums=(2.0, 1.0, 1.0, 1.0),
    )
    assignment = make_assignment(
        partition,
        (
            ZoneClass.RESIDENTIAL,
            ZoneClass.MIXED,
            ZoneClass.RESIDENTIAL,
            ZoneClass.RESIDENTIAL,
        ),
        config,
    )

    result = BoundedRegionRefiner().refine(
        partition=partition,
        assignment=assignment,
        config=config,
    )

    assert labels(result) == (
        ZoneClass.MIXED,
        ZoneClass.MIXED,
        ZoneClass.RESIDENTIAL,
        ZoneClass.RESIDENTIAL,
    )
    assert result.initial_objective.undersized_region_count == 1
    assert result.final_objective.undersized_region_count == 0
    assert result.assignment.absolute_target_error_m2 == pytest.approx(0.0)
    assert result.converged
    assert result.stop_reason == "stable"
    assert result.changed_cell_count == 1


def test_forbidden_adjacency_has_priority_over_target_share_error() -> None:
    partition = make_linear_partition(2)
    config = make_config(
        shares=(0.5, 0.5, 0.0, 0.0),
        minimums=(0.5, 0.5, 0.5, 0.5),
        rules=(
            ZoneAdjacencyRule(
                ZoneClass.RESIDENTIAL,
                ZoneClass.MIXED,
                ZoneAdjacencyPolicy.FORBIDDEN,
            ),
        ),
    )
    assignment = make_assignment(
        partition,
        (ZoneClass.RESIDENTIAL, ZoneClass.MIXED),
        config,
    )

    result = BoundedRegionRefiner().refine(
        partition=partition,
        assignment=assignment,
        config=config,
    )

    assert result.initial_objective.forbidden_adjacency_count == 1
    assert result.final_objective.forbidden_adjacency_count == 0
    assert result.assignment.absolute_target_error_m2 > assignment.absolute_target_error_m2
    assert labels(result) == (ZoneClass.MIXED, ZoneClass.MIXED)


def test_stable_assignment_is_a_deterministic_noop() -> None:
    partition = make_linear_partition(3)
    config = make_config(
        shares=(1.0, 0.0, 0.0, 0.0),
        minimums=(1.0, 1.0, 1.0, 1.0),
    )
    assignment = make_assignment(
        partition,
        (ZoneClass.RESIDENTIAL,) * 3,
        config,
    )
    refiner = BoundedRegionRefiner()

    first = refiner.refine(partition=partition, assignment=assignment, config=config)
    second = refiner.refine(partition=partition, assignment=assignment, config=config)

    assert first == second
    assert first.assignment == assignment
    assert first.iterations == 0
    assert first.changed_cell_count == 0
    assert first.converged
    assert first.initial_objective == first.final_objective


def test_max_iterations_is_reported_without_false_convergence() -> None:
    partition = make_linear_partition(4)
    config = make_config(
        shares=(0.25, 0.25, 0.25, 0.25),
        minimums=(2.0, 2.0, 2.0, 2.0),
    )
    assignment = make_assignment(partition, tuple(ZoneClass), config)

    result = BoundedRegionRefiner().refine(
        partition=partition,
        assignment=assignment,
        config=config,
        max_iterations=1,
    )

    assert result.iterations == 1
    assert not result.converged
    assert result.stop_reason == "max_iterations"
    assert result.final_objective < result.initial_objective


def test_point_touch_does_not_create_region_adjacency() -> None:
    first_geometry = box(0.0, 0.0, 1.0, 1.0)
    second_geometry = box(1.0, 1.0, 2.0, 2.0)
    geometries = (first_geometry, second_geometry)
    cells = tuple(
        ZoningPartitionCell(
            seed_index=index,
            seed=ZoningSeed(
                row=0,
                col=index,
                x_m=geometry.centroid.x,
                y_m=geometry.centroid.y,
                suitability_score=0.5,
            ),
            geometry=geometry,
            area_m2=1.0,
            validity_repaired=False,
        )
        for index, geometry in enumerate(geometries)
    )
    developable = MultiPolygon(geometries)
    partition = ZoningPartitionResult(
        working_srid=32637,
        developable_area=developable,
        cells=cells,
        developable_area_m2=2.0,
        covered_area_m2=2.0,
        uncovered_area_m2=0.0,
        overlap_area_m2=0.0,
        developable_validity_repaired=False,
        repaired_cell_count=0,
    )
    config = make_config(
        shares=(0.5, 0.5, 0.0, 0.0),
        minimums=(0.5, 0.5, 0.5, 0.5),
    )
    assignment = make_assignment(
        partition,
        (ZoneClass.RESIDENTIAL, ZoneClass.MIXED),
        config,
    )

    result = BoundedRegionRefiner().refine(
        partition=partition,
        assignment=assignment,
        config=config,
    )

    assert result.adjacency_edge_count == 0
    assert result.assignment == assignment


def test_refinement_validates_limits_config_and_partition_alignment() -> None:
    partition = make_linear_partition(2)
    config = make_config(
        shares=(0.5, 0.5, 0.0, 0.0),
        minimums=(1.0, 1.0, 1.0, 1.0),
    )
    assignment = make_assignment(
        partition,
        (ZoneClass.RESIDENTIAL, ZoneClass.MIXED),
        config,
    )
    refiner = BoundedRegionRefiner()

    with pytest.raises(ZoneRefinementError, match="max_iterations"):
        refiner.refine(
            partition=partition,
            assignment=assignment,
            config=config,
            max_iterations=0,
        )

    different_config = make_config(
        shares=(1.0, 0.0, 0.0, 0.0),
        minimums=(1.0, 1.0, 1.0, 1.0),
        version="different-v1",
    )
    with pytest.raises(ZoneRefinementError, match="fingerprint"):
        refiner.refine(
            partition=partition,
            assignment=assignment,
            config=different_config,
        )
