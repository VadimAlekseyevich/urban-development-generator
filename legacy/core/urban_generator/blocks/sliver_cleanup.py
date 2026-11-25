from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from heapq import heappop, heappush

from shapely import normalize
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree

from core.urban_generator.blocks.oversized_split import (
    OversizedBlockSplitResult,
    SplitBlockCandidate,
)
from core.urban_generator.domain.crs import WorkingCRS, require_working_crs

DEFAULT_MAX_SLIVER_BLOCKS = 1_000_000
DEFAULT_MAX_SLIVER_CANDIDATES_PER_BLOCK = 20_000
DEFAULT_MAX_SLIVER_ADJACENCY_PAIRS = 2_000_000
DEFAULT_MAX_SLIVER_OPERATIONS = 1_000_000

_AREA_REL_TOLERANCE = 1e-9
_AREA_ABS_TOLERANCE_M2 = 1e-6


class SliverCleanupError(ValueError):
    """Raised when sliver cleanup violates its bounded metric-CRS contract."""


class UnmergeableSliverAction(StrEnum):
    """Explicit policy for a sliver that has no valid merge target."""

    KEEP = "KEEP"
    DROP = "DROP"


class SliverCleanupAction(StrEnum):
    """Auditable outcome of one sliver cleanup operation."""

    MERGED = "MERGED"
    DROPPED = "DROPPED"
    KEPT_UNRESOLVED = "KEPT_UNRESOLVED"


@dataclass(frozen=True, slots=True)
class SliverCleanupPolicy:
    """Area threshold and explicit merge/drop behavior for T06 cleanup.

    ``merge_within_input_block_only`` defaults to ``True`` so cleanup cannot merge two original
    road-bounded T04 blocks merely because they share the road line as a polygon boundary.
    """

    min_area_m2: float
    unmergeable_action: UnmergeableSliverAction = UnmergeableSliverAction.KEEP
    merge_within_input_block_only: bool = True

    def __post_init__(self) -> None:
        _require_positive_finite("min_area_m2", self.min_area_m2)
        if not isinstance(self.unmergeable_action, UnmergeableSliverAction):
            raise SliverCleanupError(
                "unmergeable_action must be an UnmergeableSliverAction"
            )
        if not isinstance(self.merge_within_input_block_only, bool):
            raise SliverCleanupError("merge_within_input_block_only must be a bool")


@dataclass(frozen=True, slots=True)
class SliverCleanupEvent:
    """One explicit merge/drop/keep decision, including its affected area."""

    action: SliverCleanupAction
    source_member_block_ids: tuple[str, ...]
    target_member_block_ids: tuple[str, ...]
    source_area_m2: float
    shared_boundary_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.action, SliverCleanupAction):
            raise SliverCleanupError("action must be a SliverCleanupAction")
        _require_sorted_unique_ids(
            "source_member_block_ids",
            self.source_member_block_ids,
            allow_empty=False,
        )
        _require_sorted_unique_ids(
            "target_member_block_ids",
            self.target_member_block_ids,
            allow_empty=True,
        )
        _require_positive_finite("source_area_m2", self.source_area_m2)
        _require_non_negative_finite("shared_boundary_m", self.shared_boundary_m)

        if self.action is SliverCleanupAction.MERGED:
            if not self.target_member_block_ids:
                raise SliverCleanupError("MERGED event requires target members")
            if self.shared_boundary_m <= 0.0:
                raise SliverCleanupError("MERGED event requires positive shared boundary")
        else:
            if self.target_member_block_ids:
                raise SliverCleanupError(
                    "non-merge cleanup events must not contain target members"
                )
            if self.shared_boundary_m != 0.0:
                raise SliverCleanupError(
                    "non-merge cleanup events must have zero shared boundary"
                )


