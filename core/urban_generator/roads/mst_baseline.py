from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

import numpy as np

from core.urban_generator.domain import require_working_crs
from core.urban_generator.roads.candidate_anchors import CandidateRoadAnchor
from core.urban_generator.roads.least_cost_connector import (
    LeastCostConnectionResult,
    LeastCostConnectionStatus,
    LeastCostConnector,
    LeastCostConnectorPolicy,
)
from core.urban_generator.suitability import HardExclusionMask, WeightedSuitabilityResult

DEFAULT_MAX_MST_ANCHORS = 1_000
DEFAULT_MAX_MST_DISTANCE_EVALUATIONS = 499_500


class MstBaselineConnectorError(ValueError):
    """Raised when MST baseline inputs violate the bounded connectivity contract."""


class MstBaselineStatus(StrEnum):
    """Stable overall outcome of the baseline MST connection pass."""

    CONNECTED = "CONNECTED"
    INCOMPLETE = "INCOMPLETE"
    TRIVIAL = "TRIVIAL"


@dataclass(frozen=True, slots=True)
class MstBaselinePolicy:
    """Explicit bounds for complete-metric MST pair selection."""

    max_anchors: int = DEFAULT_MAX_MST_ANCHORS
    max_distance_evaluations: int = DEFAULT_MAX_MST_DISTANCE_EVALUATIONS

    def __post_init__(self) -> None:
        _require_positive_int("max_anchors", self.max_anchors)
        _require_non_negative_int("max_distance_evaluations", self.max_distance_evaluations)


@runtime_checkable
class AnchorConnectionStrategy(Protocol):
    """Pluggable bounded strategy used to realize one selected anchor pair."""

    version: str

    def connect(
        self,
        *,
        start: CandidateRoadAnchor,
        target: CandidateRoadAnchor,
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        policy: LeastCostConnectorPolicy | None = None,
    ) -> LeastCostConnectionResult:
        """Return one stable connection result for the requested pair."""

        ...


@dataclass(frozen=True, slots=True)
class MstBaselineEdge:
    """One deterministic MST pair and its realized S06-T09-compatible connection."""

    start_anchor_id: str
    target_anchor_id: str
    baseline_distance_m: float
    connection: LeastCostConnectionResult

    def __post_init__(self) -> None:
        _require_non_empty_string("start_anchor_id", self.start_anchor_id)
        _require_non_empty_string("target_anchor_id", self.target_anchor_id)
        if self.start_anchor_id >= self.target_anchor_id:
            raise MstBaselineConnectorError(
                "MST edge anchor ids must use canonical ascending order"
            )
        distance_m = _require_non_negative_finite(
            "baseline_distance_m", self.baseline_distance_m
        )
        if distance_m <= 0.0:
            raise MstBaselineConnectorError("baseline_distance_m must be positive")
        object.__setattr__(self, "baseline_distance_m", distance_m)
        if not isinstance(self.connection, LeastCostConnectionResult):
            raise MstBaselineConnectorError(
                "connection must be a LeastCostConnectionResult"
            )
        if (
            self.connection.start_anchor_id != self.start_anchor_id
            or self.connection.target_anchor_id != self.target_anchor_id
        ):
            raise MstBaselineConnectorError(
                "connection anchor ids must match the canonical MST edge"
            )

    @property
    def connected(self) -> bool:
        return self.connection.connected


