from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import GeometryCollection, LineString, MultiLineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import split, unary_union
from shapely.strtree import STRtree

from core.urban_generator.blocks.parcel import ParcelFrontageSegment, PlanningParcel
from core.urban_generator.blocks.zone_association import (
    BlockZoneAssociationResult,
    BlockZoneAssociationStatus,
    ZoneAssociatedBlock,
)
from core.urban_generator.domain.crs import WorkingCRS, require_working_crs
from core.urban_generator.roads.road_graph import RoadGraph, RoadGraphEdge
from core.urban_generator.zoning.config import ZoneClass

DEFAULT_MAX_SUBDIVISION_BLOCKS = 250_000
DEFAULT_MAX_SUBDIVISION_ROAD_EDGES = 1_000_000
DEFAULT_MAX_SUBDIVISION_ROAD_CANDIDATES = 20_000
DEFAULT_MAX_PARCELS_PER_BLOCK = 64
DEFAULT_MAX_SUBDIVISION_PARCELS = 1_000_000
DEFAULT_MIN_FRONTAGE_LINEARITY_RATIO = 0.98

_AREA_REL_TOLERANCE = 1e-9
_AREA_ABS_TOLERANCE_M2 = 1e-6
_LENGTH_ABS_TOLERANCE_M = 1e-6


class ParcelSubdivisionError(ValueError):
    """Raised when simplified parcel subdivision violates its bounded contract."""


class ParcelSubdivisionSkipReason(StrEnum):
    """Explicit reason why a T07 block did not produce T09 planning parcels."""

    UNASSOCIATED_ZONE = "UNASSOCIATED_ZONE"
    NON_RESIDENTIAL = "NON_RESIDENTIAL"
    HOLED_BLOCK = "HOLED_BLOCK"
    BLOCK_TOO_SMALL = "BLOCK_TOO_SMALL"
    NO_FRONTAGE = "NO_FRONTAGE"
    INSUFFICIENT_FRONTAGE = "INSUFFICIENT_FRONTAGE"
    NONLINEAR_FRONTAGE = "NONLINEAR_FRONTAGE"
    SUBDIVISION_FAILED = "SUBDIVISION_FAILED"
    FRAGMENT_TOO_SMALL = "FRAGMENT_TOO_SMALL"
    FRAGMENT_WITHOUT_FRONTAGE = "FRAGMENT_WITHOUT_FRONTAGE"


@dataclass(frozen=True, slots=True)
class ParcelSubdivisionPolicy:
    """Metric suitability and lot-size policy for conservative residential subdivision."""

    target_frontage_m: float
    minimum_frontage_m: float
    minimum_parcel_area_m2: float
    max_parcels_per_block: int = DEFAULT_MAX_PARCELS_PER_BLOCK
    minimum_frontage_linearity_ratio: float = DEFAULT_MIN_FRONTAGE_LINEARITY_RATIO

    def __post_init__(self) -> None:
        _require_positive_finite("target_frontage_m", self.target_frontage_m)
        _require_positive_finite("minimum_frontage_m", self.minimum_frontage_m)
        _require_positive_finite("minimum_parcel_area_m2", self.minimum_parcel_area_m2)
        _require_positive_int("max_parcels_per_block", self.max_parcels_per_block)
        ratio = _require_finite_number(
            "minimum_frontage_linearity_ratio",
            self.minimum_frontage_linearity_ratio,
        )
        if ratio <= 0.0 or ratio > 1.0:
            raise ParcelSubdivisionError(
                "minimum_frontage_linearity_ratio must be inside (0, 1]"
            )


