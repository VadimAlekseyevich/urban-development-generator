from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPoint, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import split
from shapely.strtree import STRtree

from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.domain.network import NetworkPoint

DEFAULT_MAX_ROAD_PARTS = 500_000
DEFAULT_MAX_CANDIDATE_PAIRS = 2_000_000


class SemanticNodingError(ValueError):
    """Raised when road noding inputs violate the bounded metric contract."""


@dataclass(frozen=True, slots=True)
class SemanticRoad:
    """Canonical road geometry plus grade-separation semantics used by noding."""

    road_id: str
    geometry: BaseGeometry
    layer: int = 0
    bridge: bool = False
    tunnel: bool = False

    def __post_init__(self) -> None:
        _require_road_id(self.road_id)
        _require_road_geometry(self.geometry)
        if isinstance(self.layer, bool) or not isinstance(self.layer, int):
            raise SemanticNodingError("layer must be an integer")
        if not isinstance(self.bridge, bool):
            raise SemanticNodingError("bridge must be a bool")
        if not isinstance(self.tunnel, bool):
            raise SemanticNodingError("tunnel must be a bool")


@dataclass(frozen=True, slots=True)
class SemanticJunction:
    """One noding point with the canonical roads that participate in it."""

    point: NetworkPoint
    road_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.point, NetworkPoint):
            raise SemanticNodingError("point must be a NetworkPoint")
        if not isinstance(self.road_ids, tuple) or not self.road_ids:
            raise SemanticNodingError("road_ids must be a non-empty tuple")
        for road_id in self.road_ids:
            _require_road_id(road_id)
        if tuple(sorted(set(self.road_ids))) != self.road_ids:
            raise SemanticNodingError("road_ids must be sorted and unique")


@dataclass(frozen=True, slots=True)
class NodedRoad:
    """One canonical road split into deterministic directed geometry parts."""

    road_id: str
    parts: tuple[LineString, ...]

    def __post_init__(self) -> None:
        _require_road_id(self.road_id)
        if not isinstance(self.parts, tuple) or not self.parts:
            raise SemanticNodingError("parts must be a non-empty tuple")
        for part in self.parts:
            if not isinstance(part, LineString) or part.is_empty or part.length <= 0.0:
                raise SemanticNodingError("parts must contain non-empty positive-length LineStrings")


@dataclass(frozen=True, slots=True)
class SemanticNodingDiagnostics:
    """Bounded-work diagnostics for one semantic noding pass."""

    road_count: int
    road_part_count: int
    candidate_pair_count: int
    junction_count: int
    suppressed_intersection_count: int
    overlap_pair_count: int
    output_part_count: int


@dataclass(frozen=True, slots=True)
class SemanticNodingResult:
    """Noded roads and junctions ready for the later graph-build work item."""

    roads: tuple[NodedRoad, ...]
    junctions: tuple[SemanticJunction, ...]
    diagnostics: SemanticNodingDiagnostics


@dataclass(frozen=True, slots=True)
class _RoadPart:
    road_index: int
    part_index: int
    geometry: LineString


