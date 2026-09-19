from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import floor, hypot, isfinite

from shapely.geometry import LineString

from core.urban_generator.domain import NetworkPoint
from core.urban_generator.roads.candidate_anchors import CandidateRoadAnchor
from core.urban_generator.roads.least_cost_connector import (
    LeastCostConnectionStatus,
    LeastCostConnector,
    LeastCostConnectorPolicy,
)
from core.urban_generator.roads.road_graph import RoadGraph
from core.urban_generator.roads.spatial_snapping import (
    DEFAULT_MAX_SNAP_TARGETS,
    SpatialSnapIndex,
    SpatialSnapTarget,
)
from core.urban_generator.suitability import HardExclusionMask, WeightedSuitabilityResult


class FixedNetworkAttachmentError(ValueError):
    """Raised when generated road components cannot be attached under the bounded contract."""


class FixedNetworkAttachmentStatus(StrEnum):
    CONNECTED = "CONNECTED"
    ALREADY_CONNECTED = "ALREADY_CONNECTED"
    NO_FIXED_NODE_IN_RANGE = "NO_FIXED_NODE_IN_RANGE"
    NO_PATH = "NO_PATH"
    SEARCH_LIMIT_REACHED = "SEARCH_LIMIT_REACHED"


@dataclass(frozen=True, slots=True)
class FixedNetworkAttachmentPolicy:
    max_distance_m: float
    max_attachments: int = 64
    max_fixed_nodes: int = DEFAULT_MAX_SNAP_TARGETS

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_distance_m, bool)
            or not isinstance(self.max_distance_m, (int, float))
            or not isfinite(self.max_distance_m)
            or self.max_distance_m < 0.0
        ):
            raise FixedNetworkAttachmentError(
                "max_distance_m must be a finite non-negative number"
            )
        for field_name in ("max_attachments", "max_fixed_nodes"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise FixedNetworkAttachmentError(
                    f"{field_name} must be a positive integer"
                )


@dataclass(frozen=True, slots=True)
class FixedNetworkAttachment:
    component_index: int
    component_anchor_ids: tuple[str, ...]
    selected_anchor_id: str
    fixed_node_id: str | None
    status: FixedNetworkAttachmentStatus
    geometry: LineString | None
    length_m: float | None
    visited_cell_count: int

    @property
    def attached(self) -> bool:
        return self.status in {
            FixedNetworkAttachmentStatus.CONNECTED,
            FixedNetworkAttachmentStatus.ALREADY_CONNECTED,
        }


@dataclass(frozen=True, slots=True)
class FixedNetworkAttachmentDiagnostics:
    component_count: int
    attached_component_count: int
    already_connected_count: int
    no_fixed_node_count: int
    no_path_count: int
    search_limit_reached_count: int
    valid_fixed_node_count: int
    routed_attachment_count: int
    visited_cell_count: int


@dataclass(frozen=True, slots=True)
class FixedNetworkAttachmentResult:
    attachments: tuple[FixedNetworkAttachment, ...]
    diagnostics: FixedNetworkAttachmentDiagnostics

    @property
    def complete(self) -> bool:
        return self.diagnostics.attached_component_count == self.diagnostics.component_count

    @property
    def generated_geometries(self) -> tuple[tuple[str, LineString], ...]:
        return tuple(
            (
                f"generated:fixed_attachment:{attachment.component_index:04d}",
                attachment.geometry,
            )
            for attachment in self.attachments
            if attachment.geometry is not None and attachment.attached
        )


@dataclass(frozen=True, slots=True)
class _FixedTarget:
    node_id: str
    point: NetworkPoint
    raster_row: int
    raster_col: int
    cell_center: NetworkPoint


class FixedNetworkAttachmentConnector:
    """Attach every generated anchor component to the existing fixed road graph.

    Nearest fixed nodes are discovered through SpatialSnapIndex. Routing still uses the existing
    least-cost connector by targeting the valid raster cell that contains the fixed graph node.
    A final segment from that cell center to the exact fixed node stays inside that raster cell,
    making final semantic noding connect generated geometry to the fixed graph exactly.
    """

    def __init__(
        self,
        *,
        policy: FixedNetworkAttachmentPolicy,
        pair_connector: LeastCostConnector | None = None,
    ) -> None:
        if not isinstance(policy, FixedNetworkAttachmentPolicy):
            raise FixedNetworkAttachmentError(
                "policy must be a FixedNetworkAttachmentPolicy"
            )
        self.policy = policy
        self._pair_connector = pair_connector or LeastCostConnector()

    def attach(
        self,
        *,
        anchors: tuple[CandidateRoadAnchor, ...],
        connected_pairs: tuple[tuple[str, str], ...],
        fixed_graph: RoadGraph,
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        pair_policy: LeastCostConnectorPolicy | None = None,
    ) -> FixedNetworkAttachmentResult:
        ordered_anchors = _validate_anchors(anchors)
        if not isinstance(fixed_graph, RoadGraph):
            raise FixedNetworkAttachmentError("fixed_graph must be a RoadGraph")
        if fixed_graph.working_crs.srid != suitability.grid.working_srid:
            raise FixedNetworkAttachmentError(
                "fixed graph and suitability must use the same working_srid"
            )
        if hard_mask.grid != suitability.grid:
            raise FixedNetworkAttachmentError(
                "hard_mask must use exactly the suitability grid"
            )

        components = _components(ordered_anchors, connected_pairs)
        if len(components) > self.policy.max_attachments:
            raise FixedNetworkAttachmentError(
                "generated component count exceeds max_attachments"
            )
        fixed_targets = _fixed_targets(
            graph=fixed_graph,
            suitability=suitability,
            hard_mask=hard_mask,
        )
        index = SpatialSnapIndex(
            targets=tuple(
                SpatialSnapTarget(target_id=target.node_id, point=target.point)
                for target in fixed_targets
            ),
            working_srid=suitability.grid.working_srid,
            max_targets=self.policy.max_fixed_nodes,
        )
        fixed_by_id = {target.node_id: target for target in fixed_targets}
        anchor_by_id = {anchor.anchor_id: anchor for anchor in ordered_anchors}

        attachments = tuple(
            self._attach_component(
                component_index=index_value,
                component_anchor_ids=component,
                anchor_by_id=anchor_by_id,
                fixed_index=index,
                fixed_by_id=fixed_by_id,
                suitability=suitability,
                hard_mask=hard_mask,
                pair_policy=pair_policy,
            )
            for index_value, component in enumerate(components)
        )
        return FixedNetworkAttachmentResult(
            attachments=attachments,
            diagnostics=_diagnostics(
                attachments=attachments,
                valid_fixed_node_count=len(fixed_targets),
            ),
        )

    def _attach_component(
        self,
        *,
        component_index: int,
        component_anchor_ids: tuple[str, ...],
        anchor_by_id: dict[str, CandidateRoadAnchor],
        fixed_index: SpatialSnapIndex,
        fixed_by_id: dict[str, _FixedTarget],
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        pair_policy: LeastCostConnectorPolicy | None,
    ) -> FixedNetworkAttachment:
        best: tuple[float, str, str] | None = None
        for anchor_id in component_anchor_ids:
            anchor = anchor_by_id[anchor_id]
            match = fixed_index.snap(
                anchor.point,
                tolerance_m=self.policy.max_distance_m,
            )
            if match is None:
                continue
            candidate = (match.distance_m, anchor_id, match.target.target_id)
            if best is None or candidate < best:
                best = candidate

        if best is None:
            return FixedNetworkAttachment(
                component_index=component_index,
                component_anchor_ids=component_anchor_ids,
                selected_anchor_id=component_anchor_ids[0],
                fixed_node_id=None,
                status=FixedNetworkAttachmentStatus.NO_FIXED_NODE_IN_RANGE,
                geometry=None,
                length_m=None,
                visited_cell_count=0,
            )

        _distance, anchor_id, node_id = best
        anchor = anchor_by_id[anchor_id]
        fixed = fixed_by_id[node_id]
        target_anchor = CandidateRoadAnchor(
            anchor_id=f"fixed-node:{node_id}",
            point=fixed.cell_center,
            zone_class=anchor.zone_class,
            zoning_cell_index=anchor.zoning_cell_index,
            raster_row=fixed.raster_row,
            raster_col=fixed.raster_col,
            suitability_score=float(
                suitability.scores[fixed.raster_row, fixed.raster_col]
            ),
        )

        if (
            anchor.raster_row == target_anchor.raster_row
            and anchor.raster_col == target_anchor.raster_col
        ):
            return _same_cell_attachment(
                component_index=component_index,
                component_anchor_ids=component_anchor_ids,
                anchor=anchor,
                fixed=fixed,
            )

        connection = self._pair_connector.connect(
            start=anchor,
            target=target_anchor,
            suitability=suitability,
            hard_mask=hard_mask,
            policy=pair_policy,
        )
        if connection.status is not LeastCostConnectionStatus.CONNECTED:
            status = (
                FixedNetworkAttachmentStatus.NO_PATH
                if connection.status is LeastCostConnectionStatus.NO_PATH
                else FixedNetworkAttachmentStatus.SEARCH_LIMIT_REACHED
            )
            return FixedNetworkAttachment(
                component_index=component_index,
                component_anchor_ids=component_anchor_ids,
                selected_anchor_id=anchor.anchor_id,
                fixed_node_id=fixed.node_id,
                status=status,
                geometry=None,
                length_m=None,
                visited_cell_count=connection.visited_cell_count,
            )

        assert connection.geometry is not None
        geometry = _extend_to_fixed_node(connection.geometry, fixed.point)
        return FixedNetworkAttachment(
            component_index=component_index,
            component_anchor_ids=component_anchor_ids,
            selected_anchor_id=anchor.anchor_id,
            fixed_node_id=fixed.node_id,
            status=FixedNetworkAttachmentStatus.CONNECTED,
            geometry=geometry,
            length_m=float(geometry.length),
            visited_cell_count=connection.visited_cell_count,
        )


def _validate_anchors(
    anchors: tuple[CandidateRoadAnchor, ...],
) -> tuple[CandidateRoadAnchor, ...]:
    if not isinstance(anchors, tuple):
        raise FixedNetworkAttachmentError("anchors must be an immutable tuple")
    if any(not isinstance(anchor, CandidateRoadAnchor) for anchor in anchors):
        raise FixedNetworkAttachmentError(
            "anchors must contain only CandidateRoadAnchor values"
        )
    ordered = tuple(sorted(anchors, key=lambda item: item.anchor_id))
    ids = tuple(anchor.anchor_id for anchor in ordered)
    if len(ids) != len(set(ids)):
        raise FixedNetworkAttachmentError("anchor ids must be unique")
    return ordered


def _components(
    anchors: tuple[CandidateRoadAnchor, ...],
    connected_pairs: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, ...], ...]:
    if not isinstance(connected_pairs, tuple):
        raise FixedNetworkAttachmentError(
            "connected_pairs must be an immutable tuple"
        )
    ids = tuple(anchor.anchor_id for anchor in anchors)
    known = set(ids)
    parent = {anchor_id: anchor_id for anchor_id in ids}
    for pair in connected_pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise FixedNetworkAttachmentError(
                "connected_pairs entries must be two-item tuples"
            )
        first, second = pair
        if first not in known or second not in known:
            raise FixedNetworkAttachmentError(
                "connected pair references unknown anchor"
            )
        if first == second:
            raise FixedNetworkAttachmentError(
                "connected pair endpoints must differ"
            )
        _union(parent, first, second)

    groups: dict[str, list[str]] = {}
    for anchor_id in ids:
        groups.setdefault(_find(parent, anchor_id), []).append(anchor_id)
    return tuple(
        sorted(
            (tuple(sorted(group)) for group in groups.values()),
            key=lambda group: group[0],
        )
    )


