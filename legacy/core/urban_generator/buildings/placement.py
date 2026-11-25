from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import LineString, MultiLineString, Point
from shapely.geometry.base import BaseGeometry

from core.urban_generator.buildings.envelope import (
    BuildingEnvelopeResult,
    BuildingEnvelopeStatus,
)
from core.urban_generator.domain.crs import require_working_crs

DEFAULT_MAX_GRID_SCAN_CELLS = 250_000
DEFAULT_MAX_FRONTAGE_SAMPLES = 50_000
DEFAULT_MAX_PLACEMENT_CANDIDATES = 50_000

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")
_BOUNDARY_TOLERANCE_M = 1e-6


class BuildingPlacementCandidateError(ValueError):
    """Raised when S08-T03 candidate generation violates its bounded contract."""


class BuildingPlacementCandidateKind(StrEnum):
    GRID = "GRID"
    FRONTAGE = "FRONTAGE"


@dataclass(frozen=True, slots=True)
class BuildingPlacementFrontage:
    """Road-backed linear frontage used only to create placement anchors."""

    road_id: str
    geometry: BaseGeometry

    def __post_init__(self) -> None:
        _require_id("road_id", self.road_id)
        if not isinstance(self.geometry, (LineString, MultiLineString)):
            raise BuildingPlacementCandidateError(
                "frontage geometry must be LineString or MultiLineString"
            )
        if self.geometry.is_empty or not self.geometry.is_valid or self.geometry.has_z:
            raise BuildingPlacementCandidateError(
                "frontage geometry must be non-empty, valid and 2D"
            )
        length = float(self.geometry.length)
        if not math.isfinite(length) or length <= 0.0:
            raise BuildingPlacementCandidateError(
                "frontage geometry must have positive finite length"
            )


@dataclass(frozen=True, slots=True)
class BuildingPlacementCandidatePolicy:
    """Metric spacing and bounded-work policy for candidate anchor generation."""

    grid_spacing_m: float = 20.0
    frontage_spacing_m: float = 12.0
    include_grid: bool = True
    include_frontage: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "grid_spacing_m",
            _require_positive_finite("grid_spacing_m", self.grid_spacing_m),
        )
        object.__setattr__(
            self,
            "frontage_spacing_m",
            _require_positive_finite(
                "frontage_spacing_m",
                self.frontage_spacing_m,
            ),
        )
        if not isinstance(self.include_grid, bool):
            raise BuildingPlacementCandidateError("include_grid must be a bool")
        if not isinstance(self.include_frontage, bool):
            raise BuildingPlacementCandidateError("include_frontage must be a bool")
        if not self.include_grid and not self.include_frontage:
            raise BuildingPlacementCandidateError(
                "at least one candidate strategy must be enabled"
            )


@dataclass(frozen=True, slots=True)
class BuildingPlacementCandidate:
    """One deterministic anchor; footprint/orientation are later S08 concerns."""

    candidate_id: str
    source_id: str
    kind: BuildingPlacementCandidateKind
    point: Point
    frontage_road_id: str | None = None

    def __post_init__(self) -> None:
        _require_id("candidate_id", self.candidate_id)
        _require_id("source_id", self.source_id)
        if not isinstance(self.kind, BuildingPlacementCandidateKind):
            raise BuildingPlacementCandidateError(
                "kind must be a BuildingPlacementCandidateKind"
            )
        if not isinstance(self.point, Point):
            raise BuildingPlacementCandidateError("candidate point must be a Point")
        if self.point.is_empty or self.point.has_z:
            raise BuildingPlacementCandidateError(
                "candidate point must be non-empty and 2D"
            )
        if not all(math.isfinite(value) for value in (self.point.x, self.point.y)):
            raise BuildingPlacementCandidateError(
                "candidate coordinates must be finite"
            )
        if self.kind is BuildingPlacementCandidateKind.FRONTAGE:
            if self.frontage_road_id is None:
                raise BuildingPlacementCandidateError(
                    "frontage candidate requires frontage_road_id"
                )
            _require_id("frontage_road_id", self.frontage_road_id)
        elif self.frontage_road_id is not None:
            raise BuildingPlacementCandidateError(
                "grid candidate must not carry frontage_road_id"
            )


@dataclass(frozen=True, slots=True)
class BuildingPlacementCandidateDiagnostics:
    grid_scan_cell_count: int
    grid_candidate_count: int
    frontage_sample_count: int
    frontage_candidate_count: int
    candidate_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "grid_scan_cell_count",
            "grid_candidate_count",
            "frontage_sample_count",
            "frontage_candidate_count",
            "candidate_count",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BuildingPlacementCandidateError(
                    f"{field_name} must be a non-negative integer"
                )
        if (
            self.grid_candidate_count + self.frontage_candidate_count
            != self.candidate_count
        ):
            raise BuildingPlacementCandidateError(
                "candidate strategy counts must sum to candidate_count"
            )