@dataclass(frozen=True, slots=True)
class ParcelSubdivisionDecision:
    """Auditable per-block T09 result, including explicit skip reasons."""

    block_id: str
    parcel_ids: tuple[str, ...]
    skip_reason: ParcelSubdivisionSkipReason | None
    selected_frontage_road_id: str | None
    selected_frontage_length_m: float

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        _require_sorted_unique_ids("parcel_ids", self.parcel_ids)
        _require_non_negative_finite(
            "selected_frontage_length_m",
            self.selected_frontage_length_m,
        )
        if self.skip_reason is None:
            if not self.parcel_ids:
                raise ParcelSubdivisionError("successful decision requires parcel_ids")
            if self.selected_frontage_road_id is None:
                raise ParcelSubdivisionError(
                    "successful decision requires selected_frontage_road_id"
                )
            _require_id("selected_frontage_road_id", self.selected_frontage_road_id)
            if self.selected_frontage_length_m <= 0.0:
                raise ParcelSubdivisionError(
                    "successful decision requires positive selected frontage"
                )
        else:
            if not isinstance(self.skip_reason, ParcelSubdivisionSkipReason):
                raise ParcelSubdivisionError(
                    "skip_reason must be a ParcelSubdivisionSkipReason"
                )
            if self.parcel_ids:
                raise ParcelSubdivisionError(
                    "skipped decision must not contain parcel_ids"
                )
            if self.selected_frontage_road_id is not None:
                _require_id("selected_frontage_road_id", self.selected_frontage_road_id)


@dataclass(frozen=True, slots=True)
class ParcelSubdivisionDiagnostics:
    """Aggregate T09 outcomes plus bounded spatial-index work."""

    input_block_count: int
    residential_associated_block_count: int
    parceled_block_count: int
    subdivided_block_count: int
    single_parcel_block_count: int
    skipped_block_count: int
    parcel_count: int
    road_edge_count: int
    road_candidate_pair_count: int
    frontage_overlap_pair_count: int
    parceled_area_m2: float

    def __post_init__(self) -> None:
        for name in (
            "input_block_count",
            "residential_associated_block_count",
            "parceled_block_count",
            "subdivided_block_count",
            "single_parcel_block_count",
            "skipped_block_count",
            "parcel_count",
            "road_edge_count",
            "road_candidate_pair_count",
            "frontage_overlap_pair_count",
        ):
            _require_non_negative_int(name, getattr(self, name))
        _require_non_negative_finite("parceled_area_m2", self.parceled_area_m2)
        if self.parceled_block_count + self.skipped_block_count != self.input_block_count:
            raise ParcelSubdivisionError(
                "parceled and skipped block counts must sum to input_block_count"
            )
        if (
            self.subdivided_block_count + self.single_parcel_block_count
            != self.parceled_block_count
        ):
            raise ParcelSubdivisionError(
                "subdivided and single-parcel counts must sum to parceled_block_count"
            )
        if self.residential_associated_block_count > self.input_block_count:
            raise ParcelSubdivisionError(
                "residential_associated_block_count cannot exceed input_block_count"
            )
        if self.frontage_overlap_pair_count > self.road_candidate_pair_count:
            raise ParcelSubdivisionError(
                "frontage overlap pairs cannot exceed road candidate pairs"
            )


@dataclass(frozen=True, slots=True)
class ParcelSubdivisionResult:
    """Deterministic planning parcels plus one decision for every T07 block."""

    working_crs: WorkingCRS
    policy: ParcelSubdivisionPolicy
    parcels: tuple[PlanningParcel, ...]
    decisions: tuple[ParcelSubdivisionDecision, ...]
    diagnostics: ParcelSubdivisionDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.working_crs, WorkingCRS):
            raise ParcelSubdivisionError("working_crs must be a WorkingCRS")
        if not isinstance(self.policy, ParcelSubdivisionPolicy):
            raise ParcelSubdivisionError("policy must be a ParcelSubdivisionPolicy")
        if not isinstance(self.parcels, tuple):
            raise ParcelSubdivisionError("parcels must be an immutable tuple")
        if any(not isinstance(parcel, PlanningParcel) for parcel in self.parcels):
            raise ParcelSubdivisionError("parcels must contain only PlanningParcel values")
        if not isinstance(self.decisions, tuple):
            raise ParcelSubdivisionError("decisions must be an immutable tuple")
        if any(
            not isinstance(decision, ParcelSubdivisionDecision)
            for decision in self.decisions
        ):
            raise ParcelSubdivisionError(
                "decisions must contain only ParcelSubdivisionDecision values"
            )
        if not isinstance(self.diagnostics, ParcelSubdivisionDiagnostics):
            raise ParcelSubdivisionError(
                "diagnostics must be a ParcelSubdivisionDiagnostics"
            )
        if self.diagnostics.parcel_count != len(self.parcels):
            raise ParcelSubdivisionError("diagnostics parcel_count must match parcels")
        if self.diagnostics.input_block_count != len(self.decisions):
            raise ParcelSubdivisionError(
                "diagnostics input_block_count must match decisions"
            )
        parcel_ids = tuple(parcel.parcel_id for parcel in self.parcels)
        _require_sorted_unique_ids("parcel IDs", parcel_ids)
        decision_block_ids = tuple(decision.block_id for decision in self.decisions)
        _require_sorted_unique_ids("decision block IDs", decision_block_ids)


