from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

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

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_AREA_TOLERANCE_RATIO = 1e-9
_MIN_AREA_TOLERANCE_M2 = 1e-6
_MIN_SHARED_BOUNDARY_M = 1e-9
_MAX_ITERATION_LIMIT = 10_000
_ZONE_ORDER = {zone_class: index for index, zone_class in enumerate(ZoneClass)}
_ObjectiveKey = tuple[int, int, float, float, int, int]
_OrderingKey = tuple[_ObjectiveKey, float, float, int, int]


class ZoneRefinementError(ValueError):
    """Raised when zoning region refinement violates its deterministic contract."""


class ZoneRefinementTermination(StrEnum):
    """Why the bounded refinement loop stopped."""

    CONVERGED = "CONVERGED"
    STALLED = "STALLED"
    MAX_ITERATIONS = "MAX_ITERATIONS"


@dataclass(frozen=True, slots=True)
class ZoneRegionDiagnostic:
    """One connected same-class region over immutable partition cells."""

    region_index: int
    zone_class: ZoneClass
    cell_indices: tuple[int, ...]
    area_m2: float
    minimum_area_m2: float
    meets_minimum_area: bool

    def __post_init__(self) -> None:
        _require_non_negative_int("region_index", self.region_index)
        if not isinstance(self.zone_class, ZoneClass):
            raise ZoneRefinementError("region zone_class must be a ZoneClass value")
        if not isinstance(self.cell_indices, tuple) or not self.cell_indices:
            raise ZoneRefinementError(
                "region cell_indices must be a non-empty immutable tuple"
            )
        invalid_index = any(
            isinstance(index, bool) or not isinstance(index, int) or index < 0
            for index in self.cell_indices
        )
        if invalid_index:
            raise ZoneRefinementError(
                "region cell_indices must contain non-negative integers"
            )
        if tuple(sorted(set(self.cell_indices))) != self.cell_indices:
            raise ZoneRefinementError("region cell_indices must be unique and sorted")
        _require_positive_finite("area_m2", self.area_m2)
        _require_positive_finite("minimum_area_m2", self.minimum_area_m2)
        if not isinstance(self.meets_minimum_area, bool):
            raise ZoneRefinementError("meets_minimum_area must be boolean")

    @property
    def minimum_area_deficit_m2(self) -> float:
        return max(0.0, self.minimum_area_m2 - self.area_m2)


@dataclass(frozen=True, slots=True)
class ZoneRefinementMetrics:
    """Comparable convergence metrics for one refinement state."""

    region_count: int
    forbidden_adjacency_count: int
    discouraged_adjacency_count: int
    preferred_adjacency_count: int
    under_minimum_region_count: int
    under_minimum_area_deficit_m2: float
    absolute_target_error_m2: float

    def __post_init__(self) -> None:
        _require_non_negative_int("region_count", self.region_count)
        _require_non_negative_int(
            "forbidden_adjacency_count",
            self.forbidden_adjacency_count,
        )
        _require_non_negative_int(
            "discouraged_adjacency_count",
            self.discouraged_adjacency_count,
        )
        _require_non_negative_int(
            "preferred_adjacency_count",
            self.preferred_adjacency_count,
        )
        _require_non_negative_int(
            "under_minimum_region_count",
            self.under_minimum_region_count,
        )
        _require_non_negative_finite(
            "under_minimum_area_deficit_m2",
            self.under_minimum_area_deficit_m2,
        )
        _require_non_negative_finite(
            "absolute_target_error_m2",
            self.absolute_target_error_m2,
        )

    @property
    def hard_violation_count(self) -> int:
        return self.forbidden_adjacency_count + self.under_minimum_region_count