def _fixed_targets(
    *,
    graph: RoadGraph,
    suitability: WeightedSuitabilityResult,
    hard_mask: HardExclusionMask,
) -> tuple[_FixedTarget, ...]:
    targets: list[_FixedTarget] = []
    for node in sorted(graph.nodes, key=lambda item: item.node.node_id):
        if not node.is_fixed:
            continue
        cell = _cell_for_point(node.point, suitability)
        if cell is None:
            continue
        row, col = cell
        if not bool(suitability.valid_mask[row, col]) or bool(hard_mask.excluded[row, col]):
            continue
        targets.append(
            _FixedTarget(
                node_id=node.node.node_id,
                point=node.point,
                raster_row=row,
                raster_col=col,
                cell_center=_cell_center(suitability, row=row, col=col),
            )
        )
    return tuple(targets)


def _cell_for_point(
    point: NetworkPoint,
    suitability: WeightedSuitabilityResult,
) -> tuple[int, int] | None:
    grid = suitability.grid
    min_x, min_y, max_x, max_y = grid.bounds
    if (
        point.x_m < min_x
        or point.x_m > max_x
        or point.y_m < min_y
        or point.y_m > max_y
    ):
        return None
    col = floor((point.x_m - min_x) / grid.cell_width_m)
    row = floor((max_y - point.y_m) / grid.cell_height_m)
    col = min(grid.width - 1, max(0, col))
    row = min(grid.height - 1, max(0, row))
    return row, col


