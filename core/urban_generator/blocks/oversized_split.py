from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from shapely import normalize
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import split
from shapely.strtree import STRtree

from core.urban_generator.blocks.frontage import (
    BlockFrontageValidationResult,
    FrontageValidatedBlock,
)
from core.urban_generator.domain.crs import WorkingCRS, require_working_crs
from core.urban_generator.roads.road_graph import RoadGraph, RoadGraphEdge

DEFAULT_MAX_SPLIT_BLOCKS = 500_000
DEFAULT_MAX_SPLIT_ROAD_EDGES = 1_000_000
DEFAULT_MAX_SPLIT_ROAD_CANDIDATES = 20_000
DEFAULT_MAX_SPLIT_OPERATIONS = 500_000
DEFAULT_MAX_SPLIT_OUTPUT_BLOCKS = 1_000_000
DEFAULT_MAX_SPLIT_DEPTH = 12

_AREA_REL_TOLERANCE = 1e-9
_AREA_ABS_TOLERANCE_M2 = 1e-6
_DIRECTION_TOLERANCE = 1e-12


class OversizedBlockSplitError(ValueError):
    """Raised when oversized block splitting violates its bounded metric-CRS contract."""


@dataclass(frozen=True, slots=True)
class OversizedBlockSplitPolicy:
    """Explicit size threshold for deciding whether a block needs subdivision."""

    max_area_m2: float

    def __post_init__(self) -> None:
        _require_positive_finite("max_area_m2", self.max_area_m2)


class BlockSplitStrategy(StrEnum):
    """Geometry axis used for one successful oversized-block split."""

    ROAD_FRONTAGE = "ROAD_FRONTAGE"
    PRINCIPAL_AXIS = "PRINCIPAL_AXIS"


@dataclass(frozen=True, slots=True)
class SplitBlockCandidate:
    """One post-T05 block geometry with deterministic split provenance.

    Metrics/frontage facts from T03/T04 are intentionally not copied because a split changes
    geometry and would make those measurements stale. ``input_block_id`` points back to the T04
    block while ``source_block_id``/``source_fragment_index`` preserve the earlier T02 lineage.
    """

    block_id: str
    input_block_id: str
    source_block_id: str
    source_fragment_index: int
    split_path: tuple[int, ...]
    geometry: Polygon

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        _require_id("input_block_id", self.input_block_id)
        _require_id("source_block_id", self.source_block_id)
        _require_non_negative_int("source_fragment_index", self.source_fragment_index)
        if not isinstance(self.split_path, tuple):
            raise OversizedBlockSplitError("split_path must be an immutable tuple")
        for index in self.split_path:
            _require_non_negative_int("split_path item", index)
        _require_polygon("geometry", self.geometry)

    @property
    def split_depth(self) -> int:
        return len(self.split_path)


@dataclass(frozen=True, slots=True)
class OversizedBlockSplitDiagnostics:
    """Aggregate split outcomes and bounded-work counters."""

    input_block_count: int
    oversized_input_block_count: int
    output_block_count: int
    split_attempt_count: int
    successful_split_count: int
    road_informed_split_count: int
    principal_axis_split_count: int
    unsplittable_fragment_count: int
    road_candidate_pair_count: int
    max_observed_split_depth: int

    def __post_init__(self) -> None:
        for field_name in (
            "input_block_count",
            "oversized_input_block_count",
            "output_block_count",
            "split_attempt_count",
            "successful_split_count",
            "road_informed_split_count",
            "principal_axis_split_count",
            "unsplittable_fragment_count",
            "road_candidate_pair_count",
            "max_observed_split_depth",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if self.oversized_input_block_count > self.input_block_count:
            raise OversizedBlockSplitError(
                "oversized_input_block_count cannot exceed input_block_count"
            )
        if self.successful_split_count > self.split_attempt_count:
            raise OversizedBlockSplitError(
                "successful_split_count cannot exceed split_attempt_count"
            )
        if (
            self.road_informed_split_count + self.principal_axis_split_count
            != self.successful_split_count
        ):
            raise OversizedBlockSplitError(
                "successful split strategies must sum to successful_split_count"
            )


@dataclass(frozen=True, slots=True)
class OversizedBlockSplitResult:
    """Deterministic post-T05 block candidates for later sliver cleanup."""

    working_crs: WorkingCRS
    policy: OversizedBlockSplitPolicy
    blocks: tuple[SplitBlockCandidate, ...]
    diagnostics: OversizedBlockSplitDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.working_crs, WorkingCRS):
            raise OversizedBlockSplitError("working_crs must be a WorkingCRS")
        if not isinstance(self.policy, OversizedBlockSplitPolicy):
            raise OversizedBlockSplitError("policy must be an OversizedBlockSplitPolicy")
        if not isinstance(self.blocks, tuple):
            raise OversizedBlockSplitError("blocks must be an immutable tuple")
        if any(not isinstance(block, SplitBlockCandidate) for block in self.blocks):
            raise OversizedBlockSplitError(
                "blocks must contain only SplitBlockCandidate values"
            )
        if not isinstance(self.diagnostics, OversizedBlockSplitDiagnostics):
            raise OversizedBlockSplitError(
                "diagnostics must be an OversizedBlockSplitDiagnostics"
            )
        if self.diagnostics.output_block_count != len(self.blocks):
            raise OversizedBlockSplitError(
                "diagnostics output_block_count must match result blocks"
            )