@dataclass(frozen=True, slots=True)
class ZoneRefinementMove:
    """One deterministic cell relabel applied by the refinement loop."""

    iteration: int
    cell_index: int
    from_zone_class: ZoneClass
    to_zone_class: ZoneClass

    def __post_init__(self) -> None:
        invalid_iteration = (
            isinstance(self.iteration, bool)
            or not isinstance(self.iteration, int)
            or self.iteration <= 0
        )
        if invalid_iteration:
            raise ZoneRefinementError("move iteration must be a positive integer")
        _require_non_negative_int("cell_index", self.cell_index)
        if not isinstance(self.from_zone_class, ZoneClass) or not isinstance(
            self.to_zone_class,
            ZoneClass,
        ):
            raise ZoneRefinementError("move zone classes must be ZoneClass values")
        if self.from_zone_class is self.to_zone_class:
            raise ZoneRefinementError("refinement move must change the zone class")


@dataclass(frozen=True, slots=True)
class ZoneRefinementDiagnostics:
    """Bounded-loop convergence diagnostics."""

    max_iterations: int
    iterations: int
    termination: ZoneRefinementTermination
    converged: bool
    moves: tuple[ZoneRefinementMove, ...]
    initial_metrics: ZoneRefinementMetrics
    final_metrics: ZoneRefinementMetrics

    def __post_init__(self) -> None:
        _require_max_iterations(self.max_iterations)
        _require_non_negative_int("iterations", self.iterations)
        if self.iterations > self.max_iterations:
            raise ZoneRefinementError("iterations cannot exceed max_iterations")
        if not isinstance(self.termination, ZoneRefinementTermination):
            raise ZoneRefinementError(
                "termination must be a ZoneRefinementTermination value"
            )
        if not isinstance(self.converged, bool):
            raise ZoneRefinementError("converged must be boolean")
        if not isinstance(self.moves, tuple) or any(
            not isinstance(move, ZoneRefinementMove) for move in self.moves
        ):
            raise ZoneRefinementError(
                "moves must be an immutable tuple of ZoneRefinementMove"
            )
        if len(self.moves) != self.iterations:
            raise ZoneRefinementError("moves length must equal iterations")
        expected_iterations = tuple(range(1, self.iterations + 1))
        if tuple(move.iteration for move in self.moves) != expected_iterations:
            raise ZoneRefinementError(
                "move iterations must be contiguous starting at one"
            )
        if not isinstance(self.initial_metrics, ZoneRefinementMetrics) or not isinstance(
            self.final_metrics,
            ZoneRefinementMetrics,
        ):
            raise ZoneRefinementError(
                "initial/final metrics must be ZoneRefinementMetrics"
            )

        expected_converged = self.final_metrics.hard_violation_count == 0
        if self.converged != expected_converged:
            raise ZoneRefinementError("converged must reflect final hard violations")
        if self.converged and self.termination is not ZoneRefinementTermination.CONVERGED:
            raise ZoneRefinementError(
                "converged refinement must terminate as CONVERGED"
            )
        if not self.converged and self.termination is ZoneRefinementTermination.CONVERGED:
            raise ZoneRefinementError(
                "CONVERGED termination requires no hard violations"
            )
        if (
            self.termination is ZoneRefinementTermination.MAX_ITERATIONS
            and self.iterations != self.max_iterations
        ):
            raise ZoneRefinementError(
                "MAX_ITERATIONS termination requires iterations == max_iterations"
            )


