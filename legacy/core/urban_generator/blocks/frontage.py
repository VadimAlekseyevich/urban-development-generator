from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree

from core.urban_generator.blocks.metrics import BlockMetricsResult, MeasuredBlock
from core.urban_generator.domain.crs import WorkingCRS, require_working_crs
from core.urban_generator.roads.road_graph import RoadGraph, RoadGraphEdge

DEFAULT_MAX_FRONTAGE_BLOCKS = 500_000
DEFAULT_MAX_FRONTAGE_ROAD_EDGES = 1_000_000
DEFAULT_MAX_ROAD_CANDIDATES_PER_BLOCK = 20_000


class BlockFrontageValidationError(ValueError):
    """Raised when frontage/access inputs violate the bounded metric-CRS contract."""


@dataclass(frozen=True, slots=True)
class BlockFrontagePolicy:
    """Configurable access distance and minimum positive frontage requirement."""

    access_tolerance_m: float = 0.0
    minimum_frontage_m: float = 0.0

    def __post_init__(self) -> None:
        _require_non_negative_finite("access_tolerance_m", self.access_tolerance_m)
        _require_non_negative_finite("minimum_frontage_m", self.minimum_frontage_m)


@dataclass(frozen=True, slots=True)
class BlockFrontageValidation:
    """Road-access/frontage facts and validation outcome for one measured block."""

    frontage_length_m: float
    frontage_road_ids: tuple[str, ...]
    nearest_road_distance_m: float | None
    nearest_road_edge_id: str | None
    has_access: bool
    has_frontage: bool
    meets_minimum_frontage: bool
    is_valid: bool

    def __post_init__(self) -> None:
        _require_non_negative_finite("frontage_length_m", self.frontage_length_m)
        if not isinstance(self.frontage_road_ids, tuple):
            raise BlockFrontageValidationError("frontage_road_ids must be an immutable tuple")
        if tuple(sorted(set(self.frontage_road_ids))) != self.frontage_road_ids:
            raise BlockFrontageValidationError("frontage_road_ids must be sorted and unique")
        for road_id in self.frontage_road_ids:
            _require_id("road_id", road_id)

        if self.nearest_road_distance_m is None:
            if self.nearest_road_edge_id is not None:
                raise BlockFrontageValidationError(
                    "nearest_road_edge_id must be None when nearest distance is unavailable"
                )
        else:
            _require_non_negative_finite(
                "nearest_road_distance_m",
                self.nearest_road_distance_m,
            )
            if self.nearest_road_edge_id is None:
                raise BlockFrontageValidationError(
                    "nearest_road_edge_id is required when nearest distance is available"
                )
            _require_id("nearest_road_edge_id", self.nearest_road_edge_id)

        for field_name in (
            "has_access",
            "has_frontage",
            "meets_minimum_frontage",
            "is_valid",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise BlockFrontageValidationError(f"{field_name} must be a bool")
        if self.has_frontage != (self.frontage_length_m > 0.0):
            raise BlockFrontageValidationError(
                "has_frontage must reflect positive frontage_length_m"
            )
        if self.meets_minimum_frontage and not self.has_frontage:
            raise BlockFrontageValidationError(
                "minimum frontage cannot pass without positive frontage"
            )
        if self.is_valid != (self.has_access and self.meets_minimum_frontage):
            raise BlockFrontageValidationError(
                "is_valid must require both access and minimum frontage"
            )


@dataclass(frozen=True, slots=True)
class FrontageValidatedBlock:
    """One T03 measured block paired with T04 frontage/access validation."""

    measured_block: MeasuredBlock
    validation: BlockFrontageValidation

    def __post_init__(self) -> None:
        if not isinstance(self.measured_block, MeasuredBlock):
            raise BlockFrontageValidationError("measured_block must be a MeasuredBlock")
        if not isinstance(self.validation, BlockFrontageValidation):
            raise BlockFrontageValidationError(
                "validation must be a BlockFrontageValidation"
            )


@dataclass(frozen=True, slots=True)
class BlockFrontageDiagnostics:
    """Aggregate validation and bounded-index work diagnostics."""

    block_count: int
    valid_block_count: int
    no_access_block_count: int
    no_frontage_block_count: int
    below_minimum_frontage_block_count: int
    total_frontage_m: float
    frontage_candidate_pair_count: int
    nearest_candidate_pair_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "block_count",
            "valid_block_count",
            "no_access_block_count",
            "no_frontage_block_count",
            "below_minimum_frontage_block_count",
            "frontage_candidate_pair_count",
            "nearest_candidate_pair_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        _require_non_negative_finite("total_frontage_m", self.total_frontage_m)
        if self.valid_block_count > self.block_count:
            raise BlockFrontageValidationError("valid_block_count cannot exceed block_count")


@dataclass(frozen=True, slots=True)
class BlockFrontageValidationResult:
    """Deterministic T04 validation results for later block stages."""

    working_crs: WorkingCRS
    policy: BlockFrontagePolicy
    blocks: tuple[FrontageValidatedBlock, ...]
    diagnostics: BlockFrontageDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.working_crs, WorkingCRS):
            raise BlockFrontageValidationError("working_crs must be a WorkingCRS")
        if not isinstance(self.policy, BlockFrontagePolicy):
            raise BlockFrontageValidationError("policy must be a BlockFrontagePolicy")
        if not isinstance(self.blocks, tuple):
            raise BlockFrontageValidationError("blocks must be an immutable tuple")
        if any(not isinstance(block, FrontageValidatedBlock) for block in self.blocks):
            raise BlockFrontageValidationError(
                "blocks must contain only FrontageValidatedBlock values"
            )
        if not isinstance(self.diagnostics, BlockFrontageDiagnostics):
            raise BlockFrontageValidationError(
                "diagnostics must be a BlockFrontageDiagnostics"
            )
        if self.diagnostics.block_count != len(self.blocks):
            raise BlockFrontageValidationError(
                "diagnostics block_count must match result blocks"
            )


