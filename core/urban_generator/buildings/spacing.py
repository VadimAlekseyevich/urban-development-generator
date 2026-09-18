from __future__ import annotations

import math
import re
from dataclasses import dataclass

from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from core.urban_generator.domain.crs import require_working_crs

DEFAULT_MAX_SPACING_FOOTPRINTS = 100_000
DEFAULT_MAX_SPACING_CANDIDATES = 10_000

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")
_AREA_EPSILON_M2 = 1e-9


class BuildingSpacingError(ValueError):
    """Raised when S08-T08 spacing inputs violate the metric spatial contract."""


class BuildingSpacingCandidateLimitError(BuildingSpacingError):
    """Raised instead of evaluating an unbounded number of nearby footprints."""


@dataclass(frozen=True, slots=True)
class PlacedBuildingFootprint:
    """One already accepted building footprint indexed for later spacing checks."""

    building_id: str
    geometry: BaseGeometry
    working_srid: int

    def __post_init__(self) -> None:
        _require_id("building_id", self.building_id)
        require_working_crs(self.working_srid)
        _require_polygonal_geometry("placed footprint", self.geometry)


@dataclass(frozen=True, slots=True)
class BuildingSpacingSubject:
    """One proposed footprint to validate against already placed buildings."""

    geometry: BaseGeometry
    working_srid: int

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        _require_polygonal_geometry("spacing subject", self.geometry)


@dataclass(frozen=True, slots=True)
class BuildingSpacingPolicy:
    """Metric no-overlap/minimum-gap policy."""

    minimum_gap_m: float = 0.0

    def __post_init__(self) -> None:
        gap = _require_non_negative_finite("minimum_gap_m", self.minimum_gap_m)
        object.__setattr__(self, "minimum_gap_m", gap)


@dataclass(frozen=True, slots=True)
class BuildingSpacingHit:
    """Deterministic first blocking relationship with an existing building."""

    building_id: str
    actual_distance_m: float
    overlap_area_m2: float
    required_gap_m: float

    def __post_init__(self) -> None:
        _require_id("building_id", self.building_id)
        distance = _require_non_negative_finite(
            "actual_distance_m",
            self.actual_distance_m,
        )
        overlap = _require_non_negative_finite(
            "overlap_area_m2",
            self.overlap_area_m2,
        )
        required = _require_non_negative_finite(
            "required_gap_m",
            self.required_gap_m,
        )
        object.__setattr__(self, "actual_distance_m", distance)
        object.__setattr__(self, "overlap_area_m2", overlap)
        object.__setattr__(self, "required_gap_m", required)

    @property
    def overlaps(self) -> bool:
        return self.overlap_area_m2 > _AREA_EPSILON_M2


class BuildingSpacingIndex:
    """STRtree-backed validator for no-overlap and minimum inter-building gap.

    The index is intentionally immutable. A later placement loop may rebuild or manage
    index lifecycle explicitly; S08-T08 only defines the bounded spatial validation
    primitive and does not introduce placement/convergence orchestration.
    """

    def __init__(
        self,
        *,
        footprints: tuple[PlacedBuildingFootprint, ...],
        working_srid: int,
        policy: BuildingSpacingPolicy | None = None,
        max_footprints: int = DEFAULT_MAX_SPACING_FOOTPRINTS,
        max_candidates: int = DEFAULT_MAX_SPACING_CANDIDATES,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        self.policy = policy if policy is not None else BuildingSpacingPolicy()
        if not isinstance(self.policy, BuildingSpacingPolicy):
            raise BuildingSpacingError("policy must be a BuildingSpacingPolicy")
        _require_positive_int("max_footprints", max_footprints)
        _require_positive_int("max_candidates", max_candidates)
        if not isinstance(footprints, tuple):
            raise BuildingSpacingError("footprints must be an immutable tuple")
        if len(footprints) > max_footprints:
            raise BuildingSpacingError(
                "placed footprint limit exceeded: "
                f"{len(footprints)} > {max_footprints}"
            )
        if any(not isinstance(item, PlacedBuildingFootprint) for item in footprints):
            raise BuildingSpacingError(
                "footprints must contain PlacedBuildingFootprint values"
            )
        if any(item.working_srid != self.working_crs.srid for item in footprints):
            raise BuildingSpacingError(
                "placed footprint working_srid must match index working CRS"
            )
        ids = tuple(item.building_id for item in footprints)
        if len(ids) != len(set(ids)):
            raise BuildingSpacingError("placed building ids must be unique")

        ordered = tuple(sorted(footprints, key=lambda item: item.building_id))
        self.footprints = ordered
        self.max_candidates = max_candidates
        self._geometries = tuple(item.geometry for item in ordered)
        self._tree = STRtree(self._geometries) if self._geometries else None

    @property
    def footprint_count(self) -> int:
        return len(self.footprints)

    def check(self, subject: BuildingSpacingSubject) -> BuildingSpacingHit | None:
        """Return the deterministic first spacing violation, or None when valid."""

        if not isinstance(subject, BuildingSpacingSubject):
            raise BuildingSpacingError(
                "subject must be a BuildingSpacingSubject"
            )
        if subject.working_srid != self.working_crs.srid:
            raise BuildingSpacingError(
                "subject working_srid must match index working CRS"
            )
        if self._tree is None:
            return None

        min_x, min_y, max_x, max_y = subject.geometry.bounds
        gap = self.policy.minimum_gap_m
        search_geometry = box(
            min_x - gap,
            min_y - gap,
            max_x + gap,
            max_y + gap,
        )
        candidate_indexes = tuple(
            int(index) for index in self._tree.query(search_geometry)
        )
        if len(candidate_indexes) > self.max_candidates:
            raise BuildingSpacingCandidateLimitError(
                "building spacing candidate limit exceeded: "
                f"{len(candidate_indexes)} > {self.max_candidates}"
            )

        hits: list[BuildingSpacingHit] = []
        for index in candidate_indexes:
            existing = self.footprints[index]
            overlap_area = float(
                subject.geometry.intersection(existing.geometry).area
            )
            distance = float(subject.geometry.distance(existing.geometry))
            overlaps = overlap_area > _AREA_EPSILON_M2
            gap_failed = gap > 0.0 and distance < gap
            if not overlaps and not gap_failed:
                continue
            hits.append(
                BuildingSpacingHit(
                    building_id=existing.building_id,
                    actual_distance_m=distance,
                    overlap_area_m2=overlap_area,
                    required_gap_m=gap,
                )
            )

        if not hits:
            return None
        return min(
            hits,
            key=lambda hit: (
                0 if hit.overlaps else 1,
                hit.actual_distance_m,
                hit.building_id,
            ),
        )

    def allows(self, subject: BuildingSpacingSubject) -> bool:
        """Convenience predicate when violation metadata is not needed."""

        return self.check(subject) is None


def _require_polygonal_geometry(
    field_name: str,
    geometry: BaseGeometry,
) -> None:
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise BuildingSpacingError(
            f"{field_name} must be Polygon or MultiPolygon"
        )
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise BuildingSpacingError(
            f"{field_name} must be non-empty, valid and 2D"
        )
    area = float(geometry.area)
    if not math.isfinite(area) or area <= 0.0:
        raise BuildingSpacingError(
            f"{field_name} must have positive finite area"
        )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise BuildingSpacingError(f"invalid {field_name}: {value!r}")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BuildingSpacingError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuildingSpacingError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise BuildingSpacingError(
            f"{field_name} must be a finite non-negative number"
        )
    return number