@dataclass(frozen=True, slots=True)
class ZoneRefinementResult:
    """Refined labels and diagnostics over the unchanged base partition geometry."""

    assignments: tuple[ZoneAssignment, ...]
    shares: tuple[ZoneShareDiagnostic, ...]
    regions: tuple[ZoneRegionDiagnostic, ...]
    diagnostics: ZoneRefinementDiagnostics
    total_area_m2: float
    zoning_config_version: str
    zoning_config_fingerprint: str
    input_assignment_strategy_version: str
    refinement_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.assignments, tuple) or not self.assignments:
            raise ZoneRefinementError(
                "assignments must be a non-empty immutable tuple"
            )
        if any(not isinstance(item, ZoneAssignment) for item in self.assignments):
            raise ZoneRefinementError(
                "assignments must contain only ZoneAssignment values"
            )
        expected_cells = tuple(range(len(self.assignments)))
        if tuple(item.cell_index for item in self.assignments) != expected_cells:
            raise ZoneRefinementError(
                "assignments must be in contiguous cell-index order"
            )
        if len({item.seed_index for item in self.assignments}) != len(
            self.assignments
        ):
            raise ZoneRefinementError("assignment seed_index values must be unique")

        if not isinstance(self.shares, tuple) or any(
            not isinstance(item, ZoneShareDiagnostic) for item in self.shares
        ):
            raise ZoneRefinementError(
                "shares must be an immutable tuple of ZoneShareDiagnostic"
            )
        if tuple(item.zone_class for item in self.shares) != tuple(ZoneClass):
            raise ZoneRefinementError(
                "shares must contain all canonical zone classes in order"
            )
        if not isinstance(self.regions, tuple) or not self.regions:
            raise ZoneRefinementError("regions must be a non-empty immutable tuple")
        if any(not isinstance(item, ZoneRegionDiagnostic) for item in self.regions):
            raise ZoneRefinementError(
                "regions must contain only ZoneRegionDiagnostic values"
            )
        expected_regions = tuple(range(len(self.regions)))
        if tuple(region.region_index for region in self.regions) != expected_regions:
            raise ZoneRefinementError(
                "regions must be in contiguous region-index order"
            )
        region_cells = tuple(
            cell_index
            for region in self.regions
            for cell_index in region.cell_indices
        )
        if sorted(region_cells) != list(range(len(self.assignments))):
            raise ZoneRefinementError(
                "regions must cover every assignment cell exactly once"
            )

        if not isinstance(self.diagnostics, ZoneRefinementDiagnostics):
            raise ZoneRefinementError(
                "diagnostics must be ZoneRefinementDiagnostics"
            )
        total_area_m2 = _require_positive_finite(
            "total_area_m2",
            self.total_area_m2,
        )
        if not isinstance(self.zoning_config_version, str) or not (
            self.zoning_config_version
        ):
            raise ZoneRefinementError(
                "zoning_config_version must be a non-empty string"
            )
        if (
            not isinstance(self.zoning_config_fingerprint, str)
            or _SHA256_RE.fullmatch(self.zoning_config_fingerprint) is None
        ):
            raise ZoneRefinementError(
                "zoning_config_fingerprint must be a lowercase SHA-256 hex digest"
            )
        if (
            not isinstance(self.input_assignment_strategy_version, str)
            or not self.input_assignment_strategy_version
        ):
            raise ZoneRefinementError(
                "input_assignment_strategy_version must be a non-empty string"
            )
        if not isinstance(self.refinement_version, str) or not self.refinement_version:
            raise ZoneRefinementError(
                "refinement_version must be a non-empty string"
            )

        tolerance_m2 = _area_tolerance(total_area_m2)
        assignment_area = math.fsum(item.area_m2 for item in self.assignments)
        if not math.isclose(
            assignment_area,
            total_area_m2,
            rel_tol=0.0,
            abs_tol=tolerance_m2,
        ):
            raise ZoneRefinementError("assignment areas must sum to total_area_m2")
        share_area = math.fsum(item.assigned_area_m2 for item in self.shares)
        if not math.isclose(
            share_area,
            total_area_m2,
            rel_tol=0.0,
            abs_tol=tolerance_m2,
        ):
            raise ZoneRefinementError("share assigned areas must sum to total_area_m2")
        region_area = math.fsum(region.area_m2 for region in self.regions)
        if not math.isclose(
            region_area,
            total_area_m2,
            rel_tol=0.0,
            abs_tol=tolerance_m2,
        ):
            raise ZoneRefinementError("region areas must sum to total_area_m2")

    def assignment_for_cell(self, cell_index: int) -> ZoneAssignment:
        _require_non_negative_int("cell_index", cell_index)
        if cell_index >= len(self.assignments):
            raise ZoneRefinementError(
                f"unknown partition cell index: {cell_index}"
            )
        return self.assignments[cell_index]

    def share(self, zone_class: ZoneClass) -> ZoneShareDiagnostic:
        if not isinstance(zone_class, ZoneClass):
            raise ZoneRefinementError(
                "share lookup requires a ZoneClass value"
            )
        return self.shares[_ZONE_ORDER[zone_class]]


