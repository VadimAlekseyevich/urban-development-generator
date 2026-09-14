from __future__ import annotations

import math

import pytest
from shapely.geometry import box
from shapely.ops import unary_union

from core.urban_generator.zoning import (
    DeterministicZoneRegionRefiner,
    ZoneAdjacencyPolicy,
    ZoneAdjacencyRule,
    ZoneAssignment,
    ZoneAssignmentResult,
    ZoneClass,
    ZoneClassConfig,
    ZoneRefinementError,
    ZoneRefinementTermination,
    ZoneShareDiagnostic,
    ZoningConfig,
    ZoningPartitionCell,
    ZoningPartitionResult,
    ZoningSeed,
)


def make_config(
    *,
    residential_share: float = 0.5,
    mixed_share: float = 0.5,
    public_share: float = 0.0,
    recreation_share: float = 0.0,
    residential_min: float = 1.0,
    mixed_min: float = 1.0,
    public_min: float = 1.0,
    recreation_min: float = 1.0,
    adjacency_rules: tuple[ZoneAdjacencyRule, ...] = (),
    version: str = "refinement-test-v1",
) -> ZoningConfig:
    return ZoningConfig(
        version=version,
        zones=(
            ZoneClassConfig(ZoneClass.RESIDENTIAL, residential_share, residential_min),
            ZoneClassConfig(ZoneClass.MIXED, mixed_share, mixed_min),
            ZoneClassConfig(ZoneClass.PUBLIC, public_share, public_min),
            ZoneClassConfig(ZoneClass.RECREATION, recreation_share, recreation_min),
        ),
        adjacency_rules=adjacency_rules,
    )


def make_partition_from_geometries(
    geometries: tuple[object, ...],
    *,
    scores: tuple[float, ...] | None = None,
) -> ZoningPartitionResult:
    if scores is None:
        scores = tuple(0.5 for _geometry in geometries)
    assert len(scores) == len(geometries)

    cells: list[ZoningPartitionCell] = []
    for index, (geometry, score) in enumerate(zip(geometries, scores, strict=True)):
        centroid = geometry.centroid  # type: ignore[attr-defined]
        seed = ZoningSeed(
            row=0,
            col=index,
            x_m=float(centroid.x),
            y_m=float(centroid.y),
            suitability_score=score,
        )
        cells.append(
            ZoningPartitionCell(
                seed_index=index,
                seed=seed,
                geometry=geometry,  # type: ignore[arg-type]
                area_m2=float(geometry.area),  # type: ignore[attr-defined]
                validity_repaired=False,
            )
        )

    developable = unary_union(geometries)
    area_m2 = float(developable.area)
    return ZoningPartitionResult(
        working_srid=32637,
        developable_area=developable,
        cells=tuple(cells),
        developable_area_m2=area_m2,
        covered_area_m2=area_m2,
        uncovered_area_m2=0.0,
        overlap_area_m2=0.0,
        developable_validity_repaired=False,
        repaired_cell_count=0,
    )


def make_line_partition(count: int) -> ZoningPartitionResult:
    return make_partition_from_geometries(
        tuple(box(float(index), 0.0, float(index + 1), 1.0) for index in range(count)),
        scores=tuple(0.1 + index * 0.1 for index in range(count)),
    )