@dataclass(frozen=True, slots=True)
class _FrontagePiece:
    road_id: str
    geometry: LineString

    @property
    def length_m(self) -> float:
        return float(self.geometry.length)

    @property
    def sort_key(self) -> tuple[float, str, str]:
        return (-self.length_m, self.road_id, self.geometry.wkb_hex)


class SimplifiedParcelSubdivider:
    """Create simple frontage lots only for suitable associated residential blocks.

    One STRtree indexes road edges. For each eligible block the longest unique road-backed boundary
    segment selects a frontage axis. Nearly straight frontages can be divided into equal frontage
    intervals; perpendicular cuts then split the complete block. The entire block is skipped when
    the split cannot produce a complete set of positive-area, minimum-area, minimum-frontage lots.
    Partial parcel sets are never returned.
    """

    def __init__(
        self,
        *,
        working_srid: int,
        policy: ParcelSubdivisionPolicy,
        max_blocks: int = DEFAULT_MAX_SUBDIVISION_BLOCKS,
        max_road_edges: int = DEFAULT_MAX_SUBDIVISION_ROAD_EDGES,
        max_road_candidates_per_block: int = DEFAULT_MAX_SUBDIVISION_ROAD_CANDIDATES,
        max_output_parcels: int = DEFAULT_MAX_SUBDIVISION_PARCELS,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        if not isinstance(policy, ParcelSubdivisionPolicy):
            raise ParcelSubdivisionError("policy must be a ParcelSubdivisionPolicy")
        self.policy = policy
        for name, value in (
            ("max_blocks", max_blocks),
            ("max_road_edges", max_road_edges),
            ("max_road_candidates_per_block", max_road_candidates_per_block),
            ("max_output_parcels", max_output_parcels),
        ):
            _require_positive_int(name, value)
        self.max_blocks = max_blocks
        self.max_road_edges = max_road_edges
        self.max_road_candidates_per_block = max_road_candidates_per_block
        self.max_output_parcels = max_output_parcels

    def subdivide(
        self,
        zoned_blocks: BlockZoneAssociationResult,
        *,
        road_graph: RoadGraph,
    ) -> ParcelSubdivisionResult:
        if not isinstance(zoned_blocks, BlockZoneAssociationResult):
            raise ParcelSubdivisionError(
                "zoned_blocks must be a BlockZoneAssociationResult"
            )
        if not isinstance(road_graph, RoadGraph):
            raise ParcelSubdivisionError("road_graph must be a RoadGraph")
        if zoned_blocks.working_crs != self.working_crs:
            raise ParcelSubdivisionError(
                "zoned block working CRS must match subdivider working CRS"
            )
        if road_graph.working_crs != self.working_crs:
            raise ParcelSubdivisionError(
                "road graph working CRS must match subdivider working CRS"
            )
        if len(zoned_blocks.blocks) > self.max_blocks:
            raise ParcelSubdivisionError(
                f"subdivision block limit exceeded: {len(zoned_blocks.blocks)} > "
                f"{self.max_blocks}"
            )
        if len(road_graph.edges) > self.max_road_edges:
            raise ParcelSubdivisionError(
                f"subdivision road edge limit exceeded: {len(road_graph.edges)} > "
                f"{self.max_road_edges}"
            )

        ordered_edges = tuple(sorted(road_graph.edges, key=lambda edge: edge.edge_id))
        if len({edge.edge_id for edge in ordered_edges}) != len(ordered_edges):
            raise ParcelSubdivisionError("road graph edge_id values must be unique")
        edge_geometries = tuple(edge.geometry for edge in ordered_edges)
        tree = STRtree(edge_geometries) if edge_geometries else None

        ordered_blocks = tuple(
            sorted(
                zoned_blocks.blocks,
                key=lambda item: item.cleaned_block.block_id,
            )
        )
        if len({item.cleaned_block.block_id for item in ordered_blocks}) != len(
            ordered_blocks
        ):
            raise ParcelSubdivisionError("zoned block IDs must be unique")

        draft_parcels: list[
            tuple[str, Polygon, tuple[ParcelFrontageSegment, ...], str, ZoneClass]
        ] = []
        draft_decisions: list[
            tuple[str, ParcelSubdivisionSkipReason | None, str | None, float, int]
        ] = []
        road_candidate_pair_count = 0
        frontage_overlap_pair_count = 0
        residential_associated_block_count = 0
        parceled_area_m2 = 0.0

        for item in ordered_blocks:
            block = item.cleaned_block
            association = item.association
            block_id = block.block_id

            if association.status is not BlockZoneAssociationStatus.ASSOCIATED:
                draft_decisions.append(
                    (block_id, ParcelSubdivisionSkipReason.UNASSOCIATED_ZONE, None, 0.0, 0)
                )
                continue
            if association.zone_class is not ZoneClass.RESIDENTIAL:
                draft_decisions.append(
                    (block_id, ParcelSubdivisionSkipReason.NON_RESIDENTIAL, None, 0.0, 0)
                )
                continue
            if association.zone_id is None:
                raise ParcelSubdivisionError(
                    "ASSOCIATED residential block must carry zone_id"
                )
            residential_associated_block_count += 1

            geometry = block.geometry
            if geometry.interiors:
                draft_decisions.append(
                    (block_id, ParcelSubdivisionSkipReason.HOLED_BLOCK, None, 0.0, 0)
                )
                continue
            block_area_m2 = float(geometry.area)
            if block_area_m2 + _AREA_ABS_TOLERANCE_M2 < self.policy.minimum_parcel_area_m2:
                draft_decisions.append(
                    (block_id, ParcelSubdivisionSkipReason.BLOCK_TOO_SMALL, None, 0.0, 0)
                )
                continue

            candidate_edges: tuple[RoadGraphEdge, ...]
            if tree is None:
                candidate_edges = ()
            else:
                indexes = tuple(
                    sorted(int(index) for index in tree.query(geometry.boundary))
                )
                road_candidate_pair_count += len(indexes)
                if len(indexes) > self.max_road_candidates_per_block:
                    raise ParcelSubdivisionError(
                        f"road candidate limit exceeded for block {block_id!r}: "
                        f"{len(indexes)} > {self.max_road_candidates_per_block}"
                    )
                candidate_edges = tuple(ordered_edges[index] for index in indexes)

            frontage_pieces = _unique_frontage_pieces(geometry, candidate_edges)
            frontage_overlap_pair_count += len(frontage_pieces)
            if not frontage_pieces:
                draft_decisions.append(
                    (block_id, ParcelSubdivisionSkipReason.NO_FRONTAGE, None, 0.0, 0)
                )
                continue

            dominant = min(frontage_pieces, key=lambda piece: piece.sort_key)
            if dominant.length_m + _LENGTH_ABS_TOLERANCE_M < self.policy.minimum_frontage_m:
                draft_decisions.append(
                    (
                        block_id,
                        ParcelSubdivisionSkipReason.INSUFFICIENT_FRONTAGE,
                        dominant.road_id,
                        dominant.length_m,
                        0,
                    )
                )
                continue
            if _linearity_ratio(dominant.geometry) < self.policy.minimum_frontage_linearity_ratio:
                draft_decisions.append(
                    (
                        block_id,
                        ParcelSubdivisionSkipReason.NONLINEAR_FRONTAGE,
                        dominant.road_id,
                        dominant.length_m,
                        0,
                    )
                )
                continue

            by_frontage = max(1, int(dominant.length_m // self.policy.target_frontage_m))
            by_frontage_minimum = max(
                1,
                int(dominant.length_m // self.policy.minimum_frontage_m),
            )
            by_area = max(1, int(block_area_m2 // self.policy.minimum_parcel_area_m2))
            parcel_count = min(
                by_frontage,
                by_frontage_minimum,
                by_area,
                self.policy.max_parcels_per_block,
            )

            fragments = _frontage_split_fragments(
                geometry,
                dominant.geometry,
                parcel_count=parcel_count,
            )
            if fragments is None:
                draft_decisions.append(
                    (
                        block_id,
                        ParcelSubdivisionSkipReason.SUBDIVISION_FAILED,
                        dominant.road_id,
                        dominant.length_m,
                        0,
                    )
                )
                continue

            fragment_area_m2 = math.fsum(float(fragment.area) for fragment in fragments)
            _require_area_accounting(block_area_m2, fragment_area_m2)
            if any(
                float(fragment.area) + _AREA_ABS_TOLERANCE_M2
                < self.policy.minimum_parcel_area_m2
                for fragment in fragments
            ):
                draft_decisions.append(
                    (
                        block_id,
                        ParcelSubdivisionSkipReason.FRAGMENT_TOO_SMALL,
                        dominant.road_id,
                        dominant.length_m,
                        0,
                    )
                )
                continue

            block_parcels: list[
                tuple[str, Polygon, tuple[ParcelFrontageSegment, ...], str, ZoneClass]
            ] = []
            fragment_failure: ParcelSubdivisionSkipReason | None = None
            for fragment_index, fragment in enumerate(fragments):
                frontages = _parcel_frontages(fragment, candidate_edges)
                unique_frontage_length_m = _unique_frontage_length(frontages)
                if unique_frontage_length_m + _LENGTH_ABS_TOLERANCE_M < self.policy.minimum_frontage_m:
                    fragment_failure = ParcelSubdivisionSkipReason.FRAGMENT_WITHOUT_FRONTAGE
                    break
                block_parcels.append(
                    (
                        f"{block_id}:{fragment_index:04d}",
                        fragment,
                        frontages,
                        association.zone_id,
                        ZoneClass.RESIDENTIAL,
                    )
                )

            if fragment_failure is not None:
                draft_decisions.append(
                    (
                        block_id,
                        fragment_failure,
                        dominant.road_id,
                        dominant.length_m,
                        0,
                    )
                )
                continue

            if len(draft_parcels) + len(block_parcels) > self.max_output_parcels:
                raise ParcelSubdivisionError(
                    "subdivision output parcel limit exceeded: "
                    f"{len(draft_parcels) + len(block_parcels)} > {self.max_output_parcels}"
                )
            draft_parcels.extend(block_parcels)
            parceled_area_m2 += block_area_m2
            draft_decisions.append(
                (
                    block_id,
                    None,
                    dominant.road_id,
                    dominant.length_m,
                    len(block_parcels),
                )
            )

        ordered_drafts = tuple(
            sorted(draft_parcels, key=lambda draft: (draft[0], draft[1].wkb_hex))
        )
        parcel_id_by_draft_id = {
            draft_id: f"parcel:{index:08d}"
            for index, (draft_id, _geometry, _frontages, _zone_id, _zone_class) in enumerate(
                ordered_drafts
            )
        }
        parcels = tuple(
            PlanningParcel(
                parcel_id=parcel_id_by_draft_id[draft_id],
                block_id=draft_id.rsplit(":", 1)[0],
                working_srid=self.working_crs.srid,
                geometry=geometry,
                buildable_envelope=geometry,
                frontages=frontages,
                zone_id=zone_id,
                zone_class=zone_class,
            )
            for draft_id, geometry, frontages, zone_id, zone_class in ordered_drafts
        )

        ids_by_block: dict[str, list[str]] = {}
        for draft_id, _geometry, _frontages, _zone_id, _zone_class in ordered_drafts:
            block_id = draft_id.rsplit(":", 1)[0]
            ids_by_block.setdefault(block_id, []).append(parcel_id_by_draft_id[draft_id])

        decisions = tuple(
            ParcelSubdivisionDecision(
                block_id=block_id,
                parcel_ids=tuple(ids_by_block.get(block_id, ())),
                skip_reason=skip_reason,
                selected_frontage_road_id=road_id,
                selected_frontage_length_m=frontage_length_m,
            )
            for block_id, skip_reason, road_id, frontage_length_m, _parcel_count in sorted(
                draft_decisions,
                key=lambda draft: draft[0],
            )
        )
        parceled_block_count = sum(decision.skip_reason is None for decision in decisions)
        subdivided_block_count = sum(len(decision.parcel_ids) > 1 for decision in decisions)
        single_parcel_block_count = sum(
            len(decision.parcel_ids) == 1 for decision in decisions
        )
        diagnostics = ParcelSubdivisionDiagnostics(
            input_block_count=len(ordered_blocks),
            residential_associated_block_count=residential_associated_block_count,
            parceled_block_count=parceled_block_count,
            subdivided_block_count=subdivided_block_count,
            single_parcel_block_count=single_parcel_block_count,
            skipped_block_count=len(ordered_blocks) - parceled_block_count,
            parcel_count=len(parcels),
            road_edge_count=len(ordered_edges),
            road_candidate_pair_count=road_candidate_pair_count,
            frontage_overlap_pair_count=frontage_overlap_pair_count,
            parceled_area_m2=parceled_area_m2,
        )
        return ParcelSubdivisionResult(
            working_crs=self.working_crs,
            policy=self.policy,
            parcels=parcels,
            decisions=decisions,
            diagnostics=diagnostics,
        )


def _unique_frontage_pieces(
    block: Polygon,
    candidate_edges: tuple[RoadGraphEdge, ...],
) -> tuple[_FrontagePiece, ...]:
    accepted: list[_FrontagePiece] = []
    covered: BaseGeometry | None = None
    for edge in sorted(candidate_edges, key=lambda item: (item.road_id, item.edge_id)):
        overlap = block.boundary.intersection(edge.geometry)
        for line in _linear_parts(overlap):
            unique: BaseGeometry = line if covered is None else line.difference(covered)
            for part in _linear_parts(unique):
                if part.length <= _LENGTH_ABS_TOLERANCE_M:
                    continue
                accepted.append(_FrontagePiece(road_id=edge.road_id, geometry=part))
                covered = part if covered is None else unary_union((covered, part))
    return tuple(sorted(accepted, key=lambda piece: piece.sort_key))


def _parcel_frontages(
    parcel: Polygon,
    candidate_edges: tuple[RoadGraphEdge, ...],
) -> tuple[ParcelFrontageSegment, ...]:
    segments: list[ParcelFrontageSegment] = []
    covered: BaseGeometry | None = None
    for edge in sorted(candidate_edges, key=lambda item: (item.road_id, item.edge_id)):
        overlap = parcel.boundary.intersection(edge.geometry)
        for line in _linear_parts(overlap):
            unique: BaseGeometry = line if covered is None else line.difference(covered)
            for part in _linear_parts(unique):
                if part.length <= _LENGTH_ABS_TOLERANCE_M:
                    continue
                segments.append(ParcelFrontageSegment(road_id=edge.road_id, geometry=part))
                covered = part if covered is None else unary_union((covered, part))
    return tuple(sorted(segments, key=lambda item: item.sort_key))


def _unique_frontage_length(frontages: tuple[ParcelFrontageSegment, ...]) -> float:
    if not frontages:
        return 0.0
    return float(unary_union(tuple(item.geometry for item in frontages)).length)


def _frontage_split_fragments(
    block: Polygon,
    frontage: LineString,
    *,
    parcel_count: int,
) -> tuple[Polygon, ...] | None:
    if parcel_count <= 1:
        return (block,)

    coordinates = tuple(frontage.coords)
    start_x, start_y = coordinates[0]
    end_x, end_y = coordinates[-1]
    dx = end_x - start_x
    dy = end_y - start_y
    chord_length = math.hypot(dx, dy)
    if chord_length <= _LENGTH_ABS_TOLERANCE_M:
        return None
    tangent_x = dx / chord_length
    tangent_y = dy / chord_length
    normal_x = -tangent_y
    normal_y = tangent_x

    min_x, min_y, max_x, max_y = block.bounds
    span = max(math.hypot(max_x - min_x, max_y - min_y) * 4.0, 1.0)
    cutters: list[LineString] = []
    for index in range(1, parcel_count):
        point = frontage.interpolate(frontage.length * index / parcel_count)
        cutters.append(
            LineString(
                [
                    (point.x - normal_x * span, point.y - normal_y * span),
                    (point.x + normal_x * span, point.y + normal_y * span),
                ]
            )
        )

    try:
        pieces = split(block, MultiLineString(cutters))
    except (TypeError, ValueError):
        return None
    polygons = tuple(
        geometry
        for geometry in pieces.geoms
        if isinstance(geometry, Polygon) and geometry.area > _AREA_ABS_TOLERANCE_M2
    )
    if len(polygons) != parcel_count:
        return None
    return tuple(sorted(polygons, key=lambda polygon: (polygon.centroid.x, polygon.centroid.y, polygon.wkb_hex)))


def _linearity_ratio(line: LineString) -> float:
    coordinates = tuple(line.coords)
    start = coordinates[0]
    end = coordinates[-1]
    chord = math.hypot(end[0] - start[0], end[1] - start[1])
    return min(1.0, chord / float(line.length))


def _linear_parts(geometry: BaseGeometry) -> tuple[LineString, ...]:
    if geometry.is_empty:
        return ()
    if isinstance(geometry, LineString):
        return (geometry,)
    if isinstance(geometry, MultiLineString):
        return tuple(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        parts: list[LineString] = []
        for item in geometry.geoms:
            parts.extend(_linear_parts(item))
        return tuple(parts)
    return ()


def _require_area_accounting(expected_m2: float, actual_m2: float) -> None:
    tolerance_m2 = max(
        _AREA_ABS_TOLERANCE_M2,
        expected_m2 * _AREA_REL_TOLERANCE,
    )
    if not math.isclose(expected_m2, actual_m2, rel_tol=0.0, abs_tol=tolerance_m2):
        raise ParcelSubdivisionError(
            "parcel subdivision fragments must conserve complete block area"
        )


def _require_sorted_unique_ids(field_name: str, values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple):
        raise ParcelSubdivisionError(f"{field_name} must be an immutable tuple")
    for value in values:
        _require_id(field_name, value)
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise ParcelSubdivisionError(f"{field_name} must be sorted and unique")


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ParcelSubdivisionError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise ParcelSubdivisionError(f"{field_name} must not contain line breaks")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ParcelSubdivisionError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ParcelSubdivisionError(f"{field_name} must be a non-negative integer")


def _require_finite_number(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParcelSubdivisionError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ParcelSubdivisionError(f"{field_name} must be a finite number")
    return number


def _require_positive_finite(field_name: str, value: float) -> None:
    if _require_finite_number(field_name, value) <= 0.0:
        raise ParcelSubdivisionError(f"{field_name} must be greater than zero")


def _require_non_negative_finite(field_name: str, value: float) -> None:
    if _require_finite_number(field_name, value) < 0.0:
        raise ParcelSubdivisionError(f"{field_name} must be non-negative")