@dataclass(frozen=True, slots=True)
class _AdjacencyEdge:
    first: int
    second: int
    shared_boundary_m: float


@dataclass(frozen=True, slots=True)
class _Topology:
    edges: tuple[_AdjacencyEdge, ...]
    neighbors: tuple[tuple[int, ...], ...]


@dataclass(frozen=True, slots=True)
class _EvaluatedState:
    metrics: ZoneRefinementMetrics
    regions: tuple[ZoneRegionDiagnostic, ...]
    problem_cells: tuple[int, ...]


class DeterministicZoneRegionRefiner:
    """Relabel partition cells to reduce adjacency/minimum-region violations."""

    version = "1"

    def refine(
        self,
        *,
        partition: ZoningPartitionResult,
        assignment: ZoneAssignmentResult,
        config: ZoningConfig,
        max_iterations: int = 256,
    ) -> ZoneRefinementResult:
        _validate_inputs(
            partition=partition,
            assignment=assignment,
            config=config,
            max_iterations=max_iterations,
        )
        topology = _build_topology(partition)
        labels = [item.zone_class for item in assignment.assignments]
        state = _evaluate_state(
            labels=labels,
            partition=partition,
            topology=topology,
            config=config,
        )
        initial_metrics = state.metrics
        moves: list[ZoneRefinementMove] = []

        if state.metrics.hard_violation_count == 0:
            termination = ZoneRefinementTermination.CONVERGED
        else:
            termination = ZoneRefinementTermination.STALLED
            for iteration in range(1, max_iterations + 1):
                move = _best_improving_move(
                    labels=labels,
                    state=state,
                    partition=partition,
                    topology=topology,
                    config=config,
                )
                if move is None:
                    termination = ZoneRefinementTermination.STALLED
                    break

                labels[move.cell_index] = move.to_zone_class
                moves.append(
                    ZoneRefinementMove(
                        iteration=iteration,
                        cell_index=move.cell_index,
                        from_zone_class=move.from_zone_class,
                        to_zone_class=move.to_zone_class,
                    )
                )
                state = _evaluate_state(
                    labels=labels,
                    partition=partition,
                    topology=topology,
                    config=config,
                )
                if state.metrics.hard_violation_count == 0:
                    termination = ZoneRefinementTermination.CONVERGED
                    break
            else:
                termination = ZoneRefinementTermination.MAX_ITERATIONS

        assignments = tuple(
            ZoneAssignment(
                cell_index=item.cell_index,
                seed_index=item.seed_index,
                zone_class=labels[item.cell_index],
                area_m2=item.area_m2,
                suitability_score=item.suitability_score,
            )
            for item in assignment.assignments
        )
        shares = _build_share_diagnostics(
            assignments=assignments,
            config=config,
            total_area_m2=partition.developable_area_m2,
        )
        diagnostics = ZoneRefinementDiagnostics(
            max_iterations=max_iterations,
            iterations=len(moves),
            termination=termination,
            converged=state.metrics.hard_violation_count == 0,
            moves=tuple(moves),
            initial_metrics=initial_metrics,
            final_metrics=state.metrics,
        )
        return ZoneRefinementResult(
            assignments=assignments,
            shares=shares,
            regions=state.regions,
            diagnostics=diagnostics,
            total_area_m2=partition.developable_area_m2,
            zoning_config_version=config.version,
            zoning_config_fingerprint=config.fingerprint,
            input_assignment_strategy_version=assignment.strategy_version,
            refinement_version=self.version,
        )