@dataclass(frozen=True, slots=True)
class _PendingFragment:
    input: FrontageValidatedBlock
    geometry: Polygon
    split_path: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _SplitDraft:
    input: FrontageValidatedBlock
    geometry: Polygon
    split_path: tuple[int, ...]


class OversizedBlockSplitter:
    """Recursively split oversized blocks with road-informed/principal-axis cuts.

    A block is oversized only when its area exceeds ``policy.max_area_m2``. If the current
    fragment has positive-length road frontage, the longest frontage edge supplies the reference
    direction and the cut is perpendicular to it. Otherwise the long axis of the minimum rotated
    rectangle supplies the reference direction. Splits pass through an interior reference point.

    The algorithm preserves every positive-area polygon returned by a successful split. It does
    not merge/drop slivers or enforce a minimum output area; those policies belong to S07-T06.
    Work is bounded by input/output limits, per-fragment road candidates, split depth and total
    split attempts.
    """

    def __init__(
        self,
        *,
        working_srid: int,
        policy: OversizedBlockSplitPolicy,
        max_blocks: int = DEFAULT_MAX_SPLIT_BLOCKS,
        max_road_edges: int = DEFAULT_MAX_SPLIT_ROAD_EDGES,
        max_road_candidates_per_fragment: int = DEFAULT_MAX_SPLIT_ROAD_CANDIDATES,
        max_split_operations: int = DEFAULT_MAX_SPLIT_OPERATIONS,
        max_output_blocks: int = DEFAULT_MAX_SPLIT_OUTPUT_BLOCKS,
        max_split_depth: int = DEFAULT_MAX_SPLIT_DEPTH,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        if not isinstance(policy, OversizedBlockSplitPolicy):
            raise OversizedBlockSplitError("policy must be an OversizedBlockSplitPolicy")
        self.policy = policy
        for field_name, value in (
            ("max_blocks", max_blocks),
            ("max_road_edges", max_road_edges),
            ("max_road_candidates_per_fragment", max_road_candidates_per_fragment),
            ("max_split_operations", max_split_operations),
            ("max_output_blocks", max_output_blocks),
            ("max_split_depth", max_split_depth),
        ):
            _require_positive_int(field_name, value)
        self.max_blocks = max_blocks
        self.max_road_edges = max_road_edges
        self.max_road_candidates_per_fragment = max_road_candidates_per_fragment
        self.max_split_operations = max_split_operations
        self.max_output_blocks = max_output_blocks
        self.max_split_depth = max_split_depth

    def split(
        self,
        frontage: BlockFrontageValidationResult,
        *,
        road_graph: RoadGraph,
    ) -> OversizedBlockSplitResult:
        if not isinstance(frontage, BlockFrontageValidationResult):
            raise OversizedBlockSplitError(
                "frontage must be a BlockFrontageValidationResult"
            )
        if frontage.working_crs != self.working_crs:
            raise OversizedBlockSplitError(
                "frontage working CRS must match splitter working CRS"
            )
        if not isinstance(road_graph, RoadGraph):
            raise OversizedBlockSplitError("road_graph must be a RoadGraph")
        if road_graph.working_crs != self.working_crs:
            raise OversizedBlockSplitError(
                "road graph working CRS must match splitter working CRS"
            )
        if len(frontage.blocks) > self.max_blocks:
            raise OversizedBlockSplitError(
                f"split input block limit exceeded: {len(frontage.blocks)} > {self.max_blocks}"
            )
        if len(road_graph.edges) > self.max_road_edges:
            raise OversizedBlockSplitError(
                "split road edge limit exceeded: "
                f"{len(road_graph.edges)} > {self.max_road_edges}"
            )

        ordered_inputs = tuple(
            sorted(frontage.blocks, key=lambda item: item.measured_block.block.block_id)
        )
        _require_unique_input_ids(ordered_inputs)
        ordered_edges = tuple(sorted(road_graph.edges, key=lambda edge: edge.edge_id))
        _require_unique_edge_ids(ordered_edges)
        road_geometries = tuple(edge.geometry for edge in ordered_edges)
        road_tree = STRtree(road_geometries) if road_geometries else None

        drafts: list[_SplitDraft] = []
        split_attempt_count = 0
        successful_split_count = 0
        road_informed_split_count = 0
        principal_axis_split_count = 0
        unsplittable_fragment_count = 0
        road_candidate_pair_count = 0
        max_observed_split_depth = 0
        oversized_input_block_count = 0

        for input_block in ordered_inputs:
            root_geometry = input_block.measured_block.block.geometry
            if float(root_geometry.area) > self.policy.max_area_m2:
                oversized_input_block_count += 1
            pending = [_PendingFragment(input=input_block, geometry=root_geometry, split_path=())]

            while pending:
                current = pending.pop()
                max_observed_split_depth = max(
                    max_observed_split_depth,
                    len(current.split_path),
                )
                if float(current.geometry.area) <= self.policy.max_area_m2:
                    drafts.append(
                        _SplitDraft(
                            input=current.input,
                            geometry=current.geometry,
                            split_path=current.split_path,
                        )
                    )
                    _require_output_bound(len(drafts), self.max_output_blocks)
                    continue

                if len(current.split_path) >= self.max_split_depth:
                    unsplittable_fragment_count += 1
                    drafts.append(
                        _SplitDraft(
                            input=current.input,
                            geometry=current.geometry,
                            split_path=current.split_path,
                        )
                    )
                    _require_output_bound(len(drafts), self.max_output_blocks)
                    continue

                split_attempt_count += 1
                if split_attempt_count > self.max_split_operations:
                    raise OversizedBlockSplitError(
                        "split operation limit exceeded: "
                        f"{split_attempt_count} > {self.max_split_operations}"
                    )

                direction, strategy, candidate_count = self._reference_direction(
                    current.geometry,
                    road_tree=road_tree,
                    road_edges=ordered_edges,
                    road_geometries=road_geometries,
                )
                road_candidate_pair_count += candidate_count
                children = _split_polygon(current.geometry, direction)
                if len(children) < 2:
                    unsplittable_fragment_count += 1
                    drafts.append(
                        _SplitDraft(
                            input=current.input,
                            geometry=current.geometry,
                            split_path=current.split_path,
                        )
                    )
                    _require_output_bound(len(drafts), self.max_output_blocks)
                    continue

                successful_split_count += 1
                if strategy is BlockSplitStrategy.ROAD_FRONTAGE:
                    road_informed_split_count += 1
                else:
                    principal_axis_split_count += 1

                for child_index in reversed(range(len(children))):
                    pending.append(
                        _PendingFragment(
                            input=current.input,
                            geometry=children[child_index],
                            split_path=(*current.split_path, child_index),
                        )
                    )

                projected_output = len(drafts) + len(pending)
                _require_output_bound(projected_output, self.max_output_blocks)

        drafts.sort(
            key=lambda item: (
                item.input.measured_block.block.block_id,
                item.split_path,
                item.geometry.wkb_hex,
            )
        )
        blocks = tuple(
            SplitBlockCandidate(
                block_id=f"block:{index:08d}",
                input_block_id=draft.input.measured_block.block.block_id,
                source_block_id=draft.input.measured_block.block.source_block_id,
                source_fragment_index=draft.input.measured_block.block.source_fragment_index,
                split_path=draft.split_path,
                geometry=draft.geometry,
            )
            for index, draft in enumerate(drafts)
        )
        diagnostics = OversizedBlockSplitDiagnostics(
            input_block_count=len(ordered_inputs),
            oversized_input_block_count=oversized_input_block_count,
            output_block_count=len(blocks),
            split_attempt_count=split_attempt_count,
            successful_split_count=successful_split_count,
            road_informed_split_count=road_informed_split_count,
            principal_axis_split_count=principal_axis_split_count,
            unsplittable_fragment_count=unsplittable_fragment_count,
            road_candidate_pair_count=road_candidate_pair_count,
            max_observed_split_depth=max_observed_split_depth,
        )
        return OversizedBlockSplitResult(
            working_crs=self.working_crs,
            policy=self.policy,
            blocks=blocks,
            diagnostics=diagnostics,
        )

    def _reference_direction(
        self,
        geometry: Polygon,
        *,
        road_tree: STRtree | None,
        road_edges: tuple[RoadGraphEdge, ...],
        road_geometries: tuple[LineString, ...],
    ) -> tuple[tuple[float, float], BlockSplitStrategy, int]:
        if road_tree is not None:
            boundary = geometry.boundary
            candidate_indexes = tuple(
                sorted(int(index) for index in road_tree.query(boundary))
            )
            if len(candidate_indexes) > self.max_road_candidates_per_fragment:
                raise OversizedBlockSplitError(
                    "split road candidate limit exceeded: "
                    f"{len(candidate_indexes)} > {self.max_road_candidates_per_fragment}"
                )
            frontage_candidates: list[tuple[float, str, int]] = []
            for index in candidate_indexes:
                overlap_length = float(boundary.intersection(road_geometries[index]).length)
                if not math.isfinite(overlap_length) or overlap_length < 0.0:
                    raise OversizedBlockSplitError(
                        "road frontage overlap length must be finite and non-negative"
                    )
                if overlap_length > 0.0:
                    frontage_candidates.append(
                        (-overlap_length, road_edges[index].edge_id, index)
                    )
            if frontage_candidates:
                _negative_length, _edge_id, edge_index = min(frontage_candidates)
                return (
                    _line_direction(road_geometries[edge_index]),
                    BlockSplitStrategy.ROAD_FRONTAGE,
                    len(candidate_indexes),
                )
            candidate_count = len(candidate_indexes)
        else:
            candidate_count = 0

        return (
            _principal_axis_direction(geometry),
            BlockSplitStrategy.PRINCIPAL_AXIS,
            candidate_count,
        )


def _split_polygon(
    geometry: Polygon,
    reference_direction: tuple[float, float],
) -> tuple[Polygon, ...]:
    direction_x, direction_y = _canonical_direction(*reference_direction)
    cut_x, cut_y = -direction_y, direction_x
    center = geometry.centroid
    if not geometry.covers(center):
        center = geometry.representative_point()

    min_x, min_y, max_x, max_y = geometry.bounds
    diagonal = math.hypot(max_x - min_x, max_y - min_y)
    _require_positive_finite("block diagonal", diagonal)
    half_length = max(1.0, diagonal * 2.0)
    cut_line = LineString(
        (
            (center.x - cut_x * half_length, center.y - cut_y * half_length),
            (center.x + cut_x * half_length, center.y + cut_y * half_length),
        )
    )
    raw = split(geometry, cut_line)
    children = tuple(
        _normalize_polygon(part)
        for part in raw.geoms
        if isinstance(part, Polygon) and not part.is_empty and float(part.area) > 0.0
    )
    if len(children) < 2:
        return ()
    children = tuple(sorted(children, key=lambda item: item.wkb_hex))
    child_area = math.fsum(float(child.area) for child in children)
    if not math.isclose(
        child_area,
        float(geometry.area),
        rel_tol=_AREA_REL_TOLERANCE,
        abs_tol=_AREA_ABS_TOLERANCE_M2,
    ):
        raise OversizedBlockSplitError(
            "split children must conserve source polygon area within tolerance"
        )
    if any(float(child.area) >= float(geometry.area) for child in children):
        return ()
    return children


def _principal_axis_direction(geometry: Polygon) -> tuple[float, float]:
    rectangle = geometry.minimum_rotated_rectangle
    if not isinstance(rectangle, Polygon) or rectangle.is_empty:
        raise OversizedBlockSplitError(
            "minimum rotated rectangle must be a non-empty Polygon"
        )
    coordinates = tuple(rectangle.exterior.coords)
    if len(coordinates) != 5:
        raise OversizedBlockSplitError("minimum rotated rectangle must have four sides")

    directions: list[tuple[float, tuple[float, float]]] = []
    for left, right in zip(coordinates[:-1], coordinates[1:], strict=True):
        dx = float(right[0]) - float(left[0])
        dy = float(right[1]) - float(left[1])
        length = math.hypot(dx, dy)
        _require_positive_finite("minimum rotated rectangle side", length)
        directions.append((length, _canonical_direction(dx, dy)))
    maximum = max(length for length, _direction in directions)
    longest = tuple(
        direction
        for length, direction in directions
        if math.isclose(length, maximum, rel_tol=1e-12, abs_tol=1e-12)
    )
    return min(longest)


def _line_direction(geometry: LineString) -> tuple[float, float]:
    if not isinstance(geometry, LineString) or geometry.is_empty:
        raise OversizedBlockSplitError("road geometry must be a non-empty LineString")
    coordinates = tuple(geometry.coords)
    if len(coordinates) < 2:
        raise OversizedBlockSplitError("road geometry must have at least two coordinates")
    dx = float(coordinates[-1][0]) - float(coordinates[0][0])
    dy = float(coordinates[-1][1]) - float(coordinates[0][1])
    if math.hypot(dx, dy) <= _DIRECTION_TOLERANCE:
        for left, right in zip(coordinates[:-1], coordinates[1:], strict=True):
            segment_dx = float(right[0]) - float(left[0])
            segment_dy = float(right[1]) - float(left[1])
            if math.hypot(segment_dx, segment_dy) > _DIRECTION_TOLERANCE:
                return _canonical_direction(segment_dx, segment_dy)
        raise OversizedBlockSplitError("road geometry must have a usable direction")
    return _canonical_direction(dx, dy)


def _canonical_direction(dx: float, dy: float) -> tuple[float, float]:
    if not math.isfinite(dx) or not math.isfinite(dy):
        raise OversizedBlockSplitError("direction components must be finite")
    length = math.hypot(dx, dy)
    if length <= _DIRECTION_TOLERANCE:
        raise OversizedBlockSplitError("direction must have positive length")
    unit_x = dx / length
    unit_y = dy / length
    if unit_x < 0.0 or (math.isclose(unit_x, 0.0, abs_tol=_DIRECTION_TOLERANCE) and unit_y < 0.0):
        unit_x = -unit_x
        unit_y = -unit_y
    if math.isclose(unit_x, 0.0, abs_tol=_DIRECTION_TOLERANCE):
        unit_x = 0.0
    if math.isclose(unit_y, 0.0, abs_tol=_DIRECTION_TOLERANCE):
        unit_y = 0.0
    return unit_x, unit_y


def _normalize_polygon(geometry: Polygon) -> Polygon:
    normalized = normalize(geometry)
    if not isinstance(normalized, Polygon):
        raise OversizedBlockSplitError("normalized split child must remain a Polygon")
    _require_polygon("split child", normalized)
    return normalized


def _require_unique_input_ids(blocks: tuple[FrontageValidatedBlock, ...]) -> None:
    seen: set[str] = set()
    for block in blocks:
        block_id = block.measured_block.block.block_id
        if block_id in seen:
            raise OversizedBlockSplitError(f"duplicate input block_id: {block_id!r}")
        seen.add(block_id)


def _require_unique_edge_ids(edges: tuple[RoadGraphEdge, ...]) -> None:
    seen: set[str] = set()
    for edge in edges:
        if edge.edge_id in seen:
            raise OversizedBlockSplitError(f"duplicate road edge_id: {edge.edge_id!r}")
        seen.add(edge.edge_id)


def _require_polygon(field_name: str, geometry: BaseGeometry) -> None:
    if not isinstance(geometry, Polygon):
        raise OversizedBlockSplitError(f"{field_name} must be a Polygon")
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise OversizedBlockSplitError(
            f"{field_name} must be non-empty, valid and 2D"
        )
    _require_positive_finite(f"{field_name} area", float(geometry.area))


def _require_output_bound(count: int, maximum: int) -> None:
    if count > maximum:
        raise OversizedBlockSplitError(
            f"split output limit exceeded: {count} > {maximum}"
        )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise OversizedBlockSplitError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise OversizedBlockSplitError(f"{field_name} must not contain line breaks")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise OversizedBlockSplitError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OversizedBlockSplitError(f"{field_name} must be a non-negative integer")


def _require_positive_finite(field_name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OversizedBlockSplitError(f"{field_name} must be a positive finite number")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise OversizedBlockSplitError(f"{field_name} must be a positive finite number")