@dataclass(frozen=True, slots=True)
class CleanedBlockCandidate:
    """One post-T06 block with complete T05 member provenance."""

    block_id: str
    members: tuple[SplitBlockCandidate, ...]
    geometry: Polygon

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        if not isinstance(self.members, tuple) or not self.members:
            raise SliverCleanupError("members must be a non-empty immutable tuple")
        if any(not isinstance(member, SplitBlockCandidate) for member in self.members):
            raise SliverCleanupError("members must contain only SplitBlockCandidate values")
        member_ids = tuple(member.block_id for member in self.members)
        if member_ids != tuple(sorted(member_ids)) or len(member_ids) != len(set(member_ids)):
            raise SliverCleanupError("members must be sorted by unique block_id")
        _require_polygon("geometry", self.geometry)

    @property
    def member_block_ids(self) -> tuple[str, ...]:
        return tuple(member.block_id for member in self.members)

    @property
    def input_block_ids(self) -> tuple[str, ...]:
        return tuple(sorted({member.input_block_id for member in self.members}))


@dataclass(frozen=True, slots=True)
class SliverCleanupDiagnostics:
    """Aggregate T06 decisions plus exact area accounting."""

    input_block_count: int
    input_sliver_count: int
    output_block_count: int
    merged_event_count: int
    dropped_event_count: int
    kept_unresolved_event_count: int
    operation_count: int
    spatial_candidate_pair_count: int
    adjacency_pair_count: int
    input_area_m2: float
    output_area_m2: float
    dropped_area_m2: float

    def __post_init__(self) -> None:
        for field_name in (
            "input_block_count",
            "input_sliver_count",
            "output_block_count",
            "merged_event_count",
            "dropped_event_count",
            "kept_unresolved_event_count",
            "operation_count",
            "spatial_candidate_pair_count",
            "adjacency_pair_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        _require_non_negative_finite("input_area_m2", self.input_area_m2)
        _require_non_negative_finite("output_area_m2", self.output_area_m2)
        _require_non_negative_finite("dropped_area_m2", self.dropped_area_m2)
        if self.input_sliver_count > self.input_block_count:
            raise SliverCleanupError("input_sliver_count cannot exceed input_block_count")
        if self.operation_count != (
            self.merged_event_count
            + self.dropped_event_count
            + self.kept_unresolved_event_count
        ):
            raise SliverCleanupError("operation_count must equal the sum of cleanup events")
        _require_area_accounting(
            self.input_area_m2,
            self.output_area_m2 + self.dropped_area_m2,
            message="cleanup diagnostics must account for all input area",
        )


@dataclass(frozen=True, slots=True)
class SliverCleanupResult:
    """Deterministic cleaned blocks and an auditable cleanup event stream."""

    working_crs: WorkingCRS
    policy: SliverCleanupPolicy
    blocks: tuple[CleanedBlockCandidate, ...]
    events: tuple[SliverCleanupEvent, ...]
    diagnostics: SliverCleanupDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.working_crs, WorkingCRS):
            raise SliverCleanupError("working_crs must be a WorkingCRS")
        if not isinstance(self.policy, SliverCleanupPolicy):
            raise SliverCleanupError("policy must be a SliverCleanupPolicy")
        if not isinstance(self.blocks, tuple):
            raise SliverCleanupError("blocks must be an immutable tuple")
        if any(not isinstance(block, CleanedBlockCandidate) for block in self.blocks):
            raise SliverCleanupError(
                "blocks must contain only CleanedBlockCandidate values"
            )
        if not isinstance(self.events, tuple):
            raise SliverCleanupError("events must be an immutable tuple")
        if any(not isinstance(event, SliverCleanupEvent) for event in self.events):
            raise SliverCleanupError("events must contain only SliverCleanupEvent values")
        if not isinstance(self.diagnostics, SliverCleanupDiagnostics):
            raise SliverCleanupError("diagnostics must be a SliverCleanupDiagnostics")
        if self.diagnostics.output_block_count != len(self.blocks):
            raise SliverCleanupError(
                "diagnostics output_block_count must match result blocks"
            )
        if self.diagnostics.operation_count != len(self.events):
            raise SliverCleanupError("diagnostics operation_count must match events")


