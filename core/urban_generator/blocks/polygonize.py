from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from shapely import make_valid, normalize
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize_full
from shapely.strtree import STRtree

from core.urban_generator.domain.crs import WorkingCRS, require_working_crs
from core.urban_generator.roads.road_graph import RoadGraph

DEFAULT_MAX_POLYGONIZE_EDGES = 500_000
DEFAULT_MAX_CANDIDATE_BLOCKS = 250_000


class BlockPolygonizationError(ValueError):
    """Raised when road linework cannot satisfy the bounded block polygonization contract."""


@dataclass(frozen=True, slots=True)
class CandidateBlock:
    """One raw block candidate produced only from road-network topology.

    S07-T01 intentionally carries no developable clipping, block metrics, frontage/access,
    zone association, sliver cleanup, or persistence. Those concerns belong to later S07 work
    items. ``geometry`` is normalized only to make candidate identity deterministic.
    """

    block_id: str
    geometry: Polygon

    def __post_init__(self) -> None:
        if not isinstance(self.block_id, str) or not self.block_id.strip():
            raise BlockPolygonizationError("block_id must be a non-empty string")
        if not isinstance(self.geometry, Polygon):
            raise BlockPolygonizationError("candidate block geometry must be a Polygon")
        if self.geometry.is_empty or not self.geometry.is_valid:
            raise BlockPolygonizationError("candidate block geometry must be non-empty and valid")
        if self.geometry.has_z:
            raise BlockPolygonizationError("candidate block geometry must be 2D")
        area = float(self.geometry.area)
        if not isfinite(area) or area <= 0.0:
            raise BlockPolygonizationError(
                "candidate block geometry must have positive finite area"
            )


@dataclass(frozen=True, slots=True)
class BlockPolygonizationDiagnostics:
    """Diagnostics emitted by one bounded road polygonization pass."""

    input_edge_count: int
    unique_line_count: int
    duplicate_line_count: int
    candidate_block_count: int
    cut_edge_count: int
    dangle_edge_count: int
    invalid_ring_count: int
    repaired_candidate_count: int = 0
    repaired_part_count: int = 0
    excluded_road_edge_count: int = 0

    @property
    def non_polygonized_line_count(self) -> int:
        return self.cut_edge_count + self.dangle_edge_count + self.invalid_ring_count


@dataclass(frozen=True, slots=True)
class BlockPolygonizationResult:
    """Raw candidate blocks plus topology diagnostics for downstream S07 stages."""

    working_crs: WorkingCRS
    blocks: tuple[CandidateBlock, ...]
    diagnostics: BlockPolygonizationDiagnostics