def _cell_center(
    suitability: WeightedSuitabilityResult,
    *,
    row: int,
    col: int,
) -> NetworkPoint:
    grid = suitability.grid
    min_x, _min_y, _max_x, max_y = grid.bounds
    return NetworkPoint(
        x_m=min_x + (col + 0.5) * grid.cell_width_m,
        y_m=max_y - (row + 0.5) * grid.cell_height_m,
    )


def _same_cell_attachment(
    *,
    component_index: int,
    component_anchor_ids: tuple[str, ...],
    anchor: CandidateRoadAnchor,
    fixed: _FixedTarget,
) -> FixedNetworkAttachment:
    distance = hypot(
        anchor.point.x_m - fixed.point.x_m,
        anchor.point.y_m - fixed.point.y_m,
    )
    if distance <= 1e-9:
        return FixedNetworkAttachment(
            component_index=component_index,
            component_anchor_ids=component_anchor_ids,
            selected_anchor_id=anchor.anchor_id,
            fixed_node_id=fixed.node_id,
            status=FixedNetworkAttachmentStatus.ALREADY_CONNECTED,
            geometry=None,
            length_m=0.0,
            visited_cell_count=0,
        )
    geometry = LineString(
        (
            (anchor.point.x_m, anchor.point.y_m),
            (fixed.point.x_m, fixed.point.y_m),
        )
    )
    return FixedNetworkAttachment(
        component_index=component_index,
        component_anchor_ids=component_anchor_ids,
        selected_anchor_id=anchor.anchor_id,
        fixed_node_id=fixed.node_id,
        status=FixedNetworkAttachmentStatus.CONNECTED,
        geometry=geometry,
        length_m=float(geometry.length),
        visited_cell_count=0,
    )


