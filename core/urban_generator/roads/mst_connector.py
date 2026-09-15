from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from core.urban_generator.domain import require_working_crs
from core.urban_generator.roads.candidate_anchors import (
    DEFAULT_MAX_ROAD_ANCHORS,
    CandidateRoadAnchor,
)
from core.urban_generator.roads.least_cost_connector import (
    LeastCostConnectionResult,
    LeastCostConnectionStatus,
    LeastCostConnector,
    LeastCostConnectorPolicy,
)
from core.urban_generator.suitability import HardExclusionMask, WeightedSuitabilityResult

DEFAULT_MAX_MST_ANCHORS = DEFAULT_MAX_ROAD_ANCHORS


class AnchorConnectivityError(ValueError):
    """Raised when an anchor-connectivity strategy violates its core contract."""


@dataclass(frozen=True, slots=True)
class MSTBaselineConnectorPolicy:
    """Explicit work bound for the deterministic MST baseline strategy."""

    max_anchors: int = DEFAULT_MAX_MST_ANCHORS

    def __post_init__(self) -> None:
        _require_positive_int("max_anchors", self.max_anchors)


@dataclass(frozen=True, slots=True)
class AnchorConnectivityEdge:
    """One planned topology edge and its delegated least-cost routing result."""

    edge_index: int
    start_anchor_id: str
    target_anchor_id: str
    direct_distance_m: float
    connection: LeastCostConnectionResult

    def __post_init__(self) -> None:
        _require_non_negative_int("edge_index", self.edge_index)
        _require_non_empty_string("start_anchor_id", self.start_anchor_id)
        _require_non_empty_string("target_anchor_id", self.target_anchor_id)
        if self.start_anchor_id == self.target_anchor_id:
            raise AnchorConnectivityError("connectivity edge endpoints must differ")
        _require_non_negative_finite("direct_distance_m", self.direct_distance_m)
        if not isinstance(self.connection, LeastCostConnectionResult):
            raise AnchorConnectivityError(
                "connection must be a LeastCostConnectionResult"
            )
        if self.connection.start_anchor_id != self.start_anchor_id:
            raise AnchorConnectivityError(
                "connection start_anchor_id must match topology edge"
            )
        if self.connection.target_anchor_id != self.target_anchor_id:
            raise AnchorConnectivityError(
                "connection target_anchor_id must match topology edge"
            )

    @property
    def connected(self) -> bool:
        return self.connection.connected


