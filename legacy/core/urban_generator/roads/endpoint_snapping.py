from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry

from core.urban_generator.domain import NetworkPoint
from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.roads.semantic_noding import SemanticRoad
from core.urban_generator.roads.spatial_snapping import (
    DEFAULT_MAX_SNAP_TARGETS,
    SpatialSnapIndex,
    SpatialSnapTarget,
)


class EndpointRoadSnappingError(ValueError):
    """Raised when deterministic road-endpoint snapping cannot preserve valid road geometry."""


@dataclass(frozen=True, slots=True)
class EndpointRoadSnappingPolicy:
    tolerance_m: float
    max_targets: int = DEFAULT_MAX_SNAP_TARGETS
    max_candidate_pairs: int = 2_000_000

    def __post_init__(self) -> None:
        if (
            isinstance(self.tolerance_m, bool)
            or not isinstance(self.tolerance_m, (int, float))
            or not isfinite(self.tolerance_m)
            or self.tolerance_m < 0.0
        ):
            raise EndpointRoadSnappingError(
                "tolerance_m must be a finite non-negative number"
            )
        for field_name in ("max_targets", "max_candidate_pairs"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise EndpointRoadSnappingError(
                    f"{field_name} must be a positive integer"
                )


@dataclass(frozen=True, slots=True)
class EndpointRoadSnappingDiagnostics:
    road_count: int
    endpoint_count: int
    candidate_pair_count: int
    cluster_count: int
    snapped_endpoint_count: int


@dataclass(frozen=True, slots=True)
class EndpointRoadSnappingResult:
    roads: tuple[SemanticRoad, ...]
    diagnostics: EndpointRoadSnappingDiagnostics


@dataclass(frozen=True, slots=True)
class _EndpointRef:
    target_id: str
    road_id: str
    part_index: int
    at_start: bool
    point: NetworkPoint


class EndpointRoadSnapper:
    """Cluster near-coincident road endpoints using the existing indexed snapping primitive.

    Connected components of the tolerance graph choose the lexicographically smallest target id
    as the exact representative coordinate. This avoids asymmetric A->B / B->A endpoint swaps.
    Interior vertices are never modified.
    """

    def __init__(
        self,
        *,
        working_srid: int,
        policy: EndpointRoadSnappingPolicy,
    ) -> None:
        require_working_crs(working_srid)
        if not isinstance(policy, EndpointRoadSnappingPolicy):
            raise EndpointRoadSnappingError(
                "policy must be an EndpointRoadSnappingPolicy"
            )
        self.working_srid = working_srid
        self.policy = policy

    def snap(self, roads: tuple[SemanticRoad, ...]) -> EndpointRoadSnappingResult:
        if not isinstance(roads, tuple):
            raise EndpointRoadSnappingError("roads must be an immutable tuple")
        if any(not isinstance(road, SemanticRoad) for road in roads):
            raise EndpointRoadSnappingError(
                "roads must contain only SemanticRoad values"
            )
        ordered = tuple(sorted(roads, key=lambda road: road.road_id))
        road_ids = tuple(road.road_id for road in ordered)
        if len(road_ids) != len(set(road_ids)):
            raise EndpointRoadSnappingError("road ids must be unique")

        endpoints = _collect_endpoints(ordered)
        targets = tuple(
            SpatialSnapTarget(target_id=item.target_id, point=item.point)
            for item in endpoints
        )
        index = SpatialSnapIndex(
            targets=targets,
            working_srid=self.working_srid,
            max_targets=self.policy.max_targets,
        )
        parent = {item.target_id: item.target_id for item in endpoints}
        candidate_pair_count = 0

        for item in endpoints:
            matches = index.candidates(
                item.point,
                tolerance_m=self.policy.tolerance_m,
                exclude_target_ids=frozenset({item.target_id}),
            )
            for match in matches:
                other_id = match.target.target_id
                if item.target_id >= other_id:
                    continue
                candidate_pair_count += 1
                if candidate_pair_count > self.policy.max_candidate_pairs:
                    raise EndpointRoadSnappingError(
                        "endpoint snap candidate-pair limit exceeded"
                    )
                _union(parent, item.target_id, other_id)

        clusters: dict[str, list[_EndpointRef]] = {}
        by_id = {item.target_id: item for item in endpoints}
        for item in endpoints:
            root = _find(parent, item.target_id)
            clusters.setdefault(root, []).append(item)

        representative_by_target: dict[str, NetworkPoint] = {}
        snapped_endpoint_count = 0
        for members in clusters.values():
            representative_id = min(member.target_id for member in members)
            representative = by_id[representative_id].point
            for member in members:
                representative_by_target[member.target_id] = representative
                if member.point != representative:
                    snapped_endpoint_count += 1

        snapped_roads = tuple(
            _rebuild_road(road, representative_by_target)
            for road in ordered
        )
        return EndpointRoadSnappingResult(
            roads=snapped_roads,
            diagnostics=EndpointRoadSnappingDiagnostics(
                road_count=len(ordered),
                endpoint_count=len(endpoints),
                candidate_pair_count=candidate_pair_count,
                cluster_count=len(clusters),
                snapped_endpoint_count=snapped_endpoint_count,
            ),
        )


def _collect_endpoints(roads: tuple[SemanticRoad, ...]) -> tuple[_EndpointRef, ...]:
    items: list[_EndpointRef] = []
    for road in roads:
        for part_index, line in enumerate(_parts(road.geometry)):
            start = line.coords[0]
            end = line.coords[-1]
            items.extend(
                (
                    _EndpointRef(
                        target_id=f"{road.road_id}::part:{part_index}::start",
                        road_id=road.road_id,
                        part_index=part_index,
                        at_start=True,
                        point=NetworkPoint(x_m=float(start[0]), y_m=float(start[1])),
                    ),
                    _EndpointRef(
                        target_id=f"{road.road_id}::part:{part_index}::end",
                        road_id=road.road_id,
                        part_index=part_index,
                        at_start=False,
                        point=NetworkPoint(x_m=float(end[0]), y_m=float(end[1])),
                    ),
                )
            )
    return tuple(items)


def _rebuild_road(
    road: SemanticRoad,
    representative_by_target: dict[str, NetworkPoint],
) -> SemanticRoad:
    rebuilt: list[LineString] = []
    for part_index, line in enumerate(_parts(road.geometry)):
        coordinates = list(line.coords)
        start = representative_by_target[
            f"{road.road_id}::part:{part_index}::start"
        ]
        end = representative_by_target[
            f"{road.road_id}::part:{part_index}::end"
        ]
        coordinates[0] = (start.x_m, start.y_m)
        coordinates[-1] = (end.x_m, end.y_m)
        rebuilt_line = LineString(coordinates)
        if rebuilt_line.is_empty or not rebuilt_line.is_valid or rebuilt_line.length <= 0.0:
            raise EndpointRoadSnappingError(
                f"endpoint snapping collapsed or invalidated road {road.road_id!r} "
                f"part {part_index}"
            )
        rebuilt.append(rebuilt_line)

    geometry = (
        rebuilt[0]
        if isinstance(road.geometry, LineString)
        else MultiLineString(rebuilt)
    )
    return SemanticRoad(
        road_id=road.road_id,
        geometry=geometry,
        layer=road.layer,
        bridge=road.bridge,
        tunnel=road.tunnel,
    )


def _parts(geometry: BaseGeometry) -> tuple[LineString, ...]:
    if isinstance(geometry, LineString):
        return (geometry,)
    if isinstance(geometry, MultiLineString):
        return tuple(geometry.geoms)
    raise EndpointRoadSnappingError("road geometry must be LineString or MultiLineString")


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