@dataclass(frozen=True, slots=True)
class MstBaselineDiagnostics:
    """Bounded-work and realized-connectivity diagnostics for one baseline pass."""

    anchor_count: int
    distance_evaluation_count: int
    selected_edge_count: int
    connected_edge_count: int
    no_path_edge_count: int
    search_limit_edge_count: int
    total_baseline_distance_m: float
    total_connected_length_m: float
    total_connected_cost: float

    def __post_init__(self) -> None:
        for field_name in (
            "anchor_count",
            "distance_evaluation_count",
            "selected_edge_count",
            "connected_edge_count",
            "no_path_edge_count",
            "search_limit_edge_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if self.selected_edge_count != max(0, self.anchor_count - 1):
            raise MstBaselineConnectorError(
                "selected_edge_count must equal max(0, anchor_count - 1)"
            )
        if (
            self.connected_edge_count
            + self.no_path_edge_count
            + self.search_limit_edge_count
            != self.selected_edge_count
        ):
            raise MstBaselineConnectorError(
                "MST edge status counts must sum to selected_edge_count"
            )
        for field_name in (
            "total_baseline_distance_m",
            "total_connected_length_m",
            "total_connected_cost",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_non_negative_finite(field_name, getattr(self, field_name)),
            )


@dataclass(frozen=True, slots=True)
class MstBaselineResult:
    """Deterministic baseline tree topology plus realized pair connections."""

    working_srid: int
    anchor_ids: tuple[str, ...]
    edges: tuple[MstBaselineEdge, ...]
    status: MstBaselineStatus
    diagnostics: MstBaselineDiagnostics
    strategy_name: str
    strategy_version: str
    edge_connector_version: str

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        if not isinstance(self.anchor_ids, tuple):
            raise MstBaselineConnectorError("anchor_ids must be an immutable tuple")
        if tuple(sorted(self.anchor_ids)) != self.anchor_ids:
            raise MstBaselineConnectorError("anchor_ids must use canonical sorted order")
        if len(set(self.anchor_ids)) != len(self.anchor_ids):
            raise MstBaselineConnectorError("anchor_ids must be unique")
        if any(not isinstance(item, str) or not item.strip() for item in self.anchor_ids):
            raise MstBaselineConnectorError("anchor_ids must contain non-empty strings")
        if not isinstance(self.edges, tuple):
            raise MstBaselineConnectorError("edges must be an immutable tuple")
        if any(not isinstance(edge, MstBaselineEdge) for edge in self.edges):
            raise MstBaselineConnectorError("edges must contain only MstBaselineEdge values")
        if not isinstance(self.status, MstBaselineStatus):
            raise MstBaselineConnectorError("status must be a MstBaselineStatus value")
        if not isinstance(self.diagnostics, MstBaselineDiagnostics):
            raise MstBaselineConnectorError(
                "diagnostics must be MstBaselineDiagnostics"
            )
        if self.diagnostics.anchor_count != len(self.anchor_ids):
            raise MstBaselineConnectorError(
                "diagnostics anchor_count must match anchor_ids"
            )
        if self.diagnostics.selected_edge_count != len(self.edges):
            raise MstBaselineConnectorError(
                "diagnostics selected_edge_count must match edges"
            )
        if any(
            edge.connection.working_srid != self.working_srid for edge in self.edges
        ):
            raise MstBaselineConnectorError(
                "all edge connections must use the baseline working_srid"
            )
        for field_name in (
            "strategy_name",
            "strategy_version",
            "edge_connector_version",
        ):
            _require_non_empty_string(field_name, getattr(self, field_name))

        if len(self.anchor_ids) <= 1:
            if self.status is not MstBaselineStatus.TRIVIAL or self.edges:
                raise MstBaselineConnectorError(
                    "zero/one-anchor result must be TRIVIAL without edges"
                )
        elif self.diagnostics.connected_edge_count == len(self.edges):
            if self.status is not MstBaselineStatus.CONNECTED:
                raise MstBaselineConnectorError(
                    "fully realized MST must have CONNECTED status"
                )
        elif self.status is not MstBaselineStatus.INCOMPLETE:
            raise MstBaselineConnectorError(
                "MST with unrealized edges must have INCOMPLETE status"
            )

    @property
    def fully_connected(self) -> bool:
        return self.status in {MstBaselineStatus.CONNECTED, MstBaselineStatus.TRIVIAL}


@dataclass(frozen=True, slots=True)
class _SelectedPair:
    start: CandidateRoadAnchor
    target: CandidateRoadAnchor
    distance_m: float


class MstBaselineConnector:
    """Select a deterministic Euclidean MST and realize its edges with a pair connector."""

    name = "mst_euclidean"
    version = "1"

    def connect(
        self,
        *,
        working_srid: int,
        anchors: tuple[CandidateRoadAnchor, ...],
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        policy: MstBaselinePolicy | None = None,
        edge_connector: AnchorConnectionStrategy | None = None,
        edge_policy: LeastCostConnectorPolicy | None = None,
    ) -> MstBaselineResult:
        require_working_crs(working_srid)
        if not isinstance(anchors, tuple):
            raise MstBaselineConnectorError("anchors must be an immutable tuple")
        if any(not isinstance(anchor, CandidateRoadAnchor) for anchor in anchors):
            raise MstBaselineConnectorError(
                "anchors must contain only CandidateRoadAnchor values"
            )
        if not isinstance(suitability, WeightedSuitabilityResult):
            raise MstBaselineConnectorError(
                "suitability must be a WeightedSuitabilityResult"
            )
        if not isinstance(hard_mask, HardExclusionMask):
            raise MstBaselineConnectorError("hard_mask must be a HardExclusionMask")
        if suitability.grid.working_srid != working_srid:
            raise MstBaselineConnectorError(
                "suitability grid must use the requested working_srid"
            )
        if hard_mask.grid != suitability.grid:
            raise MstBaselineConnectorError(
                "hard_mask must use exactly the suitability grid"
            )
        if not np.array_equal(suitability.hard_excluded_mask, hard_mask.excluded):
            raise MstBaselineConnectorError(
                "suitability hard-excluded cells must match hard_mask exactly"
            )

        if policy is None:
            policy = MstBaselinePolicy()
        if not isinstance(policy, MstBaselinePolicy):
            raise MstBaselineConnectorError("policy must be a MstBaselinePolicy")
        if edge_connector is None:
            edge_connector = LeastCostConnector()
        if not isinstance(edge_connector, AnchorConnectionStrategy):
            raise MstBaselineConnectorError(
                "edge_connector must implement AnchorConnectionStrategy"
            )
        if edge_policy is not None and not isinstance(edge_policy, LeastCostConnectorPolicy):
            raise MstBaselineConnectorError(
                "edge_policy must be a LeastCostConnectorPolicy or None"
            )

        ordered = tuple(sorted(anchors, key=lambda anchor: anchor.anchor_id))
        _validate_anchor_set(ordered)
        if len(ordered) > policy.max_anchors:
            raise MstBaselineConnectorError(
                f"anchor count {len(ordered)} exceeds max_anchors={policy.max_anchors}"
            )
        required_evaluations = len(ordered) * (len(ordered) - 1) // 2
        if required_evaluations > policy.max_distance_evaluations:
            raise MstBaselineConnectorError(
                "complete-metric MST requires "
                f"{required_evaluations} distance evaluations, exceeding "
                f"max_distance_evaluations={policy.max_distance_evaluations}"
            )

        selected_pairs, evaluation_count = _select_mst_pairs(ordered)
        edges = tuple(
            self._realize_pair(
                pair=pair,
                working_srid=working_srid,
                suitability=suitability,
                hard_mask=hard_mask,
                edge_connector=edge_connector,
                edge_policy=edge_policy,
            )
            for pair in selected_pairs
        )
        diagnostics = _build_diagnostics(
            anchor_count=len(ordered),
            distance_evaluation_count=evaluation_count,
            edges=edges,
        )
        if len(ordered) <= 1:
            status = MstBaselineStatus.TRIVIAL
        elif diagnostics.connected_edge_count == len(edges):
            status = MstBaselineStatus.CONNECTED
        else:
            status = MstBaselineStatus.INCOMPLETE

        return MstBaselineResult(
            working_srid=working_srid,
            anchor_ids=tuple(anchor.anchor_id for anchor in ordered),
            edges=edges,
            status=status,
            diagnostics=diagnostics,
            strategy_name=self.name,
            strategy_version=self.version,
            edge_connector_version=edge_connector.version,
        )

    def _realize_pair(
        self,
        *,
        pair: _SelectedPair,
        working_srid: int,
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        edge_connector: AnchorConnectionStrategy,
        edge_policy: LeastCostConnectorPolicy | None,
    ) -> MstBaselineEdge:
        connection = edge_connector.connect(
            start=pair.start,
            target=pair.target,
            suitability=suitability,
            hard_mask=hard_mask,
            policy=edge_policy,
        )
        if not isinstance(connection, LeastCostConnectionResult):
            raise MstBaselineConnectorError(
                "edge_connector must return LeastCostConnectionResult values"
            )
        if connection.working_srid != working_srid:
            raise MstBaselineConnectorError(
                "edge connector result must use the baseline working_srid"
            )
        return MstBaselineEdge(
            start_anchor_id=pair.start.anchor_id,
            target_anchor_id=pair.target.anchor_id,
            baseline_distance_m=pair.distance_m,
            connection=connection,
        )


def _validate_anchor_set(anchors: tuple[CandidateRoadAnchor, ...]) -> None:
    anchor_ids = tuple(anchor.anchor_id for anchor in anchors)
    if len(set(anchor_ids)) != len(anchor_ids):
        raise MstBaselineConnectorError("anchor_id values must be unique")
    coordinates = tuple((anchor.point.x_m, anchor.point.y_m) for anchor in anchors)
    if len(set(coordinates)) != len(coordinates):
        raise MstBaselineConnectorError(
            "anchors must occupy unique metric point coordinates"
        )


def _select_mst_pairs(
    anchors: tuple[CandidateRoadAnchor, ...],
) -> tuple[tuple[_SelectedPair, ...], int]:
    if len(anchors) <= 1:
        return (), 0

    by_id = {anchor.anchor_id: anchor for anchor in anchors}
    root = anchors[0]
    remaining = {anchor.anchor_id for anchor in anchors[1:]}
    best: dict[str, tuple[float, str]] = {}
    evaluation_count = 0

    for anchor in anchors[1:]:
        best[anchor.anchor_id] = (_distance_m(root, anchor), root.anchor_id)
        evaluation_count += 1

    selected: list[_SelectedPair] = []
    while remaining:
        target_id = min(
            remaining,
            key=lambda anchor_id: (
                best[anchor_id][0],
                best[anchor_id][1],
                anchor_id,
            ),
        )
        distance_m, parent_id = best[target_id]
        first_id, second_id = sorted((parent_id, target_id))
        selected.append(
            _SelectedPair(
                start=by_id[first_id],
                target=by_id[second_id],
                distance_m=distance_m,
            )
        )
        remaining.remove(target_id)

        target = by_id[target_id]
        for candidate_id in sorted(remaining):
            candidate = by_id[candidate_id]
            candidate_distance = _distance_m(target, candidate)
            evaluation_count += 1
            previous_distance, previous_parent = best[candidate_id]
            if candidate_distance < previous_distance or (
                candidate_distance == previous_distance and target_id < previous_parent
            ):
                best[candidate_id] = (candidate_distance, target_id)

    selected.sort(key=lambda pair: (pair.start.anchor_id, pair.target.anchor_id))
    return tuple(selected), evaluation_count


def _distance_m(first: CandidateRoadAnchor, second: CandidateRoadAnchor) -> float:
    return math.hypot(
        second.point.x_m - first.point.x_m,
        second.point.y_m - first.point.y_m,
    )


def _build_diagnostics(
    *,
    anchor_count: int,
    distance_evaluation_count: int,
    edges: tuple[MstBaselineEdge, ...],
) -> MstBaselineDiagnostics:
    connected = tuple(edge for edge in edges if edge.connected)
    return MstBaselineDiagnostics(
        anchor_count=anchor_count,
        distance_evaluation_count=distance_evaluation_count,
        selected_edge_count=len(edges),
        connected_edge_count=len(connected),
        no_path_edge_count=sum(
            edge.connection.status is LeastCostConnectionStatus.NO_PATH for edge in edges
        ),
        search_limit_edge_count=sum(
            edge.connection.status is LeastCostConnectionStatus.SEARCH_LIMIT_REACHED
            for edge in edges
        ),
        total_baseline_distance_m=sum(edge.baseline_distance_m for edge in edges),
        total_connected_length_m=sum(
            edge.connection.length_m or 0.0 for edge in connected
        ),
        total_connected_cost=sum(edge.connection.total_cost or 0.0 for edge in connected),
    )


def _require_non_empty_string(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MstBaselineConnectorError(f"{field_name} must be a non-empty string")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MstBaselineConnectorError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MstBaselineConnectorError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(field_name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MstBaselineConnectorError(
            f"{field_name} must be a non-negative finite number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise MstBaselineConnectorError(
            f"{field_name} must be a non-negative finite number"
        )
    return number
