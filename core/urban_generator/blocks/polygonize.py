from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from shapely import normalize
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize_full

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

    def polygonize(self, graph: RoadGraph) -> BlockPolygonizationResult:
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

        unique_lines: dict[tuple[tuple[float, float], ...], LineString] = {}
        duplicate_line_count = 0
        for edge in sorted(graph.edges, key=lambda item: item.edge_id):
            key = _canonical_line_key(edge.geometry)
            if key in unique_lines:
                duplicate_line_count += 1
                continue
            unique_lines[key] = LineString(key)

        ordered_lines = tuple(unique_lines[key] for key in sorted(unique_lines))
        polygons, cuts, dangles, invalid_rings = polygonize_full(ordered_lines)

        normalized_polygons: list[Polygon] = []
        for geometry in polygons.geoms:
            if not isinstance(geometry, Polygon):
                raise BlockPolygonizationError(
                    "polygonize_full returned a non-Polygon candidate"
                )
            normalized_geometry = normalize(geometry)
            if not isinstance(normalized_geometry, Polygon):
                raise BlockPolygonizationError(
                    "normalized candidate block must remain a Polygon"
                )
            if (
                normalized_geometry.is_empty
                or not normalized_geometry.is_valid
                or normalized_geometry.has_z
                or not isfinite(float(normalized_geometry.area))
                or normalized_geometry.area <= 0.0
            ):
                raise BlockPolygonizationError(
                    "polygonize_full returned an invalid candidate block"
                )
            normalized_polygons.append(normalized_geometry)

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
        )
        return BlockPolygonizationResult(
            working_crs=self.working_crs,
            blocks=blocks,
            diagnostics=diagnostics,
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