@dataclass(frozen=True, slots=True)
class BuildingPlacementCandidateResult:
    source_id: str
    working_srid: int
    candidates: tuple[BuildingPlacementCandidate, ...]
    diagnostics: BuildingPlacementCandidateDiagnostics

    def __post_init__(self) -> None:
        _require_id("source_id", self.source_id)
        require_working_crs(self.working_srid)
        if not isinstance(self.candidates, tuple):
            raise BuildingPlacementCandidateError(
                "candidates must be an immutable tuple"
            )
        if any(
            not isinstance(item, BuildingPlacementCandidate)
            for item in self.candidates
        ):
            raise BuildingPlacementCandidateError(
                "candidates must contain BuildingPlacementCandidate values"
            )
        ids = tuple(item.candidate_id for item in self.candidates)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise BuildingPlacementCandidateError(
                "candidate ids must be sorted and unique"
            )
        if self.diagnostics.candidate_count != len(self.candidates):
            raise BuildingPlacementCandidateError(
                "diagnostics candidate_count must match candidates"
            )


class BuildingPlacementCandidateGenerator:
    """Generate deterministic bounded grid/frontage anchors inside one READY envelope."""

    def __init__(
        self,
        *,
        working_srid: int,
        policy: BuildingPlacementCandidatePolicy | None = None,
        max_grid_scan_cells: int = DEFAULT_MAX_GRID_SCAN_CELLS,
        max_frontage_samples: int = DEFAULT_MAX_FRONTAGE_SAMPLES,
        max_candidates: int = DEFAULT_MAX_PLACEMENT_CANDIDATES,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        self.policy = (
            policy if policy is not None else BuildingPlacementCandidatePolicy()
        )
        if not isinstance(self.policy, BuildingPlacementCandidatePolicy):
            raise BuildingPlacementCandidateError(
                "policy must be a BuildingPlacementCandidatePolicy"
            )
        for name, value in (
            ("max_grid_scan_cells", max_grid_scan_cells),
            ("max_frontage_samples", max_frontage_samples),
            ("max_candidates", max_candidates),
        ):
            _require_positive_int(name, value)
        self.max_grid_scan_cells = max_grid_scan_cells
        self.max_frontage_samples = max_frontage_samples
        self.max_candidates = max_candidates

    def generate(
        self,
        envelope: BuildingEnvelopeResult,
        *,
        frontages: tuple[BuildingPlacementFrontage, ...] = (),
    ) -> BuildingPlacementCandidateResult:
        geometry = self._validate_inputs(envelope, frontages)
        drafts: list[tuple[BuildingPlacementCandidateKind, Point, str | None]] = []
        grid_scan_cell_count = 0
        frontage_sample_count = 0

        if self.policy.include_grid:
            grid_points, grid_scan_cell_count = self._grid_points(geometry)
            drafts.extend(
                (BuildingPlacementCandidateKind.GRID, point, None)
                for point in grid_points
            )

        if self.policy.include_frontage:
            frontage_points, frontage_sample_count = self._frontage_points(
                geometry,
                frontages,
            )
            drafts.extend(
                (BuildingPlacementCandidateKind.FRONTAGE, point, road_id)
                for point, road_id in frontage_points
            )

        if len(drafts) > self.max_candidates:
            raise BuildingPlacementCandidateError(
                "placement candidate limit exceeded: "
                f"{len(drafts)} > {self.max_candidates}"
            )

        ordered = tuple(
            sorted(
                drafts,
                key=lambda item: (
                    0 if item[0] is BuildingPlacementCandidateKind.GRID else 1,
                    round(item[1].x, 12),
                    round(item[1].y, 12),
                    item[2] or "",
                ),
            )
        )
        candidates = tuple(
            BuildingPlacementCandidate(
                candidate_id=f"candidate:{index:08d}",
                source_id=envelope.source_id,
                kind=kind,
                point=point,
                frontage_road_id=road_id,
            )
            for index, (kind, point, road_id) in enumerate(ordered)
        )
        grid_count = sum(
            item.kind is BuildingPlacementCandidateKind.GRID
            for item in candidates
        )
        frontage_count = len(candidates) - grid_count
        return BuildingPlacementCandidateResult(
            source_id=envelope.source_id,
            working_srid=self.working_crs.srid,
            candidates=candidates,
            diagnostics=BuildingPlacementCandidateDiagnostics(
                grid_scan_cell_count=grid_scan_cell_count,
                grid_candidate_count=grid_count,
                frontage_sample_count=frontage_sample_count,
                frontage_candidate_count=frontage_count,
                candidate_count=len(candidates),
            ),
        )

    def _validate_inputs(
        self,
        envelope: BuildingEnvelopeResult,
        frontages: tuple[BuildingPlacementFrontage, ...],
    ) -> BaseGeometry:
        if not isinstance(envelope, BuildingEnvelopeResult):
            raise BuildingPlacementCandidateError(
                "envelope must be a BuildingEnvelopeResult"
            )
        if envelope.working_srid != self.working_crs.srid:
            raise BuildingPlacementCandidateError(
                "envelope working_srid must match generator working CRS"
            )
        if envelope.status is not BuildingEnvelopeStatus.READY:
            raise BuildingPlacementCandidateError(
                "placement candidates require a READY buildable envelope"
            )
        geometry = envelope.buildable_geometry
        if geometry is None:
            raise BuildingPlacementCandidateError(
                "READY envelope must carry buildable geometry"
            )
        if not isinstance(frontages, tuple):
            raise BuildingPlacementCandidateError(
                "frontages must be an immutable tuple"
            )
        if any(not isinstance(item, BuildingPlacementFrontage) for item in frontages):
            raise BuildingPlacementCandidateError(
                "frontages must contain BuildingPlacementFrontage values"
            )
        for frontage in frontages:
            off_boundary = float(
                frontage.geometry.difference(geometry.boundary).length
            )
            if off_boundary > _BOUNDARY_TOLERANCE_M:
                raise BuildingPlacementCandidateError(
                    f"frontage {frontage.road_id!r} must lie on envelope boundary"
                )
        return geometry

    def _grid_points(
        self,
        geometry: BaseGeometry,
    ) -> tuple[tuple[Point, ...], int]:
        min_x, min_y, max_x, max_y = geometry.bounds
        spacing = self.policy.grid_spacing_m
        x_values = _axis_samples(min_x, max_x, spacing)
        y_values = _axis_samples(min_y, max_y, spacing)
        scan_count = len(x_values) * len(y_values)
        if scan_count > self.max_grid_scan_cells:
            raise BuildingPlacementCandidateError(
                "grid scan cell limit exceeded: "
                f"{scan_count} > {self.max_grid_scan_cells}"
            )

        points = tuple(
            point
            for y in y_values
            for x in x_values
            if geometry.covers(point := Point(x, y))
        )
        if not points:
            centroid = geometry.representative_point()
            if geometry.covers(centroid):
                return (Point(float(centroid.x), float(centroid.y)),), scan_count
        return points, scan_count

    def _frontage_points(
        self,
        geometry: BaseGeometry,
        frontages: tuple[BuildingPlacementFrontage, ...],
    ) -> tuple[tuple[tuple[Point, str], ...], int]:
        samples: list[tuple[Point, str]] = []
        sample_count = 0
        for frontage in sorted(
            frontages,
            key=lambda item: (item.road_id, item.geometry.wkb_hex),
        ):
            distances = _line_sample_distances(
                float(frontage.geometry.length),
                self.policy.frontage_spacing_m,
            )
            sample_count += len(distances)
            if sample_count > self.max_frontage_samples:
                raise BuildingPlacementCandidateError(
                    "frontage sample limit exceeded: "
                    f"{sample_count} > {self.max_frontage_samples}"
                )
            for distance in distances:
                raw = frontage.geometry.interpolate(distance)
                point = Point(float(raw.x), float(raw.y))
                if geometry.boundary.distance(point) <= _BOUNDARY_TOLERANCE_M:
                    samples.append((point, frontage.road_id))
        return tuple(samples), sample_count


def _axis_samples(minimum: float, maximum: float, spacing: float) -> tuple[float, ...]:
    span = maximum - minimum
    if span <= 0.0:
        return ()
    count = max(1, int(math.floor(span / spacing)))
    if count == 1:
        return ((minimum + maximum) / 2.0,)
    step = span / count
    return tuple(minimum + (index + 0.5) * step for index in range(count))


def _line_sample_distances(length: float, spacing: float) -> tuple[float, ...]:
    count = max(1, int(math.floor(length / spacing)))
    step = length / count
    return tuple((index + 0.5) * step for index in range(count))


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise BuildingPlacementCandidateError(
            f"invalid {field_name}: {value!r}"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BuildingPlacementCandidateError(
            f"{field_name} must be a positive integer"
        )


def _require_positive_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuildingPlacementCandidateError(
            f"{field_name} must be a positive finite number"
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise BuildingPlacementCandidateError(
            f"{field_name} must be a positive finite number"
        )
    return number