class SemanticNoder:
    """STRtree-backed road noder respecting grade-separation semantics.

    Candidate line pairs are discovered spatially, never by a full road-by-road matrix.
    Interior crossings form junctions only when ``layer``, ``bridge`` and ``tunnel`` agree.
    Exact shared endpoints remain connected even across a structure/layer transition so a
    bridge or tunnel segment stays connected to its explicit approach geometry.

    The result contains split LineStrings but intentionally does not construct graph nodes or
    edges; that remains the responsibility of S06-T05.
    """

    def __init__(
        self,
        *,
        working_srid: int,
        max_road_parts: int = DEFAULT_MAX_ROAD_PARTS,
        max_candidate_pairs: int = DEFAULT_MAX_CANDIDATE_PAIRS,
    ) -> None:
        require_working_crs(working_srid)
        _require_positive_int("max_road_parts", max_road_parts)
        _require_positive_int("max_candidate_pairs", max_candidate_pairs)
        self.working_srid = working_srid
        self.max_road_parts = max_road_parts
        self.max_candidate_pairs = max_candidate_pairs

    def node(self, roads: tuple[SemanticRoad, ...]) -> SemanticNodingResult:
        if not isinstance(roads, tuple):
            raise SemanticNodingError("roads must be an immutable tuple")
        seen_ids: set[str] = set()
        parts: list[_RoadPart] = []
        for road_index, road in enumerate(roads):
            if not isinstance(road, SemanticRoad):
                raise SemanticNodingError(f"roads[{road_index}] must be a SemanticRoad")
            if road.road_id in seen_ids:
                raise SemanticNodingError(f"duplicate road_id: {road.road_id!r}")
            seen_ids.add(road.road_id)
            for part_index, line in enumerate(_line_parts(road.geometry)):
                parts.append(
                    _RoadPart(
                        road_index=road_index,
                        part_index=part_index,
                        geometry=line,
                    )
                )
                if len(parts) > self.max_road_parts:
                    raise SemanticNodingError(
                        f"road part limit exceeded: {len(parts)} > {self.max_road_parts}"
                    )

        if not parts:
            return SemanticNodingResult(
                roads=(),
                junctions=(),
                diagnostics=SemanticNodingDiagnostics(
                    road_count=0,
                    road_part_count=0,
                    candidate_pair_count=0,
                    junction_count=0,
                    suppressed_intersection_count=0,
                    overlap_pair_count=0,
                    output_part_count=0,
                ),
            )

        tree = STRtree([part.geometry for part in parts])
        split_points: dict[int, dict[tuple[float, float], Point]] = {}
        junction_roads: dict[tuple[float, float], set[str]] = {}
        candidate_pair_count = 0
        suppressed_intersection_count = 0
        overlap_pair_count = 0

        for left_index, left_part in enumerate(parts):
            raw_indexes = tree.query(left_part.geometry, predicate="intersects")
            for raw_right_index in raw_indexes:
                right_index = int(raw_right_index)
                if right_index <= left_index:
                    continue
                candidate_pair_count += 1
                if candidate_pair_count > self.max_candidate_pairs:
                    raise SemanticNodingError(
                        "semantic noding candidate-pair limit exceeded: "
                        f"{candidate_pair_count} > {self.max_candidate_pairs}"
                    )

                right_part = parts[right_index]
                intersection = left_part.geometry.intersection(right_part.geometry)
                node_points, has_overlap = _intersection_node_points(intersection)
                if has_overlap:
                    overlap_pair_count += 1
                if not node_points:
                    continue

                left_road = roads[left_part.road_index]
                right_road = roads[right_part.road_index]
                for point in node_points:
                    left_endpoint = _is_endpoint(left_part.geometry, point)
                    right_endpoint = _is_endpoint(right_part.geometry, point)
                    if not (
                        (left_endpoint and right_endpoint)
                        or _same_grade(left_road, right_road)
                    ):
                        suppressed_intersection_count += 1
                        continue

                    key = (float(point.x), float(point.y))
                    participating = junction_roads.setdefault(key, set())
                    participating.add(left_road.road_id)
                    participating.add(right_road.road_id)
                    if not left_endpoint:
                        split_points.setdefault(left_index, {})[key] = point
                    if not right_endpoint:
                        split_points.setdefault(right_index, {})[key] = point

        output_by_road: list[list[LineString]] = [[] for _ in roads]
        for part_index, part in enumerate(parts):
            points_by_key = split_points.get(part_index, {})
            output_by_road[part.road_index].extend(
                _split_line_ordered(part.geometry, tuple(points_by_key.values()))
            )

        noded_roads = tuple(
            NodedRoad(road_id=road.road_id, parts=tuple(output_by_road[index]))
            for index, road in enumerate(roads)
        )
        junctions = tuple(
            SemanticJunction(
                point=NetworkPoint(x_m=x_m, y_m=y_m),
                road_ids=tuple(sorted(road_ids)),
            )
            for (x_m, y_m), road_ids in sorted(junction_roads.items())
        )
        output_part_count = sum(len(road.parts) for road in noded_roads)
        return SemanticNodingResult(
            roads=noded_roads,
            junctions=junctions,
            diagnostics=SemanticNodingDiagnostics(
                road_count=len(roads),
                road_part_count=len(parts),
                candidate_pair_count=candidate_pair_count,
                junction_count=len(junctions),
                suppressed_intersection_count=suppressed_intersection_count,
                overlap_pair_count=overlap_pair_count,
                output_part_count=output_part_count,
            ),
        )


