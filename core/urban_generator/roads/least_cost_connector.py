from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from shapely.geometry import LineString

from core.urban_generator.domain import NetworkPoint, require_working_crs
from core.urban_generator.roads.candidate_anchors import CandidateRoadAnchor
from core.urban_generator.suitability import HardExclusionMask, WeightedSuitabilityResult

DEFAULT_MAX_LEAST_COST_VISITED_CELLS = 250_000
DEFAULT_SUITABILITY_PENALTY_WEIGHT = 1.0

_CARDINAL_OFFSETS = ((-1, 0), (0, -1), (0, 1), (1, 0))
_DIAGONAL_OFFSETS = ((-1, -1), (-1, 1), (1, -1), (1, 1))
_COST_TOLERANCE = 1e-12
_COORDINATE_TOLERANCE_M = 1e-9


class LeastCostConnectorError(ValueError):
    """Raised when least-cost connector inputs violate the raster routing contract."""


class LeastCostConnectionStatus(StrEnum):
    """Stable outcome states for bounded least-cost raster search."""

    CONNECTED = "CONNECTED"
    NO_PATH = "NO_PATH"
    SEARCH_LIMIT_REACHED = "SEARCH_LIMIT_REACHED"


@dataclass(frozen=True, slots=True)
class LeastCostConnectorPolicy:
    """Explicit bounded A* and cost-surface policy for one anchor connection."""

    max_visited_cells: int = DEFAULT_MAX_LEAST_COST_VISITED_CELLS
    suitability_penalty_weight: float = DEFAULT_SUITABILITY_PENALTY_WEIGHT
    allow_diagonal: bool = True
    prevent_corner_cutting: bool = True

    def __post_init__(self) -> None:
        _require_positive_int("max_visited_cells", self.max_visited_cells)
        weight = _require_non_negative_finite(
            "suitability_penalty_weight",
            self.suitability_penalty_weight,
        )
        object.__setattr__(self, "suitability_penalty_weight", weight)
        if not isinstance(self.allow_diagonal, bool):
            raise LeastCostConnectorError("allow_diagonal must be boolean")
        if not isinstance(self.prevent_corner_cutting, bool):
            raise LeastCostConnectorError("prevent_corner_cutting must be boolean")


