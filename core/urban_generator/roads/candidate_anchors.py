from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Point
from shapely.strtree import STRtree

from core.urban_generator.domain import NetworkPoint, require_working_crs
from core.urban_generator.suitability import WeightedSuitabilityResult
from core.urban_generator.zoning import (
    ZoneAssignmentResult,
    ZoneClass,
    ZoningPartitionResult,
)

DEFAULT_MAX_ROAD_ANCHORS = 1_000
DEFAULT_MAX_ANCHOR_SAMPLE_CELLS = 250_000

_ZONE_ORDER = {zone_class: index for index, zone_class in enumerate(ZoneClass)}
_ALL_ZONE_CLASSES = tuple(ZoneClass)


class CandidateRoadAnchorError(ValueError):
    """Raised when candidate-road-anchor sampling violates its spatial contract."""


@dataclass(frozen=True, slots=True)
class CandidateRoadAnchorPolicy:
    """Explicit deterministic sampling policy for S06-T08 road anchor candidates."""

    max_candidates: int = DEFAULT_MAX_ROAD_ANCHORS
    max_sampled_cells: int = DEFAULT_MAX_ANCHOR_SAMPLE_CELLS
    minimum_suitability_score: float = 0.0
    allowed_zone_classes: tuple[ZoneClass, ...] = _ALL_ZONE_CLASSES

    def __post_init__(self) -> None:
        _require_positive_int("max_candidates", self.max_candidates)
        _require_positive_int("max_sampled_cells", self.max_sampled_cells)
        score = _require_finite_number(
            "minimum_suitability_score",
            self.minimum_suitability_score,
        )
        if score < 0.0 or score > 1.0:
            raise CandidateRoadAnchorError(
                "minimum_suitability_score must stay inside 0..1"
            )
        object.__setattr__(self, "minimum_suitability_score", score)

        if not isinstance(self.allowed_zone_classes, tuple) or not self.allowed_zone_classes:
            raise CandidateRoadAnchorError(
                "allowed_zone_classes must be a non-empty immutable tuple"
            )
        if any(not isinstance(item, ZoneClass) for item in self.allowed_zone_classes):
            raise CandidateRoadAnchorError(
                "allowed_zone_classes must contain only ZoneClass values"
            )
        if len(self.allowed_zone_classes) != len(set(self.allowed_zone_classes)):
            raise CandidateRoadAnchorError("allowed_zone_classes must not contain duplicates")
        object.__setattr__(
            self,
            "allowed_zone_classes",
            tuple(sorted(self.allowed_zone_classes, key=_ZONE_ORDER.__getitem__)),
        )


@dataclass(frozen=True, slots=True)
class CandidateRoadAnchor:
    """One raster-cell-center candidate enriched with zoning and suitability metadata."""

    anchor_id: str
    point: NetworkPoint
    zone_class: ZoneClass
    zoning_cell_index: int
    raster_row: int
    raster_col: int
    suitability_score: float

    def __post_init__(self) -> None:
        if not isinstance(self.anchor_id, str) or not self.anchor_id.strip():
            raise CandidateRoadAnchorError("anchor_id must be a non-empty string")
        if "\n" in self.anchor_id or "\r" in self.anchor_id:
            raise CandidateRoadAnchorError("anchor_id must not contain line breaks")
        if not isinstance(self.point, NetworkPoint):
            raise CandidateRoadAnchorError("point must be a NetworkPoint")
        if not isinstance(self.zone_class, ZoneClass):
            raise CandidateRoadAnchorError("zone_class must be a ZoneClass value")
        _require_non_negative_int("zoning_cell_index", self.zoning_cell_index)
        _require_non_negative_int("raster_row", self.raster_row)
        _require_non_negative_int("raster_col", self.raster_col)
        score = _require_finite_number("suitability_score", self.suitability_score)
        if score < 0.0 or score > 1.0:
            raise CandidateRoadAnchorError("suitability_score must stay inside 0..1")
        object.__setattr__(self, "suitability_score", score)


