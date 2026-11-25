from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.strtree import STRtree

from core.urban_generator.zoning.assignment import (
    ZoneAssignment,
    ZoneAssignmentResult,
    ZoneShareDiagnostic,
)
from core.urban_generator.zoning.config import (
    ZoneAdjacencyPolicy,
    ZoneClass,
    ZoningConfig,
)
from core.urban_generator.zoning.partition import ZoningPartitionResult

_MIN_SHARED_BOUNDARY_M = 1e-9


class ZoneRefinementError(ValueError):
    """Raised when bounded zoning refinement inputs violate the core contract."""


@dataclass(frozen=True, slots=True, order=True)
class ZoneRefinementObjective:
    """Lexicographic refinement objective; lower is always better."""

    forbidden_adjacency_count: int
    undersized_region_count: int
    undersized_area_shortfall_m2: float
    discouraged_adjacency_count: int
    negative_preferred_adjacency_count: int
    target_area_error_m2: float

    def __post_init__(self) -> None:
        for field_name in (
            "forbidden_adjacency_count",
            "undersized_region_count",
            "discouraged_adjacency_count",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ZoneRefinementError(f"{field_name} must be a non-negative integer")
        if (
            isinstance(self.negative_preferred_adjacency_count, bool)
            or not isinstance(self.negative_preferred_adjacency_count, int)
            or self.negative_preferred_adjacency_count > 0
        ):
            raise ZoneRefinementError(
                "negative_preferred_adjacency_count must be a non-positive integer"
            )
        _require_non_negative_finite(
            "undersized_area_shortfall_m2",
            self.undersized_area_shortfall_m2,
        )
        _require_non_negative_finite("target_area_error_m2", self.target_area_error_m2)

    @property
    def preferred_adjacency_count(self) -> int:
        return -self.negative_preferred_adjacency_count


@dataclass(frozen=True, slots=True)
class ZoneRefinementResult:
    """Refined labels plus bounded-iteration convergence diagnostics."""

    assignment: ZoneAssignmentResult
    initial_objective: ZoneRefinementObjective
    final_objective: ZoneRefinementObjective
    iterations: int
    max_iterations: int
    converged: bool
    stop_reason: str
    changed_cell_count: int
    adjacency_edge_count: int
    refinement_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.assignment, ZoneAssignmentResult):
            raise ZoneRefinementError("assignment must be a ZoneAssignmentResult")
        if not isinstance(self.initial_objective, ZoneRefinementObjective):
            raise ZoneRefinementError("initial_objective must be a ZoneRefinementObjective")
        if not isinstance(self.final_objective, ZoneRefinementObjective):
            raise ZoneRefinementError("final_objective must be a ZoneRefinementObjective")
        _require_non_negative_int("iterations", self.iterations)
        _require_positive_int("max_iterations", self.max_iterations)
        if self.iterations > self.max_iterations:
            raise ZoneRefinementError("iterations cannot exceed max_iterations")
        if not isinstance(self.converged, bool):
            raise ZoneRefinementError("converged must be boolean")
        if self.stop_reason not in {"stable", "max_iterations"}:
            raise ZoneRefinementError("stop_reason must be 'stable' or 'max_iterations'")
        _require_non_negative_int("changed_cell_count", self.changed_cell_count)
        if self.changed_cell_count > len(self.assignment.assignments):
            raise ZoneRefinementError("changed_cell_count cannot exceed assignment size")
        _require_non_negative_int("adjacency_edge_count", self.adjacency_edge_count)
        if not isinstance(self.refinement_version, str) or not self.refinement_version:
            raise ZoneRefinementError("refinement_version must be a non-empty string")
        if self.final_objective > self.initial_objective:
            raise ZoneRefinementError("refinement objective must never get worse")
        if self.converged != (self.stop_reason == "stable"):
            raise ZoneRefinementError("converged must agree with stop_reason")