@dataclass(frozen=True, slots=True)
class LeastCostConnectionResult:
    """Backend-independent result of one bounded least-cost anchor connection."""

    working_srid: int
    start_anchor_id: str
    target_anchor_id: str
    status: LeastCostConnectionStatus
    geometry: LineString | None
    raster_path: tuple[tuple[int, int], ...]
    length_m: float | None
    total_cost: float | None
    visited_cell_count: int
    hard_exclusion_source_codes: tuple[str, ...]
    connector_version: str

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        for field_name in ("start_anchor_id", "target_anchor_id", "connector_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise LeastCostConnectorError(f"{field_name} must be a non-empty string")
        if self.start_anchor_id == self.target_anchor_id:
            raise LeastCostConnectorError("start and target anchor ids must differ")
        if not isinstance(self.status, LeastCostConnectionStatus):
            raise LeastCostConnectorError("status must be a LeastCostConnectionStatus value")
        _require_non_negative_int("visited_cell_count", self.visited_cell_count)
        if not isinstance(self.hard_exclusion_source_codes, tuple):
            raise LeastCostConnectorError(
                "hard_exclusion_source_codes must be an immutable tuple"
            )
        if any(
            not isinstance(code, str) or not code.strip()
            for code in self.hard_exclusion_source_codes
        ):
            raise LeastCostConnectorError(
                "hard_exclusion_source_codes must contain non-empty strings"
            )
        if len(self.hard_exclusion_source_codes) != len(
            set(self.hard_exclusion_source_codes)
        ):
            raise LeastCostConnectorError("hard exclusion source codes must be unique")
        if not isinstance(self.raster_path, tuple):
            raise LeastCostConnectorError("raster_path must be an immutable tuple")
        for row, col in self.raster_path:
            _require_non_negative_int("raster path row", row)
            _require_non_negative_int("raster path col", col)

        if self.status is LeastCostConnectionStatus.CONNECTED:
            if not isinstance(self.geometry, LineString):
                raise LeastCostConnectorError("connected result must contain LineString geometry")
            if len(self.raster_path) < 2:
                raise LeastCostConnectorError(
                    "connected result must contain at least two raster path cells"
                )
            length_m = _require_non_negative_finite("length_m", self.length_m)
            total_cost = _require_non_negative_finite("total_cost", self.total_cost)
            if length_m <= 0.0:
                raise LeastCostConnectorError("connected result length_m must be positive")
            if total_cost <= 0.0:
                raise LeastCostConnectorError("connected result total_cost must be positive")
            if not math.isclose(
                length_m,
                float(self.geometry.length),
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise LeastCostConnectorError(
                    "connected result length_m must match geometry length"
                )
            if self.visited_cell_count <= 0:
                raise LeastCostConnectorError(
                    "connected result must visit at least one raster cell"
                )
        else:
            if self.geometry is not None or self.raster_path:
                raise LeastCostConnectorError(
                    "non-connected result must not contain geometry or raster path"
                )
            if self.length_m is not None or self.total_cost is not None:
                raise LeastCostConnectorError(
                    "non-connected result must not contain length or cost"
                )

    @property
    def connected(self) -> bool:
        return self.status is LeastCostConnectionStatus.CONNECTED


class LeastCostConnector:
    """Route one generated connection over canonical suitability with bounded A*."""

    version = "1"

    def connect(
        self,
        *,
        start: CandidateRoadAnchor,
        target: CandidateRoadAnchor,
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        policy: LeastCostConnectorPolicy | None = None,
    ) -> LeastCostConnectionResult:
        if not isinstance(start, CandidateRoadAnchor):
            raise LeastCostConnectorError("start must be a CandidateRoadAnchor")
        if not isinstance(target, CandidateRoadAnchor):
            raise LeastCostConnectorError("target must be a CandidateRoadAnchor")
        if not isinstance(suitability, WeightedSuitabilityResult):
            raise LeastCostConnectorError(
                "suitability must be a WeightedSuitabilityResult"
            )
        if not isinstance(hard_mask, HardExclusionMask):
            raise LeastCostConnectorError("hard_mask must be a HardExclusionMask")
        if policy is None:
            policy = LeastCostConnectorPolicy()
        if not isinstance(policy, LeastCostConnectorPolicy):
            raise LeastCostConnectorError("policy must be a LeastCostConnectorPolicy")

        grid = suitability.grid
        require_working_crs(grid.working_srid)
        if hard_mask.grid != grid:
            raise LeastCostConnectorError(
                "hard_mask must use exactly the suitability grid"
            )
        if not np.array_equal(suitability.hard_excluded_mask, hard_mask.excluded):
            raise LeastCostConnectorError(
                "suitability hard-excluded cells must match hard_mask exactly"
            )
        if start.anchor_id == target.anchor_id:
            raise LeastCostConnectorError("start and target anchors must differ")

        _validate_anchor(anchor=start, suitability=suitability, field_name="start")
        _validate_anchor(anchor=target, suitability=suitability, field_name="target")
        start_cell = (start.raster_row, start.raster_col)
        target_cell = (target.raster_row, target.raster_col)
        if start_cell == target_cell:
            raise LeastCostConnectorError(
                "start and target anchors must occupy different raster cells"
            )

        outcome = self._search(
            start_cell=start_cell,
            target_cell=target_cell,
            suitability=suitability,
            hard_mask=hard_mask,
            policy=policy,
        )
        if outcome.status is not LeastCostConnectionStatus.CONNECTED:
            return LeastCostConnectionResult(
                working_srid=grid.working_srid,
                start_anchor_id=start.anchor_id,
                target_anchor_id=target.anchor_id,
                status=outcome.status,
                geometry=None,
                raster_path=(),
                length_m=None,
                total_cost=None,
                visited_cell_count=outcome.visited_cell_count,
                hard_exclusion_source_codes=hard_mask.source_codes,
                connector_version=self.version,
            )

        coordinates = tuple(
            _cell_center(suitability=suitability, row=row, col=col)
            for row, col in outcome.raster_path
        )
        geometry = LineString(coordinates)
        return LeastCostConnectionResult(
            working_srid=grid.working_srid,
            start_anchor_id=start.anchor_id,
            target_anchor_id=target.anchor_id,
            status=LeastCostConnectionStatus.CONNECTED,
            geometry=geometry,
            raster_path=outcome.raster_path,
            length_m=float(geometry.length),
            total_cost=outcome.total_cost,
            visited_cell_count=outcome.visited_cell_count,
            hard_exclusion_source_codes=hard_mask.source_codes,
            connector_version=self.version,
        )

    def _search(
        self,
        *,
        start_cell: tuple[int, int],
        target_cell: tuple[int, int],
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        policy: LeastCostConnectorPolicy,
    ) -> _SearchOutcome:
        frontier: list[tuple[float, float, int, int]] = []
        start_heuristic = _heuristic_m(
            start_cell,
            target_cell,
            suitability=suitability,
        )
        heapq.heappush(
            frontier,
            (start_heuristic, 0.0, start_cell[0], start_cell[1]),
        )
        best_cost: dict[tuple[int, int], float] = {start_cell: 0.0}
        parent: dict[tuple[int, int], tuple[int, int]] = {}
        closed: set[tuple[int, int]] = set()
        visited_cell_count = 0

        while frontier:
            _estimated_total, current_cost, row, col = heapq.heappop(frontier)
            current = (row, col)
            if current in closed:
                continue
            stored_cost = best_cost.get(current)
            if stored_cost is None or current_cost > stored_cost + _COST_TOLERANCE:
                continue
            if visited_cell_count >= policy.max_visited_cells:
                return _SearchOutcome(
                    status=LeastCostConnectionStatus.SEARCH_LIMIT_REACHED,
                    raster_path=(),
                    total_cost=None,
                    visited_cell_count=visited_cell_count,
                )

            closed.add(current)
            visited_cell_count += 1
            if current == target_cell:
                return _SearchOutcome(
                    status=LeastCostConnectionStatus.CONNECTED,
                    raster_path=_reconstruct_path(
                        start_cell=start_cell,
                        target_cell=target_cell,
                        parent=parent,
                    ),
                    total_cost=current_cost,
                    visited_cell_count=visited_cell_count,
                )

            for neighbor in _neighbors(
                current=current,
                suitability=suitability,
                hard_mask=hard_mask,
                policy=policy,
            ):
                if neighbor in closed:
                    continue
                step_cost = _step_cost(
                    current=current,
                    neighbor=neighbor,
                    suitability=suitability,
                    suitability_penalty_weight=policy.suitability_penalty_weight,
                )
                candidate_cost = current_cost + step_cost
                previous_cost = best_cost.get(neighbor)
                previous_parent = parent.get(neighbor)
                is_better = previous_cost is None or candidate_cost < (
                    previous_cost - _COST_TOLERANCE
                )
                is_equal_with_better_parent = (
                    previous_cost is not None
                    and math.isclose(
                        candidate_cost,
                        previous_cost,
                        rel_tol=0.0,
                        abs_tol=_COST_TOLERANCE,
                    )
                    and (previous_parent is None or current < previous_parent)
                )
                if not is_better and not is_equal_with_better_parent:
                    continue

                best_cost[neighbor] = candidate_cost
                parent[neighbor] = current
                heuristic = _heuristic_m(
                    neighbor,
                    target_cell,
                    suitability=suitability,
                )
                heapq.heappush(
                    frontier,
                    (
                        candidate_cost + heuristic,
                        candidate_cost,
                        neighbor[0],
                        neighbor[1],
                    ),
                )

        return _SearchOutcome(
            status=LeastCostConnectionStatus.NO_PATH,
            raster_path=(),
            total_cost=None,
            visited_cell_count=visited_cell_count,
        )


@dataclass(frozen=True, slots=True)
class _SearchOutcome:
    status: LeastCostConnectionStatus
    raster_path: tuple[tuple[int, int], ...]
    total_cost: float | None
    visited_cell_count: int


def _validate_anchor(
    *,
    anchor: CandidateRoadAnchor,
    suitability: WeightedSuitabilityResult,
    field_name: str,
) -> None:
    grid = suitability.grid
    row = anchor.raster_row
    col = anchor.raster_col
    if row >= grid.height or col >= grid.width:
        raise LeastCostConnectorError(
            f"{field_name} anchor raster cell must be inside the suitability grid"
        )
    if not bool(suitability.valid_mask[row, col]):
        raise LeastCostConnectorError(
            f"{field_name} anchor must occupy a valid non-excluded suitability cell"
        )
    if bool(suitability.hard_excluded_mask[row, col]):
        raise LeastCostConnectorError(
            f"{field_name} anchor must not occupy a hard-excluded cell"
        )

    expected_x, expected_y = _cell_center(suitability=suitability, row=row, col=col)
    if not math.isclose(
        anchor.point.x_m,
        expected_x,
        rel_tol=0.0,
        abs_tol=_COORDINATE_TOLERANCE_M,
    ) or not math.isclose(
        anchor.point.y_m,
        expected_y,
        rel_tol=0.0,
        abs_tol=_COORDINATE_TOLERANCE_M,
    ):
        raise LeastCostConnectorError(
            f"{field_name} anchor point must match its raster cell center"
        )
    if not math.isclose(
        anchor.suitability_score,
        float(suitability.scores[row, col]),
        rel_tol=0.0,
        abs_tol=_COST_TOLERANCE,
    ):
        raise LeastCostConnectorError(
            f"{field_name} anchor suitability_score must match the suitability raster"
        )


def _neighbors(
    *,
    current: tuple[int, int],
    suitability: WeightedSuitabilityResult,
    hard_mask: HardExclusionMask,
    policy: LeastCostConnectorPolicy,
) -> tuple[tuple[int, int], ...]:
    row, col = current
    offsets = _CARDINAL_OFFSETS + (_DIAGONAL_OFFSETS if policy.allow_diagonal else ())
    neighbors: list[tuple[int, int]] = []
    for row_offset, col_offset in offsets:
        candidate = (row + row_offset, col + col_offset)
        if not _is_traversable(
            candidate,
            suitability=suitability,
            hard_mask=hard_mask,
        ):
            continue
        if (
            row_offset != 0
            and col_offset != 0
            and policy.prevent_corner_cutting
            and (
                not _is_traversable(
                    (row + row_offset, col),
                    suitability=suitability,
                    hard_mask=hard_mask,
                )
                or not _is_traversable(
                    (row, col + col_offset),
                    suitability=suitability,
                    hard_mask=hard_mask,
                )
            )
        ):
            continue
        neighbors.append(candidate)
    return tuple(sorted(neighbors))


def _is_traversable(
    cell: tuple[int, int],
    *,
    suitability: WeightedSuitabilityResult,
    hard_mask: HardExclusionMask,
) -> bool:
    row, col = cell
    grid = suitability.grid
    if row < 0 or row >= grid.height or col < 0 or col >= grid.width:
        return False
    return bool(suitability.valid_mask[row, col]) and not bool(hard_mask.excluded[row, col])


def _step_cost(
    *,
    current: tuple[int, int],
    neighbor: tuple[int, int],
    suitability: WeightedSuitabilityResult,
    suitability_penalty_weight: float,
) -> float:
    distance_m = _cell_distance_m(current, neighbor, suitability=suitability)
    current_score = float(suitability.scores[current[0], current[1]])
    neighbor_score = float(suitability.scores[neighbor[0], neighbor[1]])
    current_factor = 1.0 + suitability_penalty_weight * (1.0 - current_score)
    neighbor_factor = 1.0 + suitability_penalty_weight * (1.0 - neighbor_score)
    return distance_m * ((current_factor + neighbor_factor) / 2.0)


def _heuristic_m(
    cell: tuple[int, int],
    target: tuple[int, int],
    *,
    suitability: WeightedSuitabilityResult,
) -> float:
    row_delta = (target[0] - cell[0]) * suitability.grid.cell_height_m
    col_delta = (target[1] - cell[1]) * suitability.grid.cell_width_m
    return math.hypot(row_delta, col_delta)


def _cell_distance_m(
    first: tuple[int, int],
    second: tuple[int, int],
    *,
    suitability: WeightedSuitabilityResult,
) -> float:
    row_delta = (second[0] - first[0]) * suitability.grid.cell_height_m
    col_delta = (second[1] - first[1]) * suitability.grid.cell_width_m
    return math.hypot(row_delta, col_delta)


def _cell_center(
    *,
    suitability: WeightedSuitabilityResult,
    row: int,
    col: int,
) -> tuple[float, float]:
    grid = suitability.grid
    min_x, _min_y, _max_x, max_y = grid.bounds
    return (
        min_x + (col + 0.5) * grid.cell_width_m,
        max_y - (row + 0.5) * grid.cell_height_m,
    )


def _reconstruct_path(
    *,
    start_cell: tuple[int, int],
    target_cell: tuple[int, int],
    parent: dict[tuple[int, int], tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    reversed_path = [target_cell]
    current = target_cell
    while current != start_cell:
        try:
            current = parent[current]
        except KeyError as exc:  # pragma: no cover - defensive invariant guard
            raise LeastCostConnectorError(
                "A* parent chain is incomplete for a connected result"
            ) from exc
        reversed_path.append(current)
    reversed_path.reverse()
    return tuple(reversed_path)


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LeastCostConnectorError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LeastCostConnectorError(f"{field_name} must be a non-negative integer")


def _require_non_negative_finite(field_name: str, value: float | None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LeastCostConnectorError(f"{field_name} must be a non-negative finite number")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise LeastCostConnectorError(f"{field_name} must be a non-negative finite number")
    return number