def make_assignment(
    *,
    partition: ZoningPartitionResult,
    config: ZoningConfig,
    labels: tuple[ZoneClass, ...],
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
    assigned_area = {zone_class: 0.0 for zone_class in ZoneClass}
    counts = {zone_class: 0 for zone_class in ZoneClass}
    for item in assignments:
        assigned_area[item.zone_class] += item.area_m2
        counts[item.zone_class] += 1
    total = partition.developable_area_m2
    shares = tuple(
        ZoneShareDiagnostic(
            zone_class=zone.zone_class,
            target_share=zone.target_share,
            target_area_m2=total * zone.target_share,
            assigned_area_m2=assigned_area[zone.zone_class],
            achieved_share=assigned_area[zone.zone_class] / total,
            absolute_area_error_m2=abs(
                assigned_area[zone.zone_class] - total * zone.target_share
            ),
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


def forbidden_residential_recreation() -> tuple[ZoneAdjacencyRule, ...]:
    return (
        ZoneAdjacencyRule(
            ZoneClass.RESIDENTIAL,
            ZoneClass.RECREATION,
            ZoneAdjacencyPolicy.FORBIDDEN,
        ),
    )


def test_refinement_removes_forbidden_adjacency_with_deterministic_relabel() -> None:
    partition = make_line_partition(3)
    config = make_config(
        residential_share=2.0 / 3.0,
        mixed_share=0.0,
        recreation_share=1.0 / 3.0,
        adjacency_rules=forbidden_residential_recreation(),
    )
    assignment = make_assignment(
        partition=partition,
        config=config,
        labels=(
            ZoneClass.RESIDENTIAL,
            ZoneClass.RECREATION,
            ZoneClass.RESIDENTIAL,
        ),
    )

    result = DeterministicZoneRegionRefiner().refine(
        partition=partition,
        assignment=assignment,
        config=config,
    )

    assert tuple(item.zone_class for item in result.assignments) == (
        ZoneClass.RESIDENTIAL,
        ZoneClass.RESIDENTIAL,
        ZoneClass.RESIDENTIAL,
    )
    assert result.diagnostics.converged
    assert result.diagnostics.termination is ZoneRefinementTermination.CONVERGED
    assert result.diagnostics.iterations == 1
    assert result.diagnostics.initial_metrics.forbidden_adjacency_count == 2
    assert result.diagnostics.final_metrics.forbidden_adjacency_count == 0
    assert result.diagnostics.moves[0].cell_index == 1


def test_refinement_grows_under_minimum_region_when_it_best_preserves_targets() -> None:
    partition = make_line_partition(3)
    config = make_config(
        residential_share=2.0 / 3.0,
        mixed_share=1.0 / 3.0,
        residential_min=2.0,
    )
    assignment = make_assignment(
        partition=partition,
        config=config,
        labels=(ZoneClass.RESIDENTIAL, ZoneClass.MIXED, ZoneClass.MIXED),
    )

    result = DeterministicZoneRegionRefiner().refine(
        partition=partition,
        assignment=assignment,
        config=config,
    )

    assert tuple(item.zone_class for item in result.assignments) == (
        ZoneClass.RESIDENTIAL,
        ZoneClass.RESIDENTIAL,
        ZoneClass.MIXED,
    )
    assert result.diagnostics.initial_metrics.under_minimum_region_count == 1
    assert result.diagnostics.final_metrics.under_minimum_region_count == 0
    assert result.diagnostics.final_metrics.absolute_target_error_m2 == pytest.approx(0.0)
    residential_region = next(
        region for region in result.regions if region.zone_class is ZoneClass.RESIDENTIAL
    )
    assert residential_region.cell_indices == (0, 1)
    assert residential_region.area_m2 == pytest.approx(2.0)
    assert residential_region.meets_minimum_area


def test_point_contact_does_not_count_as_zone_adjacency() -> None:
    partition = make_partition_from_geometries(
        (box(0.0, 0.0, 1.0, 1.0), box(1.0, 1.0, 2.0, 2.0))
    )
    config = make_config(
        residential_share=0.5,
        mixed_share=0.0,
        recreation_share=0.5,
        adjacency_rules=forbidden_residential_recreation(),
    )
    assignment = make_assignment(
        partition=partition,
        config=config,
        labels=(ZoneClass.RESIDENTIAL, ZoneClass.RECREATION),
    )

    result = DeterministicZoneRegionRefiner().refine(
        partition=partition,
        assignment=assignment,
        config=config,
    )

    assert result.diagnostics.converged
    assert result.diagnostics.iterations == 0
    assert result.diagnostics.final_metrics.forbidden_adjacency_count == 0


def test_refinement_reports_max_iteration_termination_when_bound_is_hit() -> None:
    partition = make_line_partition(5)
    config = make_config(
        residential_share=0.6,
        mixed_share=0.0,
        recreation_share=0.4,
        adjacency_rules=forbidden_residential_recreation(),
    )
    assignment = make_assignment(
        partition=partition,
        config=config,
        labels=(
            ZoneClass.RESIDENTIAL,
            ZoneClass.RECREATION,
            ZoneClass.RESIDENTIAL,
            ZoneClass.RECREATION,
            ZoneClass.RESIDENTIAL,
        ),
    )

    result = DeterministicZoneRegionRefiner().refine(
        partition=partition,
        assignment=assignment,
        config=config,
        max_iterations=1,
    )

    assert not result.diagnostics.converged
    assert result.diagnostics.termination is ZoneRefinementTermination.MAX_ITERATIONS
    assert result.diagnostics.iterations == 1
    assert result.diagnostics.final_metrics.forbidden_adjacency_count > 0


def test_refinement_reports_stalled_when_isolated_region_cannot_grow_or_merge() -> None:
    partition = make_partition_from_geometries(
        (box(0.0, 0.0, 1.0, 1.0), box(3.0, 0.0, 4.0, 1.0))
    )
    config = make_config(
        residential_share=0.5,
        mixed_share=0.5,
        residential_min=2.0,
    )
    assignment = make_assignment(
        partition=partition,
        config=config,
        labels=(ZoneClass.RESIDENTIAL, ZoneClass.MIXED),
    )

    result = DeterministicZoneRegionRefiner().refine(
        partition=partition,
        assignment=assignment,
        config=config,
    )

    assert not result.diagnostics.converged
    assert result.diagnostics.termination is ZoneRefinementTermination.STALLED
    assert result.diagnostics.iterations == 0
    assert result.diagnostics.final_metrics.under_minimum_region_count == 1


def test_soft_adjacency_policies_are_reported_without_becoming_hard_failures() -> None:
    partition = make_line_partition(3)
    config = make_config(
        residential_share=1.0 / 3.0,
        mixed_share=1.0 / 3.0,
        public_share=1.0 / 3.0,
        adjacency_rules=(
            ZoneAdjacencyRule(
                ZoneClass.RESIDENTIAL,
                ZoneClass.MIXED,
                ZoneAdjacencyPolicy.PREFERRED,
            ),
            ZoneAdjacencyRule(
                ZoneClass.MIXED,
                ZoneClass.PUBLIC,
                ZoneAdjacencyPolicy.DISCOURAGED,
            ),
        ),
    )
    assignment = make_assignment(
        partition=partition,
        config=config,
        labels=(ZoneClass.RESIDENTIAL, ZoneClass.MIXED, ZoneClass.PUBLIC),
    )

    result = DeterministicZoneRegionRefiner().refine(
        partition=partition,
        assignment=assignment,
        config=config,
    )

    metrics = result.diagnostics.final_metrics
    assert result.diagnostics.converged
    assert result.diagnostics.iterations == 0
    assert metrics.preferred_adjacency_count == 1
    assert metrics.discouraged_adjacency_count == 1
    assert metrics.hard_violation_count == 0


def test_refinement_is_deterministic_and_never_mutates_partition_geometry() -> None:
    partition = make_line_partition(3)
    geometry_wkb = tuple(cell.geometry.wkb for cell in partition.cells)
    config = make_config(
        residential_share=2.0 / 3.0,
        mixed_share=1.0 / 3.0,
        residential_min=2.0,
    )
    assignment = make_assignment(
        partition=partition,
        config=config,
        labels=(ZoneClass.RESIDENTIAL, ZoneClass.MIXED, ZoneClass.MIXED),
    )
    refiner = DeterministicZoneRegionRefiner()

    first = refiner.refine(partition=partition, assignment=assignment, config=config)
    second = refiner.refine(partition=partition, assignment=assignment, config=config)

    assert first == second
    assert tuple(cell.geometry.wkb for cell in partition.cells) == geometry_wkb
    assert not hasattr(first.assignments[0], "geometry")
    assert first.input_assignment_strategy_version == "test-assignment-v1"
    assert first.refinement_version == "1"


def test_refinement_validates_config_provenance_and_iteration_bound() -> None:
    partition = make_line_partition(2)
    config = make_config()
    assignment = make_assignment(
        partition=partition,
        config=config,
        labels=(ZoneClass.RESIDENTIAL, ZoneClass.MIXED),
    )
    refiner = DeterministicZoneRegionRefiner()

    mismatched = make_config(version="refinement-test-v2")
    with pytest.raises(ZoneRefinementError, match="version"):
        refiner.refine(partition=partition, assignment=assignment, config=mismatched)
    with pytest.raises(ZoneRefinementError, match="max_iterations"):
        refiner.refine(
            partition=partition,
            assignment=assignment,
            config=config,
            max_iterations=0,
        )
    with pytest.raises(ZoneRefinementError, match="max_iterations"):
        refiner.refine(
            partition=partition,
            assignment=assignment,
            config=config,
            max_iterations=10_001,
        )


def test_refinement_share_diagnostics_match_final_labels() -> None:
    partition = make_line_partition(3)
    config = make_config(
        residential_share=2.0 / 3.0,
        mixed_share=1.0 / 3.0,
        residential_min=2.0,
    )
    assignment = make_assignment(
        partition=partition,
        config=config,
        labels=(ZoneClass.RESIDENTIAL, ZoneClass.MIXED, ZoneClass.MIXED),
    )

    result = DeterministicZoneRegionRefiner().refine(
        partition=partition,
        assignment=assignment,
        config=config,
    )

    assert result.share(ZoneClass.RESIDENTIAL).assigned_area_m2 == pytest.approx(2.0)
    assert result.share(ZoneClass.MIXED).assigned_area_m2 == pytest.approx(1.0)
    assert math.fsum(item.assigned_area_m2 for item in result.shares) == pytest.approx(3.0)