class BoundedRegionRefiner:
    """Grow/refine zone regions by deterministic single-cell label moves."""

    version = "1"

    def refine(
        self,
        *,
        partition: ZoningPartitionResult,
        assignment: ZoneAssignmentResult,
        config: ZoningConfig,
        max_iterations: int = 100,
    ) -> ZoneRefinementResult:
        if not isinstance(partition, ZoningPartitionResult):
            raise ZoneRefinementError("partition must be a ZoningPartitionResult")
        if not isinstance(assignment, ZoneAssignmentResult):
            raise ZoneRefinementError("assignment must be a ZoneAssignmentResult")
        if not isinstance(config, ZoningConfig):
            raise ZoneRefinementError("config must be a ZoningConfig")
        _require_positive_int("max_iterations", max_iterations)
        if len(partition.cells) != len(assignment.assignments):
            raise ZoneRefinementError("partition and assignment must contain the same cells")
        if assignment.zoning_config_fingerprint != config.fingerprint:
            raise ZoneRefinementError(
                "assignment zoning_config_fingerprint must match refinement config"
            )
        for cell_index, cell in enumerate(partition.cells):
            item = assignment.assignments[cell_index]
            if item.cell_index != cell_index or item.seed_index != cell.seed_index:
                raise ZoneRefinementError("assignment cell/seed references must match partition")
            if not math.isclose(
                item.area_m2,
                cell.area_m2,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise ZoneRefinementError("assignment area must match partition cell area")

        edges, neighbors = _build_adjacency(partition)
        labels = [item.zone_class for item in assignment.assignments]
        initial_labels = tuple(labels)
        initial_objective = _objective(
            partition=partition,
            labels=tuple(labels),
            config=config,
            edges=edges,
            neighbors=neighbors,
        )
        current_objective = initial_objective
        canonical_order = {zone_class: index for index, zone_class in enumerate(ZoneClass)}
        iterations = 0
        converged = False

        while iterations < max_iterations:
            best_move: tuple[
                ZoneRefinementObjective,
                int,
                int,
                ZoneClass,
            ] | None = None
            for cell_index in range(len(labels)):
                current_class = labels[cell_index]
                candidate_classes = sorted(
                    {
                        labels[index]
                        for index in neighbors[cell_index]
                        if labels[index] is not current_class
                    },
                    key=lambda zone_class: canonical_order[zone_class],
                )
                for candidate_class in candidate_classes:
                    candidate_labels = list(labels)
                    candidate_labels[cell_index] = candidate_class
                    candidate_objective = _objective(
                        partition=partition,
                        labels=tuple(candidate_labels),
                        config=config,
                        edges=edges,
                        neighbors=neighbors,
                    )
                    if candidate_objective >= current_objective:
                        continue
                    move = (
                        candidate_objective,
                        cell_index,
                        canonical_order[candidate_class],
                        candidate_class,
                    )
                    if best_move is None or move[:3] < best_move[:3]:
                        best_move = move

            if best_move is None:
                converged = True
                break

            current_objective, cell_index, _class_order, candidate_class = best_move
            labels[cell_index] = candidate_class
            iterations += 1

        stop_reason = "stable" if converged else "max_iterations"
        refined_assignment = _rebuild_assignment(
            partition=partition,
            original=assignment,
            labels=tuple(labels),
            config=config,
        )
        return ZoneRefinementResult(
            assignment=refined_assignment,
            initial_objective=initial_objective,
            final_objective=current_objective,
            iterations=iterations,
            max_iterations=max_iterations,
            converged=converged,
            stop_reason=stop_reason,
            changed_cell_count=sum(
                before is not after for before, after in zip(initial_labels, labels, strict=True)
            ),
            adjacency_edge_count=len(edges),
            refinement_version=self.version,
        )


def _build_adjacency(
    partition: ZoningPartitionResult,
) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, ...], ...]]:
    geometries = tuple(cell.geometry for cell in partition.cells)
    tree = STRtree(geometries)
    edge_list: list[tuple[int, int]] = []
    neighbor_sets = [set[int]() for _cell in geometries]
    for first_index, geometry in enumerate(geometries):
        for candidate in tree.query(geometry):
            second_index = int(candidate)
            if second_index <= first_index:
                continue
            shared_boundary = geometry.boundary.intersection(
                geometries[second_index].boundary
            )
            if shared_boundary.length <= _MIN_SHARED_BOUNDARY_M:
                continue
            edge_list.append((first_index, second_index))
            neighbor_sets[first_index].add(second_index)
            neighbor_sets[second_index].add(first_index)
    edges = tuple(sorted(edge_list))
    neighbors = tuple(tuple(sorted(items)) for items in neighbor_sets)
    return edges, neighbors