@dataclass(frozen=True, slots=True)
class CandidateRoadAnchorZoneDiagnostics:
    """Eligible-vs-selected anchor counts for one canonical functional zone class."""

    zone_class: ZoneClass
    eligible_count: int
    selected_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.zone_class, ZoneClass):
            raise CandidateRoadAnchorError("zone diagnostic requires a ZoneClass value")
        _require_non_negative_int("eligible_count", self.eligible_count)
        _require_non_negative_int("selected_count", self.selected_count)
        if self.selected_count > self.eligible_count:
            raise CandidateRoadAnchorError(
                "zone selected_count cannot exceed eligible_count"
            )


@dataclass(frozen=True, slots=True)
class CandidateRoadAnchorDiagnostics:
    """Bounded-work diagnostics for deterministic candidate anchor sampling."""

    grid_cell_count: int
    sampled_row_count: int
    sampled_col_count: int
    sampled_cell_count: int
    valid_sampled_cell_count: int
    eligible_candidate_count: int
    selected_candidate_count: int
    candidate_limit_reached: bool
    sampling_limit_applied: bool
    zone_counts: tuple[CandidateRoadAnchorZoneDiagnostics, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "grid_cell_count",
            "sampled_row_count",
            "sampled_col_count",
            "sampled_cell_count",
            "valid_sampled_cell_count",
            "eligible_candidate_count",
            "selected_candidate_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if self.sampled_cell_count != self.sampled_row_count * self.sampled_col_count:
            raise CandidateRoadAnchorError(
                "sampled_cell_count must equal sampled_row_count * sampled_col_count"
            )
        if self.sampled_cell_count > self.grid_cell_count:
            raise CandidateRoadAnchorError("sampled cells cannot exceed grid cells")
        if self.valid_sampled_cell_count > self.sampled_cell_count:
            raise CandidateRoadAnchorError(
                "valid_sampled_cell_count cannot exceed sampled_cell_count"
            )
        if self.eligible_candidate_count > self.valid_sampled_cell_count:
            raise CandidateRoadAnchorError(
                "eligible_candidate_count cannot exceed valid_sampled_cell_count"
            )
        if self.selected_candidate_count > self.eligible_candidate_count:
            raise CandidateRoadAnchorError(
                "selected_candidate_count cannot exceed eligible_candidate_count"
            )
        if not isinstance(self.candidate_limit_reached, bool):
            raise CandidateRoadAnchorError("candidate_limit_reached must be boolean")
        if not isinstance(self.sampling_limit_applied, bool):
            raise CandidateRoadAnchorError("sampling_limit_applied must be boolean")
        if not isinstance(self.zone_counts, tuple):
            raise CandidateRoadAnchorError("zone_counts must be an immutable tuple")
        if tuple(item.zone_class for item in self.zone_counts) != _ALL_ZONE_CLASSES:
            raise CandidateRoadAnchorError(
                "zone_counts must contain every canonical ZoneClass in order"
            )
        if sum(item.eligible_count for item in self.zone_counts) != self.eligible_candidate_count:
            raise CandidateRoadAnchorError(
                "zone eligible counts must sum to eligible_candidate_count"
            )
        if sum(item.selected_count for item in self.zone_counts) != self.selected_candidate_count:
            raise CandidateRoadAnchorError(
                "zone selected counts must sum to selected_candidate_count"
            )


@dataclass(frozen=True, slots=True)
class CandidateRoadAnchorResult:
    """Stable backend-independent candidate set for downstream road connectors."""

    working_srid: int
    anchors: tuple[CandidateRoadAnchor, ...]
    diagnostics: CandidateRoadAnchorDiagnostics
    zoning_config_fingerprint: str
    suitability_config_fingerprint: str
    sampler_version: str

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        if not isinstance(self.anchors, tuple):
            raise CandidateRoadAnchorError("anchors must be an immutable tuple")
        if any(not isinstance(anchor, CandidateRoadAnchor) for anchor in self.anchors):
            raise CandidateRoadAnchorError(
                "anchors must contain only CandidateRoadAnchor values"
            )
        if len({anchor.anchor_id for anchor in self.anchors}) != len(self.anchors):
            raise CandidateRoadAnchorError("anchor_id values must be unique")
        raster_cells = tuple((anchor.raster_row, anchor.raster_col) for anchor in self.anchors)
        if len(set(raster_cells)) != len(raster_cells):
            raise CandidateRoadAnchorError("anchors must occupy unique raster cells")
        if not isinstance(self.diagnostics, CandidateRoadAnchorDiagnostics):
            raise CandidateRoadAnchorError(
                "diagnostics must be CandidateRoadAnchorDiagnostics"
            )
        if self.diagnostics.selected_candidate_count != len(self.anchors):
            raise CandidateRoadAnchorError(
                "diagnostics selected count must match anchors"
            )
        for field_name in (
            "zoning_config_fingerprint",
            "suitability_config_fingerprint",
            "sampler_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise CandidateRoadAnchorError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class _EligibleAnchor:
    row: int
    col: int
    point: NetworkPoint
    zoning_cell_index: int
    zone_class: ZoneClass
    suitability_score: float


class CandidateRoadAnchorSampler:
    """Sample zoning-aware, suitability-ranked candidate road anchors with hard bounds."""

    version = "1"

    def sample(
        self,
        *,
        partition: ZoningPartitionResult,
        assignment: ZoneAssignmentResult,
        suitability: WeightedSuitabilityResult,
        policy: CandidateRoadAnchorPolicy | None = None,
    ) -> CandidateRoadAnchorResult:
        if not isinstance(partition, ZoningPartitionResult):
            raise CandidateRoadAnchorError("partition must be a ZoningPartitionResult")
        if not isinstance(assignment, ZoneAssignmentResult):
            raise CandidateRoadAnchorError("assignment must be a ZoneAssignmentResult")
        if not isinstance(suitability, WeightedSuitabilityResult):
            raise CandidateRoadAnchorError(
                "suitability must be a WeightedSuitabilityResult"
            )
        if policy is None:
            policy = CandidateRoadAnchorPolicy()
        if not isinstance(policy, CandidateRoadAnchorPolicy):
            raise CandidateRoadAnchorError("policy must be a CandidateRoadAnchorPolicy")

        _validate_zoning_alignment(partition=partition, assignment=assignment)
        grid = suitability.grid
        if partition.working_srid != grid.working_srid:
            raise CandidateRoadAnchorError(
                "zoning partition and suitability grid must use the same working_srid"
            )

        row_indexes, col_indexes = _bounded_grid_indexes(
            height=grid.height,
            width=grid.width,
            max_sampled_cells=policy.max_sampled_cells,
        )
        geometries = tuple(cell.geometry for cell in partition.cells)
        tree = STRtree(geometries)
        allowed_zone_classes = set(policy.allowed_zone_classes)

        valid_sampled_cell_count = 0
        eligible: list[_EligibleAnchor] = []
        for row in row_indexes:
            for col in col_indexes:
                if not bool(suitability.valid_mask[row, col]):
                    continue
                valid_sampled_cell_count += 1
                score = float(suitability.scores[row, col])
                if score < policy.minimum_suitability_score:
                    continue

                point = _grid_cell_center(suitability=suitability, row=row, col=col)
                zoning_cell_index = _zoning_cell_for_point(
                    point=point,
                    geometries=geometries,
                    tree=tree,
                )
                if zoning_cell_index is None:
                    continue
                zone_class = assignment.assignments[zoning_cell_index].zone_class
                if zone_class not in allowed_zone_classes:
                    continue
                eligible.append(
                    _EligibleAnchor(
                        row=row,
                        col=col,
                        point=NetworkPoint(x_m=point.x, y_m=point.y),
                        zoning_cell_index=zoning_cell_index,
                        zone_class=zone_class,
                        suitability_score=score,
                    )
                )

        eligible.sort(
            key=lambda item: (
                -item.suitability_score,
                _ZONE_ORDER[item.zone_class],
                item.zoning_cell_index,
                item.row,
                item.col,
            )
        )
        selected = eligible[: policy.max_candidates]
        anchors = tuple(
            CandidateRoadAnchor(
                anchor_id=f"anchor:r{item.row:08d}:c{item.col:08d}",
                point=item.point,
                zone_class=item.zone_class,
                zoning_cell_index=item.zoning_cell_index,
                raster_row=item.row,
                raster_col=item.col,
                suitability_score=item.suitability_score,
            )
            for item in selected
        )

        eligible_by_zone = {zone_class: 0 for zone_class in ZoneClass}
        selected_by_zone = {zone_class: 0 for zone_class in ZoneClass}
        for item in eligible:
            eligible_by_zone[item.zone_class] += 1
        for anchor in anchors:
            selected_by_zone[anchor.zone_class] += 1

        sampled_cell_count = len(row_indexes) * len(col_indexes)
        diagnostics = CandidateRoadAnchorDiagnostics(
            grid_cell_count=grid.cell_count,
            sampled_row_count=len(row_indexes),
            sampled_col_count=len(col_indexes),
            sampled_cell_count=sampled_cell_count,
            valid_sampled_cell_count=valid_sampled_cell_count,
            eligible_candidate_count=len(eligible),
            selected_candidate_count=len(anchors),
            candidate_limit_reached=len(eligible) > policy.max_candidates,
            sampling_limit_applied=sampled_cell_count < grid.cell_count,
            zone_counts=tuple(
                CandidateRoadAnchorZoneDiagnostics(
                    zone_class=zone_class,
                    eligible_count=eligible_by_zone[zone_class],
                    selected_count=selected_by_zone[zone_class],
                )
                for zone_class in ZoneClass
            ),
        )
        return CandidateRoadAnchorResult(
            working_srid=grid.working_srid,
            anchors=anchors,
            diagnostics=diagnostics,
            zoning_config_fingerprint=assignment.zoning_config_fingerprint,
            suitability_config_fingerprint=suitability.config_fingerprint,
            sampler_version=self.version,
        )


def _validate_zoning_alignment(
    *,
    partition: ZoningPartitionResult,
    assignment: ZoneAssignmentResult,
) -> None:
    if len(partition.cells) != len(assignment.assignments):
        raise CandidateRoadAnchorError(
            "partition and assignment must contain the same number of zoning cells"
        )
    for cell_index, cell in enumerate(partition.cells):
        assigned = assignment.assignments[cell_index]
        if assigned.cell_index != cell_index or assigned.seed_index != cell.seed_index:
            raise CandidateRoadAnchorError(
                "assignment cell/seed references must match the zoning partition"
            )
        if not math.isclose(
            assigned.area_m2,
            cell.area_m2,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise CandidateRoadAnchorError(
                "assignment area_m2 must match the zoning partition cell area"
            )


def _bounded_grid_indexes(
    *,
    height: int,
    width: int,
    max_sampled_cells: int,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    cell_count = height * width
    if cell_count <= max_sampled_cells:
        return tuple(range(height)), tuple(range(width))

    approximate_rows = max(
        1,
        int(math.sqrt(max_sampled_cells * height / width)),
    )
    row_count = min(height, max_sampled_cells, approximate_rows)
    col_count = min(width, max(1, max_sampled_cells // row_count))
    return (
        _evenly_spaced_indexes(size=height, count=row_count),
        _evenly_spaced_indexes(size=width, count=col_count),
    )


def _evenly_spaced_indexes(*, size: int, count: int) -> tuple[int, ...]:
    if count <= 0 or count > size:
        raise CandidateRoadAnchorError("sample axis count must be inside 1..axis size")
    indexes = tuple(((2 * index + 1) * size) // (2 * count) for index in range(count))
    if len(set(indexes)) != len(indexes):
        raise CandidateRoadAnchorError("bounded axis sampling produced duplicate indexes")
    return indexes


def _grid_cell_center(
    *,
    suitability: WeightedSuitabilityResult,
    row: int,
    col: int,
) -> Point:
    grid = suitability.grid
    min_x, _min_y, _max_x, max_y = grid.bounds
    return Point(
        min_x + (col + 0.5) * grid.cell_width_m,
        max_y - (row + 0.5) * grid.cell_height_m,
    )


def _zoning_cell_for_point(
    *,
    point: Point,
    geometries: tuple[object, ...],
    tree: STRtree,
) -> int | None:
    matches = sorted(
        int(candidate_index)
        for candidate_index in tree.query(point)
        if geometries[int(candidate_index)].covers(point)  # type: ignore[attr-defined]
    )
    return matches[0] if matches else None


def _require_finite_number(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CandidateRoadAnchorError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise CandidateRoadAnchorError(f"{field_name} must be a finite number")
    return number


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CandidateRoadAnchorError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CandidateRoadAnchorError(f"{field_name} must be a non-negative integer")