@dataclass(frozen=True, slots=True)
class _CandidateMove:
    cell_index: int
    from_zone_class: ZoneClass
    to_zone_class: ZoneClass
    state: _EvaluatedState
    shared_boundary_m: float
    suitability_score: float

    @property
    def ordering_key(self) -> _OrderingKey:
        return (
            _objective_key(self.state.metrics),
            self.suitability_score,
            -self.shared_boundary_m,
            self.cell_index,
            _ZONE_ORDER[self.to_zone_class],
        )


def _best_improving_move(
    *,
    labels: list[ZoneClass],
    state: _EvaluatedState,
    partition: ZoningPartitionResult,
    topology: _Topology,
    config: ZoningConfig,
) -> _CandidateMove | None:
    current_objective = _objective_key(state.metrics)
    best: _CandidateMove | None = None

    for cell_index in state.problem_cells:
        from_zone_class = labels[cell_index]
        candidate_classes = sorted(
            {
                labels[neighbor]
                for neighbor in topology.neighbors[cell_index]
                if labels[neighbor] is not from_zone_class
            },
            key=lambda zone_class: _ZONE_ORDER[zone_class],
        )
        for to_zone_class in candidate_classes:
            labels[cell_index] = to_zone_class
            candidate_state = _evaluate_state(
                labels=labels,
                partition=partition,
                topology=topology,
                config=config,
            )
            labels[cell_index] = from_zone_class
            if _objective_key(candidate_state.metrics) >= current_objective:
                continue

            candidate = _CandidateMove(
                cell_index=cell_index,
                from_zone_class=from_zone_class,
                to_zone_class=to_zone_class,
                state=candidate_state,
                shared_boundary_m=_shared_boundary_to_class(
                    cell_index=cell_index,
                    zone_class=to_zone_class,
                    labels=labels,
                    topology=topology,
                ),
                suitability_score=(
                    partition.cells[cell_index].seed.suitability_score
                ),
            )
            if best is None or candidate.ordering_key < best.ordering_key:
                best = candidate
    return best


def _objective_key(metrics: ZoneRefinementMetrics) -> _ObjectiveKey:
    return (
        metrics.forbidden_adjacency_count,
        metrics.under_minimum_region_count,
        metrics.under_minimum_area_deficit_m2,
        metrics.absolute_target_error_m2,
        metrics.discouraged_adjacency_count,
        -metrics.preferred_adjacency_count,
    )


def _evaluate_state(
    *,
    labels: list[ZoneClass],
    partition: ZoningPartitionResult,
    topology: _Topology,
    config: ZoningConfig,
) -> _EvaluatedState:
    regions = _build_regions(
        labels=labels,
        partition=partition,
        topology=topology,
        config=config,
    )
    forbidden = 0
    discouraged = 0
    preferred = 0
    forbidden_cells: set[int] = set()
    for edge in topology.edges:
        policy = config.adjacency_policy(
            labels[edge.first],
            labels[edge.second],
        )
        if policy is ZoneAdjacencyPolicy.FORBIDDEN:
            forbidden += 1
            forbidden_cells.update((edge.first, edge.second))
        elif policy is ZoneAdjacencyPolicy.DISCOURAGED:
            discouraged += 1
        elif policy is ZoneAdjacencyPolicy.PREFERRED:
            preferred += 1

    under_minimum = tuple(
        region for region in regions if not region.meets_minimum_area
    )
    problem_cells = set(forbidden_cells)
    for region in under_minimum:
        problem_cells.update(region.cell_indices)
        for cell_index in region.cell_indices:
            problem_cells.update(topology.neighbors[cell_index])

    assigned_area = {zone_class: 0.0 for zone_class in ZoneClass}
    for cell_index, zone_class in enumerate(labels):
        assigned_area[zone_class] += partition.cells[cell_index].area_m2
    absolute_target_error_m2 = math.fsum(
        abs(
            assigned_area[zone.zone_class]
            - partition.developable_area_m2 * zone.target_share
        )
        for zone in config.zones
    )

    return _EvaluatedState(
        metrics=ZoneRefinementMetrics(
            region_count=len(regions),
            forbidden_adjacency_count=forbidden,
            discouraged_adjacency_count=discouraged,
            preferred_adjacency_count=preferred,
            under_minimum_region_count=len(under_minimum),
            under_minimum_area_deficit_m2=math.fsum(
                region.minimum_area_deficit_m2 for region in under_minimum
            ),
            absolute_target_error_m2=absolute_target_error_m2,
        ),
        regions=regions,
        problem_cells=tuple(sorted(problem_cells)),
    )