class BlockFrontageValidator:
    """Validate road proximity and positive-length block frontage using one road STRtree.

    Candidate discovery is indexed. Access distance uses ``STRtree.nearest`` plus a bounded
    deterministic equal-distance query; frontage uses exact block-boundary/road intersections.
    A point crossing can therefore have zero nearest distance but never counts as frontage, which
    avoids treating a mere transverse (including potentially grade-separated) crossing as valid
    frontage. T04 performs no oversized split, sliver cleanup, zone association or persistence.
    """

    def __init__(
        self,
        *,
        working_srid: int,
        policy: BlockFrontagePolicy | None = None,
        max_blocks: int = DEFAULT_MAX_FRONTAGE_BLOCKS,
        max_road_edges: int = DEFAULT_MAX_FRONTAGE_ROAD_EDGES,
        max_candidates_per_block: int = DEFAULT_MAX_ROAD_CANDIDATES_PER_BLOCK,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        self.policy = policy if policy is not None else BlockFrontagePolicy()
        if not isinstance(self.policy, BlockFrontagePolicy):
            raise BlockFrontageValidationError("policy must be a BlockFrontagePolicy")
        _require_positive_int("max_blocks", max_blocks)
        _require_positive_int("max_road_edges", max_road_edges)
        _require_positive_int("max_candidates_per_block", max_candidates_per_block)
        self.max_blocks = max_blocks
        self.max_road_edges = max_road_edges
        self.max_candidates_per_block = max_candidates_per_block

    def validate(
        self,
        metrics: BlockMetricsResult,
        *,
        road_graph: RoadGraph,
    ) -> BlockFrontageValidationResult:
        if not isinstance(metrics, BlockMetricsResult):
            raise BlockFrontageValidationError("metrics must be a BlockMetricsResult")
        if metrics.working_crs != self.working_crs:
            raise BlockFrontageValidationError(
                "block metrics working CRS must match validator working CRS"
            )
        if not isinstance(road_graph, RoadGraph):
            raise BlockFrontageValidationError("road_graph must be a RoadGraph")
        if road_graph.working_crs != self.working_crs:
            raise BlockFrontageValidationError(
                "road graph working CRS must match validator working CRS"
            )
        if len(metrics.blocks) > self.max_blocks:
            raise BlockFrontageValidationError(
                f"frontage block limit exceeded: {len(metrics.blocks)} > {self.max_blocks}"
            )
        if len(road_graph.edges) > self.max_road_edges:
            raise BlockFrontageValidationError(
                f"frontage road edge limit exceeded: {len(road_graph.edges)} > "
                f"{self.max_road_edges}"
            )

        ordered_edges = tuple(sorted(road_graph.edges, key=lambda edge: edge.edge_id))
        _require_unique_edge_ids(ordered_edges)
        road_geometries = tuple(edge.geometry for edge in ordered_edges)
        tree = STRtree(road_geometries) if road_geometries else None

        validated: list[FrontageValidatedBlock] = []
        frontage_candidate_pair_count = 0
        nearest_candidate_pair_count = 0

        for measured in sorted(metrics.blocks, key=lambda item: item.block.block_id):
            boundary = measured.block.geometry.boundary
            validation: BlockFrontageValidation
            if tree is None:
                validation = _empty_validation()
            else:
                frontage_indexes = tuple(
                    sorted(int(index) for index in tree.query(boundary))
                )
                frontage_candidate_pair_count += len(frontage_indexes)
                _require_candidate_bound(
                    measured.block.block_id,
                    "frontage",
                    len(frontage_indexes),
                    self.max_candidates_per_block,
                )

                nearest_index = int(tree.nearest(boundary))
                nearest_distance_m = float(boundary.distance(road_geometries[nearest_index]))
                _require_non_negative_finite("nearest road distance", nearest_distance_m)
                nearest_indexes = tuple(
                    sorted(
                        int(index)
                        for index in tree.query(
                            boundary,
                            predicate="dwithin",
                            distance=nearest_distance_m,
                        )
                    )
                )
                nearest_candidate_pair_count += len(nearest_indexes)
                _require_candidate_bound(
                    measured.block.block_id,
                    "nearest-road tie",
                    len(nearest_indexes),
                    self.max_candidates_per_block,
                )
                nearest_index = min(
                    nearest_indexes or (nearest_index,),
                    key=lambda index: (
                        float(boundary.distance(road_geometries[index])),
                        ordered_edges[index].edge_id,
                    ),
                )
                nearest_distance_m = float(
                    boundary.distance(road_geometries[nearest_index])
                )

                overlap_geometries: list[BaseGeometry] = []
                frontage_road_ids: set[str] = set()
                for index in frontage_indexes:
                    overlap = boundary.intersection(road_geometries[index])
                    overlap_length_m = float(overlap.length)
                    if not math.isfinite(overlap_length_m) or overlap_length_m < 0.0:
                        raise BlockFrontageValidationError(
                            "road frontage overlap length must be finite and non-negative"
                        )
                    if overlap_length_m > 0.0:
                        overlap_geometries.append(overlap)
                        frontage_road_ids.add(ordered_edges[index].road_id)

                frontage_length_m = (
                    float(unary_union(tuple(overlap_geometries)).length)
                    if overlap_geometries
                    else 0.0
                )
                _require_non_negative_finite("frontage_length_m", frontage_length_m)
                has_access = nearest_distance_m <= self.policy.access_tolerance_m
                has_frontage = frontage_length_m > 0.0
                meets_minimum = (
                    has_frontage
                    and frontage_length_m >= self.policy.minimum_frontage_m
                )
                validation = BlockFrontageValidation(
                    frontage_length_m=frontage_length_m,
                    frontage_road_ids=tuple(sorted(frontage_road_ids)),
                    nearest_road_distance_m=nearest_distance_m,
                    nearest_road_edge_id=ordered_edges[nearest_index].edge_id,
                    has_access=has_access,
                    has_frontage=has_frontage,
                    meets_minimum_frontage=meets_minimum,
                    is_valid=has_access and meets_minimum,
                )

            validated.append(
                FrontageValidatedBlock(
                    measured_block=measured,
                    validation=validation,
                )
            )

        blocks = tuple(validated)
        diagnostics = BlockFrontageDiagnostics(
            block_count=len(blocks),
            valid_block_count=sum(item.validation.is_valid for item in blocks),
            no_access_block_count=sum(not item.validation.has_access for item in blocks),
            no_frontage_block_count=sum(
                not item.validation.has_frontage for item in blocks
            ),
            below_minimum_frontage_block_count=sum(
                item.validation.has_frontage
                and not item.validation.meets_minimum_frontage
                for item in blocks
            ),
            total_frontage_m=math.fsum(
                item.validation.frontage_length_m for item in blocks
            ),
            frontage_candidate_pair_count=frontage_candidate_pair_count,
            nearest_candidate_pair_count=nearest_candidate_pair_count,
        )
        return BlockFrontageValidationResult(
            working_crs=self.working_crs,
            policy=self.policy,
            blocks=blocks,
            diagnostics=diagnostics,
        )


def _empty_validation() -> BlockFrontageValidation:
    return BlockFrontageValidation(
        frontage_length_m=0.0,
        frontage_road_ids=(),
        nearest_road_distance_m=None,
        nearest_road_edge_id=None,
        has_access=False,
        has_frontage=False,
        meets_minimum_frontage=False,
        is_valid=False,
    )


def _require_unique_edge_ids(edges: tuple[RoadGraphEdge, ...]) -> None:
    seen: set[str] = set()
    for edge in edges:
        if edge.edge_id in seen:
            raise BlockFrontageValidationError(f"duplicate road edge_id: {edge.edge_id!r}")
        seen.add(edge.edge_id)


def _require_candidate_bound(
    block_id: str,
    candidate_kind: str,
    candidate_count: int,
    maximum: int,
) -> None:
    if candidate_count > maximum:
        raise BlockFrontageValidationError(
            f"{candidate_kind} candidate limit exceeded for block {block_id!r}: "
            f"{candidate_count} > {maximum}"
        )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise BlockFrontageValidationError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise BlockFrontageValidationError(f"{field_name} must not contain line breaks")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BlockFrontageValidationError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BlockFrontageValidationError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(field_name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlockFrontageValidationError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise BlockFrontageValidationError(
            f"{field_name} must be a finite non-negative number"
        )