def _objective(
    *,
    partition: ZoningPartitionResult,
    labels: tuple[ZoneClass, ...],
    config: ZoningConfig,
    edges: tuple[tuple[int, int], ...],
    neighbors: tuple[tuple[int, ...], ...],
) -> ZoneRefinementObjective:
    forbidden = 0
    discouraged = 0
    preferred = 0
    for first_index, second_index in edges:
        first = labels[first_index]
        second = labels[second_index]
        if first is second:
            continue
        policy = config.adjacency_policy(first, second)
        if policy is ZoneAdjacencyPolicy.FORBIDDEN:
            forbidden += 1
        elif policy is ZoneAdjacencyPolicy.DISCOURAGED:
            discouraged += 1
        elif policy is ZoneAdjacencyPolicy.PREFERRED:
            preferred += 1

    undersized_count = 0
    undersized_shortfall = 0.0
    for zone_class, region_cells in _regions(labels=labels, neighbors=neighbors):
        area_m2 = math.fsum(partition.cells[index].area_m2 for index in region_cells)
        minimum_area_m2 = config.zone(zone_class).minimum_area_m2
        if area_m2 < minimum_area_m2:
            undersized_count += 1
            undersized_shortfall += minimum_area_m2 - area_m2

    assigned_area = {zone_class: 0.0 for zone_class in ZoneClass}
    for index, zone_class in enumerate(labels):
        assigned_area[zone_class] += partition.cells[index].area_m2
    target_error = math.fsum(
        abs(
            assigned_area[zone.zone_class]
            - partition.developable_area_m2 * zone.target_share
        )
        for zone in config.zones
    )
    return ZoneRefinementObjective(
        forbidden_adjacency_count=forbidden,
        undersized_region_count=undersized_count,
        undersized_area_shortfall_m2=undersized_shortfall,
        discouraged_adjacency_count=discouraged,
        negative_preferred_adjacency_count=-preferred,
        target_area_error_m2=target_error,
    )


def _regions(
    *,
    labels: tuple[ZoneClass, ...],
    neighbors: tuple[tuple[int, ...], ...],
) -> tuple[tuple[ZoneClass, tuple[int, ...]], ...]:
    seen: set[int] = set()
    regions: list[tuple[ZoneClass, tuple[int, ...]]] = []
    for start_index, zone_class in enumerate(labels):
        if start_index in seen:
            continue
        stack = [start_index]
        seen.add(start_index)
        region: list[int] = []
        while stack:
            cell_index = stack.pop()
            region.append(cell_index)
            for neighbor_index in reversed(neighbors[cell_index]):
                if neighbor_index in seen or labels[neighbor_index] is not zone_class:
                    continue
                seen.add(neighbor_index)
                stack.append(neighbor_index)
        regions.append((zone_class, tuple(sorted(region))))
    return tuple(regions)


def _rebuild_assignment(
    *,
    partition: ZoningPartitionResult,
    original: ZoneAssignmentResult,
    labels: tuple[ZoneClass, ...],
    config: ZoningConfig,
) -> ZoneAssignmentResult:
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
    total_area_m2 = partition.developable_area_m2
    shares = tuple(
        ZoneShareDiagnostic(
            zone_class=zone.zone_class,
            target_share=zone.target_share,
            target_area_m2=total_area_m2 * zone.target_share,
            assigned_area_m2=assigned_area[zone.zone_class],
            achieved_share=assigned_area[zone.zone_class] / total_area_m2,
            absolute_area_error_m2=abs(
                assigned_area[zone.zone_class] - total_area_m2 * zone.target_share
            ),
            cell_count=counts[zone.zone_class],
        )
        for zone in config.zones
    )
    return ZoneAssignmentResult(
        assignments=assignments,
        shares=shares,
        total_area_m2=total_area_m2,
        zoning_config_version=config.version,
        zoning_config_fingerprint=config.fingerprint,
        strategy_version=original.strategy_version,
    )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ZoneRefinementError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ZoneRefinementError(f"{field_name} must be a non-negative integer")


def _require_non_negative_finite(field_name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ZoneRefinementError(f"{field_name} must be a finite non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ZoneRefinementError(f"{field_name} must be a finite non-negative number")