def _build_regions(
    *,
    labels: list[ZoneClass],
    partition: ZoningPartitionResult,
    topology: _Topology,
    config: ZoningConfig,
) -> tuple[ZoneRegionDiagnostic, ...]:
    seen: set[int] = set()
    regions: list[ZoneRegionDiagnostic] = []
    tolerance_m2 = _area_tolerance(partition.developable_area_m2)

    for start in range(len(labels)):
        if start in seen:
            continue
        zone_class = labels[start]
        stack = [start]
        cells: list[int] = []
        seen.add(start)
        while stack:
            cell_index = stack.pop()
            cells.append(cell_index)
            for neighbor in reversed(topology.neighbors[cell_index]):
                if neighbor not in seen and labels[neighbor] is zone_class:
                    seen.add(neighbor)
                    stack.append(neighbor)

        cell_indices = tuple(sorted(cells))
        area_m2 = math.fsum(
            partition.cells[index].area_m2 for index in cell_indices
        )
        minimum_area_m2 = config.zone(zone_class).minimum_area_m2
        regions.append(
            ZoneRegionDiagnostic(
                region_index=len(regions),
                zone_class=zone_class,
                cell_indices=cell_indices,
                area_m2=area_m2,
                minimum_area_m2=minimum_area_m2,
                meets_minimum_area=(
                    area_m2 + tolerance_m2 >= minimum_area_m2
                ),
            )
        )
    return tuple(regions)


def _build_topology(partition: ZoningPartitionResult) -> _Topology:
    geometries = tuple(cell.geometry for cell in partition.cells)
    tree = STRtree(geometries)
    edges: list[_AdjacencyEdge] = []
    neighbors: list[set[int]] = [set() for _geometry in geometries]

    for first, geometry in enumerate(geometries):
        for raw_second in tree.query(geometry):
            second = int(raw_second)
            if second <= first:
                continue
            shared_boundary_m = float(
                geometry.boundary.intersection(
                    geometries[second].boundary
                ).length
            )
            if shared_boundary_m <= _MIN_SHARED_BOUNDARY_M:
                continue
            edges.append(
                _AdjacencyEdge(
                    first=first,
                    second=second,
                    shared_boundary_m=shared_boundary_m,
                )
            )
            neighbors[first].add(second)
            neighbors[second].add(first)

    return _Topology(
        edges=tuple(edges),
        neighbors=tuple(tuple(sorted(bucket)) for bucket in neighbors),
    )


def _shared_boundary_to_class(
    *,
    cell_index: int,
    zone_class: ZoneClass,
    labels: list[ZoneClass],
    topology: _Topology,
) -> float:
    length = 0.0
    for edge in topology.edges:
        if edge.first == cell_index and labels[edge.second] is zone_class:
            length += edge.shared_boundary_m
        elif edge.second == cell_index and labels[edge.first] is zone_class:
            length += edge.shared_boundary_m
    return length