@dataclass(frozen=True, slots=True)
class _Group:
    group_id: str
    members: tuple[SplitBlockCandidate, ...]
    geometry: Polygon

    @property
    def area_m2(self) -> float:
        return float(self.geometry.area)

    @property
    def member_block_ids(self) -> tuple[str, ...]:
        return tuple(member.block_id for member in self.members)

    @property
    def input_block_ids(self) -> tuple[str, ...]:
        return tuple(sorted({member.input_block_id for member in self.members}))


class BlockSliverCleaner:
    """Merge or explicitly keep/drop sub-threshold T05 block fragments.

    Spatial candidates are discovered once through an STRtree. Positive-area overlaps are rejected
    because T06 is cleanup, not overlap repair. Positive shared boundary creates an eligible merge
    adjacency only when the policy allows the two groups to merge. The adjacency graph is then
    contracted incrementally, avoiding repeated all-pairs spatial scans.
    """

    def __init__(
        self,
        *,
        working_srid: int,
        policy: SliverCleanupPolicy,
        max_blocks: int = DEFAULT_MAX_SLIVER_BLOCKS,
        max_candidates_per_block: int = DEFAULT_MAX_SLIVER_CANDIDATES_PER_BLOCK,
        max_adjacency_pairs: int = DEFAULT_MAX_SLIVER_ADJACENCY_PAIRS,
        max_operations: int = DEFAULT_MAX_SLIVER_OPERATIONS,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        if not isinstance(policy, SliverCleanupPolicy):
            raise SliverCleanupError("policy must be a SliverCleanupPolicy")
        self.policy = policy
        for field_name, value in (
            ("max_blocks", max_blocks),
            ("max_candidates_per_block", max_candidates_per_block),
            ("max_adjacency_pairs", max_adjacency_pairs),
            ("max_operations", max_operations),
        ):
            _require_positive_int(field_name, value)
        self.max_blocks = max_blocks
        self.max_candidates_per_block = max_candidates_per_block
        self.max_adjacency_pairs = max_adjacency_pairs
        self.max_operations = max_operations

    def cleanup(self, split_result: OversizedBlockSplitResult) -> SliverCleanupResult:
        if not isinstance(split_result, OversizedBlockSplitResult):
            raise SliverCleanupError("split_result must be an OversizedBlockSplitResult")
        if split_result.working_crs != self.working_crs:
            raise SliverCleanupError(
                "split result working CRS must match cleaner working CRS"
            )
        if len(split_result.blocks) > self.max_blocks:
            raise SliverCleanupError(
                f"sliver cleanup input limit exceeded: {len(split_result.blocks)} > "
                f"{self.max_blocks}"
            )

        ordered_blocks = tuple(sorted(split_result.blocks, key=lambda block: block.block_id))
        _require_unique_block_ids(ordered_blocks)
        input_area_m2 = math.fsum(float(block.geometry.area) for block in ordered_blocks)
        input_sliver_count = sum(
            float(block.geometry.area) < self.policy.min_area_m2 for block in ordered_blocks
        )

        groups = {
            block.block_id: _Group(
                group_id=block.block_id,
                members=(block,),
                geometry=block.geometry,
            )
            for block in ordered_blocks
        }
        adjacency, spatial_candidate_pair_count, adjacency_pair_count = self._build_adjacency(
            ordered_blocks
        )
        versions = {block.block_id: 0 for block in ordered_blocks}
        unresolved: set[str] = set()
        queue: list[tuple[float, tuple[str, ...], int, str]] = []
        for initial_group in groups.values():
            self._push_if_sliver(
                queue,
                initial_group,
                versions[initial_group.group_id],
            )

        events: list[SliverCleanupEvent] = []
        dropped_area_m2 = 0.0

        while queue:
            _queued_area, _queued_members, queued_version, group_id = heappop(queue)
            current_group: _Group | None = groups.get(group_id)
            if current_group is None or versions.get(group_id) != queued_version:
                continue
            if group_id in unresolved:
                continue
            if current_group.area_m2 >= self.policy.min_area_m2:
                continue

            if len(events) >= self.max_operations:
                raise SliverCleanupError(
                    f"sliver cleanup operation limit exceeded: {len(events) + 1} > "
                    f"{self.max_operations}"
                )

            merge_choice = self._choose_merge_target(
                group_id,
                groups=groups,
                adjacency=adjacency,
            )
            if merge_choice is not None:
                target_id, shared_boundary_m, merged_geometry = merge_choice
                target = groups[target_id]
                source_members = current_group.member_block_ids
                target_members = target.member_block_ids
                source_area_m2 = current_group.area_m2

                merged_members = tuple(
                    sorted(
                        (*current_group.members, *target.members),
                        key=lambda member: member.block_id,
                    )
                )
                groups[target_id] = _Group(
                    group_id=target_id,
                    members=merged_members,
                    geometry=merged_geometry,
                )
                self._contract_adjacency(
                    source_id=group_id,
                    target_id=target_id,
                    groups=groups,
                    adjacency=adjacency,
                )
                groups.pop(group_id)
                versions.pop(group_id)
                unresolved.discard(group_id)
                unresolved.discard(target_id)
                versions[target_id] += 1
                self._push_if_sliver(queue, groups[target_id], versions[target_id])
                events.append(
                    SliverCleanupEvent(
                        action=SliverCleanupAction.MERGED,
                        source_member_block_ids=source_members,
                        target_member_block_ids=target_members,
                        source_area_m2=source_area_m2,
                        shared_boundary_m=shared_boundary_m,
                    )
                )
                continue

            if self.policy.unmergeable_action is UnmergeableSliverAction.DROP:
                source_members = current_group.member_block_ids
                source_area_m2 = current_group.area_m2
                dropped_area_m2 += source_area_m2
                self._remove_group(group_id, groups=groups, adjacency=adjacency)
                versions.pop(group_id)
                unresolved.discard(group_id)
                events.append(
                    SliverCleanupEvent(
                        action=SliverCleanupAction.DROPPED,
                        source_member_block_ids=source_members,
                        target_member_block_ids=(),
                        source_area_m2=source_area_m2,
                        shared_boundary_m=0.0,
                    )
                )
                continue

            unresolved.add(group_id)
            events.append(
                SliverCleanupEvent(
                    action=SliverCleanupAction.KEPT_UNRESOLVED,
                    source_member_block_ids=current_group.member_block_ids,
                    target_member_block_ids=(),
                    source_area_m2=current_group.area_m2,
                    shared_boundary_m=0.0,
                )
            )

        ordered_groups = tuple(
            sorted(
                groups.values(),
                key=lambda group: (group.member_block_ids, group.geometry.wkb_hex),
            )
        )
        blocks = tuple(
            CleanedBlockCandidate(
                block_id=f"block:{index:08d}",
                members=group.members,
                geometry=group.geometry,
            )
            for index, group in enumerate(ordered_groups)
        )
        output_area_m2 = math.fsum(float(block.geometry.area) for block in blocks)
        _require_area_accounting(
            input_area_m2,
            output_area_m2 + dropped_area_m2,
            message="sliver cleanup must account for all input area",
        )

        event_tuple = tuple(events)
        diagnostics = SliverCleanupDiagnostics(
            input_block_count=len(ordered_blocks),
            input_sliver_count=input_sliver_count,
            output_block_count=len(blocks),
            merged_event_count=sum(
                event.action is SliverCleanupAction.MERGED for event in event_tuple
            ),
            dropped_event_count=sum(
                event.action is SliverCleanupAction.DROPPED for event in event_tuple
            ),
            kept_unresolved_event_count=sum(
                event.action is SliverCleanupAction.KEPT_UNRESOLVED
                for event in event_tuple
            ),
            operation_count=len(event_tuple),
            spatial_candidate_pair_count=spatial_candidate_pair_count,
            adjacency_pair_count=adjacency_pair_count,
            input_area_m2=input_area_m2,
            output_area_m2=output_area_m2,
            dropped_area_m2=dropped_area_m2,
        )
        return SliverCleanupResult(
            working_crs=self.working_crs,
            policy=self.policy,
            blocks=blocks,
            events=event_tuple,
            diagnostics=diagnostics,
        )

    def _build_adjacency(
        self,
        blocks: tuple[SplitBlockCandidate, ...],
    ) -> tuple[dict[str, dict[str, float]], int, int]:
        adjacency: dict[str, dict[str, float]] = {
            block.block_id: {} for block in blocks
        }
        if not blocks:
            return adjacency, 0, 0

        geometries = tuple(block.geometry for block in blocks)
        tree = STRtree(geometries)
        spatial_candidate_pair_count = 0
        adjacency_pair_count = 0
        processed_pairs: set[tuple[int, int]] = set()

        for index, block in enumerate(blocks):
            candidate_indexes = tuple(
                sorted(
                    int(raw_index)
                    for raw_index in tree.query(block.geometry)
                    if int(raw_index) != index
                )
            )
            spatial_candidate_pair_count += len(candidate_indexes)
            if len(candidate_indexes) > self.max_candidates_per_block:
                raise SliverCleanupError(
                    "sliver spatial candidate limit exceeded for block "
                    f"{block.block_id!r}: {len(candidate_indexes)} > "
                    f"{self.max_candidates_per_block}"
                )

            for other_index in candidate_indexes:
                pair = (min(index, other_index), max(index, other_index))
                if pair in processed_pairs:
                    continue
                processed_pairs.add(pair)
                other = blocks[other_index]
                overlap = block.geometry.intersection(other.geometry)
                overlap_area_m2 = float(overlap.area)
                _require_non_negative_finite("block overlap area", overlap_area_m2)
                overlap_tolerance = max(
                    _AREA_ABS_TOLERANCE_M2,
                    min(float(block.geometry.area), float(other.geometry.area))
                    * _AREA_REL_TOLERANCE,
                )
                if overlap_area_m2 > overlap_tolerance:
                    raise SliverCleanupError(
                        "sliver cleanup input blocks must not overlap by positive area: "
                        f"{block.block_id!r}, {other.block_id!r}"
                    )

                shared_boundary_m = float(
                    block.geometry.boundary.intersection(other.geometry.boundary).length
                )
                _require_non_negative_finite("shared boundary length", shared_boundary_m)
                if shared_boundary_m <= 0.0:
                    continue
                if (
                    self.policy.merge_within_input_block_only
                    and block.input_block_id != other.input_block_id
                ):
                    continue

                adjacency_pair_count += 1
                if adjacency_pair_count > self.max_adjacency_pairs:
                    raise SliverCleanupError(
                        "sliver adjacency pair limit exceeded: "
                        f"{adjacency_pair_count} > {self.max_adjacency_pairs}"
                    )
                adjacency[block.block_id][other.block_id] = shared_boundary_m
                adjacency[other.block_id][block.block_id] = shared_boundary_m

        return adjacency, spatial_candidate_pair_count, adjacency_pair_count

    def _push_if_sliver(
        self,
        queue: list[tuple[float, tuple[str, ...], int, str]],
        group: _Group,
        version: int,
    ) -> None:
        if group.area_m2 < self.policy.min_area_m2:
            heappush(
                queue,
                (group.area_m2, group.member_block_ids, version, group.group_id),
            )

    def _choose_merge_target(
        self,
        source_id: str,
        *,
        groups: dict[str, _Group],
        adjacency: dict[str, dict[str, float]],
    ) -> tuple[str, float, Polygon] | None:
        source = groups[source_id]
        candidates = tuple(
            sorted(
                (
                    (target_id, shared_boundary_m)
                    for target_id, shared_boundary_m in adjacency[source_id].items()
                    if target_id in groups
                    and (
                        not self.policy.merge_within_input_block_only
                        or groups[target_id].input_block_ids == source.input_block_ids
                    )
                ),
                key=lambda item: (
                    -item[1],
                    -groups[item[0]].area_m2,
                    groups[item[0]].member_block_ids,
                ),
            )
        )
        for target_id, shared_boundary_m in candidates:
            merged_geometry = _merge_polygon(source.geometry, groups[target_id].geometry)
            if merged_geometry is not None:
                return target_id, shared_boundary_m, merged_geometry
        return None

    @staticmethod
    def _contract_adjacency(
        *,
        source_id: str,
        target_id: str,
        groups: dict[str, _Group],
        adjacency: dict[str, dict[str, float]],
    ) -> None:
        source_links = dict(adjacency[source_id])
        target_links = adjacency[target_id]
        target_links.pop(source_id, None)

        for neighbor_id, source_weight in source_links.items():
            if neighbor_id == target_id or neighbor_id not in groups:
                continue
            neighbor_links = adjacency[neighbor_id]
            neighbor_links.pop(source_id, None)
            combined_weight = target_links.get(neighbor_id, 0.0) + source_weight
            target_links[neighbor_id] = combined_weight
            neighbor_links[target_id] = combined_weight

        adjacency.pop(source_id)

    @staticmethod
    def _remove_group(
        group_id: str,
        *,
        groups: dict[str, _Group],
        adjacency: dict[str, dict[str, float]],
    ) -> None:
        for neighbor_id in tuple(adjacency[group_id]):
            adjacency[neighbor_id].pop(group_id, None)
        adjacency.pop(group_id)
        groups.pop(group_id)


def _merge_polygon(left: Polygon, right: Polygon) -> Polygon | None:
    merged = unary_union((left, right))
    if not isinstance(merged, Polygon) or merged.is_empty or not merged.is_valid or merged.has_z:
        return None
    normalized = normalize(merged)
    if not isinstance(normalized, Polygon):
        return None
    _require_polygon("merged geometry", normalized)
    _require_area_accounting(
        float(left.area) + float(right.area),
        float(normalized.area),
        message="merged sliver geometry must conserve source area",
    )
    return normalized


def _require_unique_block_ids(blocks: tuple[SplitBlockCandidate, ...]) -> None:
    seen: set[str] = set()
    for block in blocks:
        if block.block_id in seen:
            raise SliverCleanupError(f"duplicate split block_id: {block.block_id!r}")
        seen.add(block.block_id)


def _require_polygon(field_name: str, geometry: BaseGeometry) -> None:
    if not isinstance(geometry, Polygon):
        raise SliverCleanupError(f"{field_name} must be a Polygon")
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise SliverCleanupError(f"{field_name} must be non-empty, valid and 2D")
    _require_positive_finite(f"{field_name} area", float(geometry.area))


def _require_sorted_unique_ids(
    field_name: str,
    values: tuple[str, ...],
    *,
    allow_empty: bool,
) -> None:
    if not isinstance(values, tuple):
        raise SliverCleanupError(f"{field_name} must be an immutable tuple")
    if not values and not allow_empty:
        raise SliverCleanupError(f"{field_name} must not be empty")
    for value in values:
        _require_id(field_name, value)
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise SliverCleanupError(f"{field_name} must be sorted and unique")


def _require_area_accounting(expected: float, actual: float, *, message: str) -> None:
    _require_non_negative_finite("expected area", expected)
    _require_non_negative_finite("actual area", actual)
    if not math.isclose(
        expected,
        actual,
        rel_tol=_AREA_REL_TOLERANCE,
        abs_tol=_AREA_ABS_TOLERANCE_M2,
    ):
        raise SliverCleanupError(message)


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SliverCleanupError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise SliverCleanupError(f"{field_name} must not contain line breaks")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SliverCleanupError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SliverCleanupError(f"{field_name} must be a non-negative integer")


def _require_positive_finite(field_name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SliverCleanupError(f"{field_name} must be a positive finite number")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise SliverCleanupError(f"{field_name} must be a positive finite number")


def _require_non_negative_finite(field_name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SliverCleanupError(f"{field_name} must be a finite non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise SliverCleanupError(f"{field_name} must be a finite non-negative number")