class RoadNetworkBlockPolygonizer:
    """Polygonize already-cleaned road graph linework without inventing new junctions.

    The input is the backend-independent ``RoadGraph`` produced by S06. The implementation uses
    ``polygonize_full`` directly on graph edges and deliberately does not unary-union or otherwise
    node geometric crossings. This preserves S06 grade-separation semantics: a bridge/tunnel line
    that merely crosses another edge geometrically does not become a block boundary junction.

    Geometry cleanup at this stage is conservative: exact duplicate coordinate chains (including
    reversed chains) are de-duplicated, line directions are canonicalized, and polygon geometries
    are normalized for deterministic identifiers. Sliver/area cleanup is explicitly deferred to
    S07-T06.
    """

    def __init__(
        self,
        *,
        working_srid: int,
        max_edges: int = DEFAULT_MAX_POLYGONIZE_EDGES,
        max_candidate_blocks: int = DEFAULT_MAX_CANDIDATE_BLOCKS,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        _require_positive_int(max_edges, field_name="max_edges")
        _require_positive_int(max_candidate_blocks, field_name="max_candidate_blocks")
        self.max_edges = max_edges
        self.max_candidate_blocks = max_candidate_blocks

    def polygonize(
        self, graph: RoadGraph, *, excluded_road_ids: frozenset[str] = frozenset(),
    ) -> BlockPolygonizationResult:
        if not isinstance(graph, RoadGraph):
            raise BlockPolygonizationError("graph must be a RoadGraph")
        if graph.working_crs != self.working_crs:
            raise BlockPolygonizationError(
                "graph working CRS must match polygonizer working CRS: "
                f"{graph.working_crs.srid} != {self.working_crs.srid}"
            )
        if len(graph.edges) > self.max_edges:
            raise BlockPolygonizationError(
                f"polygonize edge limit exceeded: {len(graph.edges)} > {self.max_edges}"
            )

        if not isinstance(excluded_road_ids, frozenset):
            raise BlockPolygonizationError("excluded road ids must be a frozenset")
        excluded_edge_count = 0
        unique_lines: dict[tuple[tuple[float, float], ...], LineString] = {}
        duplicate_line_count = 0
        for edge in sorted(graph.edges, key=lambda item: item.edge_id):
            if edge.road_id in excluded_road_ids:
                excluded_edge_count += 1
                continue
            key = _canonical_line_key(edge.geometry)
            if key in unique_lines:
                duplicate_line_count += 1
                continue
            unique_lines[key] = LineString(key)

        ordered_lines = tuple(unique_lines[key] for key in sorted(unique_lines))
        polygons, cuts, dangles, invalid_rings = polygonize_full(ordered_lines)

        normalized_polygons: list[Polygon] = []
        repaired_indexes: list[int] = []
        repaired_count = 0
        for geometry in polygons.geoms:
            if not isinstance(geometry, Polygon):
                raise BlockPolygonizationError(
                    "polygonize_full returned a non-Polygon candidate"
                )
            # GEOS can return a disconnected polygon interior when a hole
            # touches its shell at two points. Repair only the surface; never
            # node the routing graph or change source road coordinates.
            repaired = not geometry.is_valid
            parts = _polygon_parts(make_valid(geometry) if repaired else geometry)
            if not parts:
                raise BlockPolygonizationError(
                    "candidate block repair yielded no polygonal area"
                )
            if repaired:
                repaired_count += 1
            for part in parts:
                normalized_geometry = normalize(part)
                if (
                    not isinstance(normalized_geometry, Polygon)
                    or normalized_geometry.is_empty
                    or not normalized_geometry.is_valid
                    or normalized_geometry.has_z
                    or not isfinite(float(normalized_geometry.area))
                    or normalized_geometry.area <= 0.0
                ):
                    raise BlockPolygonizationError("invalid block after surface repair")
                if repaired:
                    repaired_indexes.append(len(normalized_polygons))
                normalized_polygons.append(normalized_geometry)
                if len(normalized_polygons) > self.max_candidate_blocks:
                    raise BlockPolygonizationError(
                        "candidate block limit exceeded: "
                        f"{len(normalized_polygons)} > {self.max_candidate_blocks}"
                    )
        _check_repaired_overlaps(normalized_polygons, repaired_indexes)

        if len(normalized_polygons) > self.max_candidate_blocks:
            raise BlockPolygonizationError(
                "candidate block limit exceeded: "
                f"{len(normalized_polygons)} > {self.max_candidate_blocks}"
            )

        normalized_polygons.sort(key=lambda geometry: geometry.wkb_hex)
        blocks = tuple(
            CandidateBlock(block_id=f"block:{index:08d}", geometry=geometry)
            for index, geometry in enumerate(normalized_polygons)
        )
        diagnostics = BlockPolygonizationDiagnostics(
            input_edge_count=len(graph.edges),
            unique_line_count=len(ordered_lines),
            duplicate_line_count=duplicate_line_count,
            candidate_block_count=len(blocks),
            cut_edge_count=len(cuts.geoms),
            dangle_edge_count=len(dangles.geoms),
            invalid_ring_count=len(invalid_rings.geoms),
            repaired_candidate_count=repaired_count,
            repaired_part_count=len(repaired_indexes),
            excluded_road_edge_count=excluded_edge_count,
        )
        return BlockPolygonizationResult(
            working_crs=self.working_crs,
            blocks=blocks,
            diagnostics=diagnostics,
        )


def _polygon_parts(geometry: BaseGeometry) -> tuple[Polygon, ...]:
    if isinstance(geometry, Polygon):
        return (geometry,) if not geometry.is_empty else ()
    if geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        return tuple(part for child in geometry.geoms for part in _polygon_parts(child))
    return ()


def _check_repaired_overlaps(polygons: list[Polygon], indexes: list[int]) -> None:
    """Refuse ambiguous repairs instead of publishing overlapping blocks."""
    if not indexes:
        return
    tree = STRtree(polygons)
    comparisons = 0
    for index in indexes:
        polygon = polygons[index]
        for other in tree.query(polygon, predicate="intersects"):
            if index == int(other):
                continue
            comparisons += 1
            if comparisons > 1_000_000:
                raise BlockPolygonizationError("block repair overlap check budget exceeded")
            if polygon.intersection(polygons[int(other)]).area > 1e-8:
                raise BlockPolygonizationError(
                    "candidate block repair overlaps another face; road topology is ambiguous"
                )


def _canonical_line_key(geometry: LineString) -> tuple[tuple[float, float], ...]:
    if not isinstance(geometry, LineString):
        raise BlockPolygonizationError("road graph edges must contain LineString geometry")
    if geometry.is_empty or not geometry.is_valid or geometry.has_z or geometry.length <= 0.0:
        raise BlockPolygonizationError(
            "road graph edge geometry must be valid positive-length 2D"
        )

    coordinates = tuple(
        (float(coordinate[0]), float(coordinate[1]))
        for coordinate in geometry.coords
    )
    if len(coordinates) < 2:
        raise BlockPolygonizationError("road graph edge must contain at least two coordinates")
    if any(not all(isfinite(value) for value in coordinate) for coordinate in coordinates):
        raise BlockPolygonizationError("road graph edge coordinates must be finite")

    reversed_coordinates = tuple(reversed(coordinates))
    return min(coordinates, reversed_coordinates)


def _require_positive_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BlockPolygonizationError(f"{field_name} must be a positive integer")