def _build_share_diagnostics(
    *,
    assignments: tuple[ZoneAssignment, ...],
    config: ZoningConfig,
    total_area_m2: float,
) -> tuple[ZoneShareDiagnostic, ...]:
    assigned_area = {zone_class: 0.0 for zone_class in ZoneClass}
    counts = {zone_class: 0 for zone_class in ZoneClass}
    for assignment in assignments:
        assigned_area[assignment.zone_class] += assignment.area_m2
        counts[assignment.zone_class] += 1

    return tuple(
        ZoneShareDiagnostic(
            zone_class=zone.zone_class,
            target_share=zone.target_share,
            target_area_m2=total_area_m2 * zone.target_share,
            assigned_area_m2=assigned_area[zone.zone_class],
            achieved_share=assigned_area[zone.zone_class] / total_area_m2,
            absolute_area_error_m2=abs(
                assigned_area[zone.zone_class]
                - total_area_m2 * zone.target_share
            ),
            cell_count=counts[zone.zone_class],
        )
        for zone in config.zones
    )


def _validate_inputs(
    *,
    partition: ZoningPartitionResult,
    assignment: ZoneAssignmentResult,
    config: ZoningConfig,
    max_iterations: int,
) -> None:
    if not isinstance(partition, ZoningPartitionResult):
        raise ZoneRefinementError(
            "partition must be a ZoningPartitionResult"
        )
    if not isinstance(assignment, ZoneAssignmentResult):
        raise ZoneRefinementError(
            "assignment must be a ZoneAssignmentResult"
        )
    if not isinstance(config, ZoningConfig):
        raise ZoneRefinementError("config must be a ZoningConfig")
    _require_max_iterations(max_iterations)
    if assignment.zoning_config_version != config.version:
        raise ZoneRefinementError(
            "assignment zoning config version does not match config"
        )
    if assignment.zoning_config_fingerprint != config.fingerprint:
        raise ZoneRefinementError(
            "assignment zoning config fingerprint does not match config"
        )
    if len(assignment.assignments) != len(partition.cells):
        raise ZoneRefinementError(
            "assignment must contain exactly one label per partition cell"
        )
    if not math.isclose(
        assignment.total_area_m2,
        partition.developable_area_m2,
        rel_tol=0.0,
        abs_tol=_area_tolerance(partition.developable_area_m2),
    ):
        raise ZoneRefinementError(
            "assignment total area does not match partition"
        )

    for cell_index, (label, cell) in enumerate(
        zip(assignment.assignments, partition.cells, strict=True)
    ):
        if label.cell_index != cell_index or label.seed_index != cell.seed_index:
            raise ZoneRefinementError(
                "assignment cell/seed references do not match partition"
            )
        if not math.isclose(
            label.area_m2,
            cell.area_m2,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ZoneRefinementError(
                "assignment cell area does not match partition"
            )
        if not math.isclose(
            label.suitability_score,
            cell.seed.suitability_score,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ZoneRefinementError(
                "assignment suitability score does not match partition seed"
            )


def _require_max_iterations(value: int) -> None:
    invalid = (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > _MAX_ITERATION_LIMIT
    )
    if invalid:
        raise ZoneRefinementError(
            "max_iterations must be an integer inside "
            f"1..{_MAX_ITERATION_LIMIT}"
        )


def _area_tolerance(total_area_m2: float) -> float:
    return max(
        _MIN_AREA_TOLERANCE_M2,
        total_area_m2 * _AREA_TOLERANCE_RATIO,
    )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ZoneRefinementError(
            f"{field_name} must be a non-negative integer"
        )


def _require_finite_number(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ZoneRefinementError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ZoneRefinementError(f"{field_name} must be a finite number")
    return number


def _require_positive_finite(field_name: str, value: float) -> float:
    number = _require_finite_number(field_name, value)
    if number <= 0.0:
        raise ZoneRefinementError(f"{field_name} must be greater than zero")
    return number


def _require_non_negative_finite(field_name: str, value: float) -> float:
    number = _require_finite_number(field_name, value)
    if number < 0.0:
        raise ZoneRefinementError(f"{field_name} must be non-negative")
    return number