def _extend_to_fixed_node(
    geometry: LineString,
    fixed_point: NetworkPoint,
) -> LineString:
    coordinates = list(geometry.coords)
    last_x, last_y = coordinates[-1][:2]
    if hypot(last_x - fixed_point.x_m, last_y - fixed_point.y_m) <= 1e-9:
        return geometry
    coordinates.append((fixed_point.x_m, fixed_point.y_m))
    result = LineString(coordinates)
    if result.is_empty or not result.is_valid or result.length <= 0.0:
        raise FixedNetworkAttachmentError(
            "attachment extension to fixed node produced invalid geometry"
        )
    return result


def _diagnostics(
    *,
    attachments: tuple[FixedNetworkAttachment, ...],
    valid_fixed_node_count: int,
) -> FixedNetworkAttachmentDiagnostics:
    return FixedNetworkAttachmentDiagnostics(
        component_count=len(attachments),
        attached_component_count=sum(item.attached for item in attachments),
        already_connected_count=sum(
            item.status is FixedNetworkAttachmentStatus.ALREADY_CONNECTED
            for item in attachments
        ),
        no_fixed_node_count=sum(
            item.status is FixedNetworkAttachmentStatus.NO_FIXED_NODE_IN_RANGE
            for item in attachments
        ),
        no_path_count=sum(
            item.status is FixedNetworkAttachmentStatus.NO_PATH
            for item in attachments
        ),
        search_limit_reached_count=sum(
            item.status is FixedNetworkAttachmentStatus.SEARCH_LIMIT_REACHED
            for item in attachments
        ),
        valid_fixed_node_count=valid_fixed_node_count,
        routed_attachment_count=sum(
            item.geometry is not None and item.attached for item in attachments
        ),
        visited_cell_count=sum(item.visited_cell_count for item in attachments),
    )


def _find(parent: dict[str, str], value: str) -> str:
    root = value
    while parent[root] != root:
        root = parent[root]
    while parent[value] != value:
        next_value = parent[value]
        parent[value] = root
        value = next_value
    return root


def _union(parent: dict[str, str], first: str, second: str) -> None:
    first_root = _find(parent, first)
    second_root = _find(parent, second)
    if first_root == second_root:
        return
    lower, higher = sorted((first_root, second_root))
    parent[higher] = lower