def _line_parts(geometry: BaseGeometry) -> tuple[LineString, ...]:
    if isinstance(geometry, LineString):
        return (geometry,)
    if isinstance(geometry, MultiLineString):
        return tuple(geometry.geoms)
    raise SemanticNodingError("road geometry must be LineString or MultiLineString")


def _same_grade(left: SemanticRoad, right: SemanticRoad) -> bool:
    return (
        left.layer == right.layer
        and left.bridge == right.bridge
        and left.tunnel == right.tunnel
    )


def _is_endpoint(line: LineString, point: Point) -> bool:
    coordinates = line.coords
    return _same_xy(coordinates[0], point) or _same_xy(coordinates[-1], point)


def _same_xy(coordinate: tuple[float, ...], point: Point) -> bool:
    return float(coordinate[0]) == float(point.x) and float(coordinate[1]) == float(point.y)


def _intersection_node_points(geometry: BaseGeometry) -> tuple[tuple[Point, ...], bool]:
    points: dict[tuple[float, float], Point] = {}
    has_overlap = False

    def collect(value: BaseGeometry) -> None:
        nonlocal has_overlap
        if value.is_empty:
            return
        if isinstance(value, Point):
            points[(float(value.x), float(value.y))] = value
            return
        if isinstance(value, MultiPoint):
            for point in value.geoms:
                collect(point)
            return
        if isinstance(value, LineString):
            has_overlap = True
            for boundary_point in value.boundary.geoms:
                collect(boundary_point)
            return
        if isinstance(value, MultiLineString):
            has_overlap = True
            for line in value.geoms:
                collect(line)
            return
        if isinstance(value, GeometryCollection):
            for item in value.geoms:
                collect(item)

    collect(geometry)
    return tuple(points[key] for key in sorted(points)), has_overlap


def _split_line_ordered(line: LineString, points: tuple[Point, ...]) -> tuple[LineString, ...]:
    if not points:
        return (line,)

    pieces = split(line, MultiPoint(points))
    ordered: list[tuple[float, LineString]] = []
    for geometry in pieces.geoms:
        if not isinstance(geometry, LineString) or geometry.is_empty or geometry.length <= 0.0:
            continue
        start = Point(geometry.coords[0])
        end = Point(geometry.coords[-1])
        start_position = float(line.project(start))
        end_position = float(line.project(end))
        if end_position < start_position:
            geometry = LineString(tuple(reversed(geometry.coords)))
            start_position, end_position = end_position, start_position
        ordered.append((start_position, geometry))

    ordered.sort(key=lambda item: item[0])
    return tuple(geometry for _, geometry in ordered)


def _require_road_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SemanticNodingError("road_id must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise SemanticNodingError("road_id must not contain line breaks")


def _require_road_geometry(geometry: BaseGeometry) -> None:
    if not isinstance(geometry, BaseGeometry):
        raise SemanticNodingError("geometry must be a Shapely geometry")
    if not isinstance(geometry, (LineString, MultiLineString)):
        raise SemanticNodingError("road geometry must be LineString or MultiLineString")
    if geometry.is_empty:
        raise SemanticNodingError("road geometry must not be empty")
    if not geometry.is_valid:
        raise SemanticNodingError("road geometry must be valid")
    if geometry.has_z:
        raise SemanticNodingError("road geometry must be 2D in the working CRS")
    for line in _line_parts(geometry):
        if line.length <= 0.0:
            raise SemanticNodingError("road geometry parts must have positive length")
        for coordinate in line.coords:
            if len(coordinate) < 2 or not all(isfinite(float(value)) for value in coordinate[:2]):
                raise SemanticNodingError("road geometry coordinates must be finite")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SemanticNodingError(f"{field_name} must be a positive integer")