@dataclass(frozen=True, slots=True)
class AnchorConnectivityDiagnostics:
    """Stable counts and cost totals for one anchor-connectivity strategy run."""

    anchor_count: int
    planned_edge_count: int
    connected_edge_count: int
    no_path_edge_count: int
    search_limit_reached_edge_count: int
    visited_cell_count: int
    total_direct_distance_m: float
    total_routed_length_m: float
    total_cost: float
    complete: bool

    def __post_init__(self) -> None:
        for field_name in (
            "anchor_count",
            "planned_edge_count",
            "connected_edge_count",
            "no_path_edge_count",
            "search_limit_reached_edge_count",
            "visited_cell_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        expected_edges = max(0, self.anchor_count - 1)
        if self.planned_edge_count != expected_edges:
            raise AnchorConnectivityError(
                "planned_edge_count must equal max(anchor_count - 1, 0)"
            )
        status_total = (
            self.connected_edge_count
            + self.no_path_edge_count
            + self.search_limit_reached_edge_count
        )
        if status_total != self.planned_edge_count:
            raise AnchorConnectivityError(
                "connection status counts must sum to planned_edge_count"
            )
        for field_name in (
            "total_direct_distance_m",
            "total_routed_length_m",
            "total_cost",
        ):
            _require_non_negative_finite(field_name, getattr(self, field_name))
        if not isinstance(self.complete, bool):
            raise AnchorConnectivityError("complete must be boolean")
        if self.complete != (self.connected_edge_count == self.planned_edge_count):
            raise AnchorConnectivityError(
                "complete must agree with connected_edge_count"
            )


@dataclass(frozen=True, slots=True)
class AnchorConnectivityResult:
    """Backend-independent output shared by pluggable anchor strategies."""

    working_srid: int
    anchor_ids: tuple[str, ...]
    edges: tuple[AnchorConnectivityEdge, ...]
    diagnostics: AnchorConnectivityDiagnostics
    strategy_name: str
    strategy_version: str

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        if not isinstance(self.anchor_ids, tuple):
            raise AnchorConnectivityError("anchor_ids must be an immutable tuple")
        for anchor_id in self.anchor_ids:
            _require_non_empty_string("anchor_id", anchor_id)
        if tuple(sorted(self.anchor_ids)) != self.anchor_ids:
            raise AnchorConnectivityError("anchor_ids must be sorted deterministically")
        if len(set(self.anchor_ids)) != len(self.anchor_ids):
            raise AnchorConnectivityError("anchor_ids must be unique")
        if not isinstance(self.edges, tuple):
            raise AnchorConnectivityError("edges must be an immutable tuple")
        if any(not isinstance(edge, AnchorConnectivityEdge) for edge in self.edges):
            raise AnchorConnectivityError(
                "edges must contain only AnchorConnectivityEdge values"
            )
        if tuple(edge.edge_index for edge in self.edges) != tuple(range(len(self.edges))):
            raise AnchorConnectivityError("edge indexes must be contiguous from zero")
        anchor_id_set = set(self.anchor_ids)
        for edge in self.edges:
            if edge.start_anchor_id not in anchor_id_set:
                raise AnchorConnectivityError("edge start anchor must exist in anchor_ids")
            if edge.target_anchor_id not in anchor_id_set:
                raise AnchorConnectivityError("edge target anchor must exist in anchor_ids")
            if edge.connection.working_srid != self.working_srid:
                raise AnchorConnectivityError(
                    "edge connection working_srid must match result working_srid"
                )
        if not isinstance(self.diagnostics, AnchorConnectivityDiagnostics):
            raise AnchorConnectivityError(
                "diagnostics must be AnchorConnectivityDiagnostics"
            )
        if self.diagnostics.anchor_count != len(self.anchor_ids):
            raise AnchorConnectivityError(
                "diagnostics anchor_count must match anchor_ids"
            )
        if self.diagnostics.planned_edge_count != len(self.edges):
            raise AnchorConnectivityError(
                "diagnostics planned_edge_count must match edges"
            )
        _require_non_empty_string("strategy_name", self.strategy_name)
        _require_non_empty_string("strategy_version", self.strategy_version)

    @property
    def complete(self) -> bool:
        return self.diagnostics.complete


@runtime_checkable
class AnchorConnectivityStrategy(Protocol):
    """Pluggable strategy port for baseline anchor connectivity."""

    name: str
    version: str

    def connect(
        self,
        *,
        anchors: tuple[CandidateRoadAnchor, ...],
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        pair_policy: LeastCostConnectorPolicy | None = None,
    ) -> AnchorConnectivityResult:
        """Plan anchor topology and route its selected pairs."""

        ...


class _AnchorPairConnector(Protocol):
    def connect(
        self,
        *,
        start: CandidateRoadAnchor,
        target: CandidateRoadAnchor,
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        policy: LeastCostConnectorPolicy | None = None,
    ) -> LeastCostConnectionResult: ...


class MSTBaselineConnector:
    """Build deterministic Euclidean MST topology, then route only its N-1 pairs."""

    name = "mst-baseline"
    version = "1"

    def __init__(
        self,
        *,
        policy: MSTBaselineConnectorPolicy | None = None,
        pair_connector: _AnchorPairConnector | None = None,
    ) -> None:
        if policy is None:
            policy = MSTBaselineConnectorPolicy()
        if not isinstance(policy, MSTBaselineConnectorPolicy):
            raise AnchorConnectivityError(
                "policy must be an MSTBaselineConnectorPolicy"
            )
        if pair_connector is not None and not callable(
            getattr(pair_connector, "connect", None)
        ):
            raise AnchorConnectivityError("pair_connector must expose connect()")
        self._policy = policy
        self._pair_connector = pair_connector or LeastCostConnector()

    @property
    def policy(self) -> MSTBaselineConnectorPolicy:
        return self._policy

    def connect(
        self,
        *,
        anchors: tuple[CandidateRoadAnchor, ...],
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        pair_policy: LeastCostConnectorPolicy | None = None,
    ) -> AnchorConnectivityResult:
        _validate_inputs(
            anchors=anchors,
            suitability=suitability,
            hard_mask=hard_mask,
            max_anchors=self._policy.max_anchors,
        )
        if pair_policy is not None and not isinstance(
            pair_policy, LeastCostConnectorPolicy
        ):
            raise AnchorConnectivityError(
                "pair_policy must be a LeastCostConnectorPolicy"
            )

        ordered_anchors = tuple(sorted(anchors, key=lambda anchor: anchor.anchor_id))
        planned_pairs = _minimum_spanning_pairs(ordered_anchors)
        edges: list[AnchorConnectivityEdge] = []
        for edge_index, (start_index, target_index, direct_distance_m) in enumerate(
            planned_pairs
        ):
            start = ordered_anchors[start_index]
            target = ordered_anchors[target_index]
            connection = self._pair_connector.connect(
                start=start,
                target=target,
                suitability=suitability,
                hard_mask=hard_mask,
                policy=pair_policy,
            )
            if not isinstance(connection, LeastCostConnectionResult):
                raise AnchorConnectivityError(
                    "pair_connector must return LeastCostConnectionResult"
                )
            edges.append(
                AnchorConnectivityEdge(
                    edge_index=edge_index,
                    start_anchor_id=start.anchor_id,
                    target_anchor_id=target.anchor_id,
                    direct_distance_m=direct_distance_m,
                    connection=connection,
                )
            )

        diagnostics = _build_diagnostics(
            anchor_count=len(ordered_anchors),
            edges=tuple(edges),
        )
        return AnchorConnectivityResult(
            working_srid=suitability.grid.working_srid,
            anchor_ids=tuple(anchor.anchor_id for anchor in ordered_anchors),
            edges=tuple(edges),
            diagnostics=diagnostics,
            strategy_name=self.name,
            strategy_version=self.version,
        )


def _validate_inputs(
    *,
    anchors: tuple[CandidateRoadAnchor, ...],
    suitability: WeightedSuitabilityResult,
    hard_mask: HardExclusionMask,
    max_anchors: int,
) -> None:
    if not isinstance(anchors, tuple):
        raise AnchorConnectivityError("anchors must be an immutable tuple")
    if any(not isinstance(anchor, CandidateRoadAnchor) for anchor in anchors):
        raise AnchorConnectivityError(
            "anchors must contain only CandidateRoadAnchor values"
        )
    if len(anchors) > max_anchors:
        raise AnchorConnectivityError(
            f"MST anchor limit exceeded: {len(anchors)} > {max_anchors}"
        )
    if len({anchor.anchor_id for anchor in anchors}) != len(anchors):
        raise AnchorConnectivityError("anchor_id values must be unique")
    raster_cells = tuple((anchor.raster_row, anchor.raster_col) for anchor in anchors)
    if len(set(raster_cells)) != len(raster_cells):
        raise AnchorConnectivityError("anchors must occupy unique raster cells")
    if not isinstance(suitability, WeightedSuitabilityResult):
        raise AnchorConnectivityError(
            "suitability must be a WeightedSuitabilityResult"
        )
    if not isinstance(hard_mask, HardExclusionMask):
        raise AnchorConnectivityError("hard_mask must be a HardExclusionMask")
    require_working_crs(suitability.grid.working_srid)
    if hard_mask.grid != suitability.grid:
        raise AnchorConnectivityError(
            "hard_mask must use exactly the suitability grid"
        )
    if not np.array_equal(suitability.hard_excluded_mask, hard_mask.excluded):
        raise AnchorConnectivityError(
            "suitability hard-excluded cells must match hard_mask exactly"
        )


def _minimum_spanning_pairs(
    anchors: tuple[CandidateRoadAnchor, ...],
) -> tuple[tuple[int, int, float], ...]:
    if len(anchors) <= 1:
        return ()

    remaining = set(range(1, len(anchors)))
    best: dict[int, tuple[float, str, str, int]] = {}
    for target_index in remaining:
        best[target_index] = _edge_key(
            anchors=anchors,
            start_index=0,
            target_index=target_index,
        )

    selected: list[tuple[int, int, float]] = []
    while remaining:
        target_index = min(remaining, key=best.__getitem__)
        direct_distance_m, _start_id, _target_id, start_index = best[target_index]
        selected.append((start_index, target_index, direct_distance_m))
        remaining.remove(target_index)

        for candidate_target_index in remaining:
            candidate = _edge_key(
                anchors=anchors,
                start_index=target_index,
                target_index=candidate_target_index,
            )
            if candidate < best[candidate_target_index]:
                best[candidate_target_index] = candidate

    return tuple(selected)


def _edge_key(
    *,
    anchors: tuple[CandidateRoadAnchor, ...],
    start_index: int,
    target_index: int,
) -> tuple[float, str, str, int]:
    start = anchors[start_index]
    target = anchors[target_index]
    return (
        math.hypot(
            target.point.x_m - start.point.x_m,
            target.point.y_m - start.point.y_m,
        ),
        start.anchor_id,
        target.anchor_id,
        start_index,
    )


def _build_diagnostics(
    *,
    anchor_count: int,
    edges: tuple[AnchorConnectivityEdge, ...],
) -> AnchorConnectivityDiagnostics:
    connected_edge_count = sum(
        edge.connection.status is LeastCostConnectionStatus.CONNECTED for edge in edges
    )
    no_path_edge_count = sum(
        edge.connection.status is LeastCostConnectionStatus.NO_PATH for edge in edges
    )
    search_limit_reached_edge_count = sum(
        edge.connection.status is LeastCostConnectionStatus.SEARCH_LIMIT_REACHED
        for edge in edges
    )
    total_routed_length_m = sum(
        edge.connection.length_m or 0.0 for edge in edges
    )
    total_cost = sum(edge.connection.total_cost or 0.0 for edge in edges)
    return AnchorConnectivityDiagnostics(
        anchor_count=anchor_count,
        planned_edge_count=len(edges),
        connected_edge_count=connected_edge_count,
        no_path_edge_count=no_path_edge_count,
        search_limit_reached_edge_count=search_limit_reached_edge_count,
        visited_cell_count=sum(edge.connection.visited_cell_count for edge in edges),
        total_direct_distance_m=sum(edge.direct_distance_m for edge in edges),
        total_routed_length_m=total_routed_length_m,
        total_cost=total_cost,
        complete=connected_edge_count == len(edges),
    )


def _require_non_empty_string(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AnchorConnectivityError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise AnchorConnectivityError(f"{field_name} must not contain line breaks")


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnchorConnectivityError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise AnchorConnectivityError(
            f"{field_name} must be a non-negative finite number"
        )
    return number


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AnchorConnectivityError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AnchorConnectivityError(f"{field_name} must be a non-negative integer")
