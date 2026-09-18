from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

from shapely.affinity import rotate
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from core.urban_generator.buildings.config import BuildingFootprintStrategy
from core.urban_generator.buildings.envelope import (
    BuildingEnvelopeResult,
    BuildingEnvelopeStatus,
)
from core.urban_generator.buildings.footprint import BuildingFootprintStatus
from core.urban_generator.buildings.placement import (
    BuildingPlacementCandidate,
    BuildingPlacementCandidateKind,
)
from core.urban_generator.domain.crs import require_working_crs

_AXIS_TOLERANCE_M = 1e-6
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")


class BarFootprintError(ValueError):
    """Raised when S08-T05 bar/frontage footprint inputs violate the contract."""


class BarFootprintAxisSource(StrEnum):
    """Provenance of the explicit axis supplied to the bar strategy."""

    FRONTAGE = "frontage"
    ROAD = "road"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class BarFootprintAxis:
    """One straight metric axis selected outside this strategy.

    S08-T05 consumes the axis but does not choose it. Generic road/frontage/principal-axis
    selection remains S08-T07.
    """

    source: BarFootprintAxisSource
    source_ref: str
    geometry: LineString
    working_srid: int

    def __post_init__(self) -> None:
        if not isinstance(self.source, BarFootprintAxisSource):
            raise BarFootprintError(
                "source must be a BarFootprintAxisSource value"
            )
        _require_id("source_ref", self.source_ref)
        require_working_crs(self.working_srid)
        if not isinstance(self.geometry, LineString):
            raise BarFootprintError("axis geometry must be a LineString")
        if (
            self.geometry.is_empty
            or not self.geometry.is_valid
            or self.geometry.has_z
        ):
            raise BarFootprintError(
                "axis geometry must be non-empty, valid and 2D"
            )
        coordinates = tuple(self.geometry.coords)
        if len(coordinates) != 2:
            raise BarFootprintError(
                "bar axis must contain exactly two coordinates"
            )
        if not all(
            math.isfinite(value)
            for coordinate in coordinates
            for value in coordinate[:2]
        ):
            raise BarFootprintError("axis coordinates must be finite")
        if float(self.geometry.length) <= 0.0:
            raise BarFootprintError("axis length must be positive")


@dataclass(frozen=True, slots=True)
class BarFootprintSpec:
    """Metric dimensions for one elongated 2D bar footprint."""

    length_m: float
    depth_m: float

    def __post_init__(self) -> None:
        length = _require_positive_finite("length_m", self.length_m)
        depth = _require_positive_finite("depth_m", self.depth_m)
        if length <= depth:
            raise BarFootprintError(
                "bar length_m must be greater than depth_m"
            )
        object.__setattr__(self, "length_m", length)
        object.__setattr__(self, "depth_m", depth)

    @property
    def area_m2(self) -> float:
        return self.length_m * self.depth_m


@dataclass(frozen=True, slots=True)
class BarFootprintResult:
    """One bar proposal with axis provenance and fit diagnostics."""

    source_id: str
    placement_candidate_id: str
    axis_source: BarFootprintAxisSource
    axis_source_ref: str
    working_srid: int
    status: BuildingFootprintStatus
    center: Point
    axis_angle_degrees: float
    proposed_geometry: Polygon
    strategy: BuildingFootprintStrategy = BuildingFootprintStrategy.BAR

    def __post_init__(self) -> None:
        _require_id("source_id", self.source_id)
        _require_id("placement_candidate_id", self.placement_candidate_id)
        _require_id("axis_source_ref", self.axis_source_ref)
        if not isinstance(self.axis_source, BarFootprintAxisSource):
            raise BarFootprintError(
                "axis_source must be a BarFootprintAxisSource value"
            )
        if self.strategy is not BuildingFootprintStrategy.BAR:
            raise BarFootprintError("bar result requires BAR strategy")
        require_working_crs(self.working_srid)
        if not isinstance(self.status, BuildingFootprintStatus):
            raise BarFootprintError(
                "status must be a BuildingFootprintStatus value"
            )
        if not isinstance(self.center, Point) or self.center.is_empty:
            raise BarFootprintError("center must be a non-empty Point")
        if self.center.has_z or not all(
            math.isfinite(value) for value in (self.center.x, self.center.y)
        ):
            raise BarFootprintError("center must be finite and 2D")
        angle = float(self.axis_angle_degrees)
        if not math.isfinite(angle) or angle < 0.0 or angle >= 180.0:
            raise BarFootprintError(
                "axis_angle_degrees must be finite in [0, 180)"
            )
        if not isinstance(self.proposed_geometry, Polygon):
            raise BarFootprintError(
                "proposed_geometry must be a Polygon"
            )
        if (
            self.proposed_geometry.is_empty
            or not self.proposed_geometry.is_valid
            or self.proposed_geometry.has_z
        ):
            raise BarFootprintError(
                "proposed_geometry must be non-empty, valid and 2D"
            )
        area = float(self.proposed_geometry.area)
        if not math.isfinite(area) or area <= 0.0:
            raise BarFootprintError(
                "proposed_geometry must have positive finite area"
            )

    @property
    def is_ready(self) -> bool:
        return self.status is BuildingFootprintStatus.READY

    @property
    def footprint_geometry(self) -> Polygon | None:
        return self.proposed_geometry if self.is_ready else None

    @property
    def area_m2(self) -> float:
        return float(self.proposed_geometry.area)


class BarFrontageFootprintStrategy:
    """Create an elongated footprint along an explicit frontage/road/block axis."""

    strategy = BuildingFootprintStrategy.BAR

    def __init__(self, *, working_srid: int) -> None:
        self.working_crs = require_working_crs(working_srid)

    def create(
        self,
        candidate: BuildingPlacementCandidate,
        *,
        envelope: BuildingEnvelopeResult,
        axis: BarFootprintAxis,
        spec: BarFootprintSpec,
    ) -> BarFootprintResult:
        geometry = self._validate_inputs(
            candidate=candidate,
            envelope=envelope,
            axis=axis,
            spec=spec,
        )
        angle = _axis_angle_degrees(axis.geometry)
        if axis.source is BarFootprintAxisSource.FRONTAGE:
            center, proposed, status = self._frontage_proposal(
                candidate=candidate,
                envelope_geometry=geometry,
                angle_degrees=angle,
                spec=spec,
            )
        else:
            center = Point(candidate.point.x, candidate.point.y)
            proposed = _bar_polygon(
                center=center,
                angle_degrees=angle,
                spec=spec,
            )
            status = (
                BuildingFootprintStatus.READY
                if geometry.covers(proposed)
                else BuildingFootprintStatus.OUTSIDE_ENVELOPE
            )

        return BarFootprintResult(
            source_id=candidate.source_id,
            placement_candidate_id=candidate.candidate_id,
            axis_source=axis.source,
            axis_source_ref=axis.source_ref,
            working_srid=self.working_crs.srid,
            status=status,
            center=center,
            axis_angle_degrees=angle,
            proposed_geometry=proposed,
        )

    def _validate_inputs(
        self,
        *,
        candidate: BuildingPlacementCandidate,
        envelope: BuildingEnvelopeResult,
        axis: BarFootprintAxis,
        spec: BarFootprintSpec,
    ) -> BaseGeometry:
        if not isinstance(candidate, BuildingPlacementCandidate):
            raise BarFootprintError(
                "candidate must be a BuildingPlacementCandidate"
            )
        if not isinstance(envelope, BuildingEnvelopeResult):
            raise BarFootprintError(
                "envelope must be a BuildingEnvelopeResult"
            )
        if envelope.working_srid != self.working_crs.srid:
            raise BarFootprintError(
                "envelope working_srid must match strategy working CRS"
            )
        if envelope.status is not BuildingEnvelopeStatus.READY:
            raise BarFootprintError(
                "bar footprints require a READY envelope"
            )
        if candidate.source_id != envelope.source_id:
            raise BarFootprintError(
                "candidate source_id must match envelope source_id"
            )
        if not isinstance(axis, BarFootprintAxis):
            raise BarFootprintError("axis must be a BarFootprintAxis")
        if axis.working_srid != self.working_crs.srid:
            raise BarFootprintError(
                "axis working_srid must match strategy working CRS"
            )
        if not isinstance(spec, BarFootprintSpec):
            raise BarFootprintError("spec must be a BarFootprintSpec")
        if axis.geometry.distance(candidate.point) > _AXIS_TOLERANCE_M:
            raise BarFootprintError(
                "placement candidate must lie on the supplied bar axis"
            )
        if axis.source is BarFootprintAxisSource.FRONTAGE:
            if candidate.kind is not BuildingPlacementCandidateKind.FRONTAGE:
                raise BarFootprintError(
                    "frontage axis requires a FRONTAGE placement candidate"
                )
            if candidate.frontage_road_id != axis.source_ref:
                raise BarFootprintError(
                    "frontage candidate road id must match axis source_ref"
                )

        geometry = envelope.buildable_geometry
        if geometry is None:
            raise BarFootprintError(
                "READY envelope must carry buildable geometry"
            )
        return geometry

    def _frontage_proposal(
        self,
        *,
        candidate: BuildingPlacementCandidate,
        envelope_geometry: BaseGeometry,
        angle_degrees: float,
        spec: BarFootprintSpec,
    ) -> tuple[Point, Polygon, BuildingFootprintStatus]:
        angle_radians = math.radians(angle_degrees)
        normal_x = -math.sin(angle_radians)
        normal_y = math.cos(angle_radians)
        offset = spec.depth_m / 2.0

        proposals: list[tuple[Point, Polygon]] = []
        for direction in (-1.0, 1.0):
            center = Point(
                candidate.point.x + direction * normal_x * offset,
                candidate.point.y + direction * normal_y * offset,
            )
            proposals.append(
                (
                    center,
                    _bar_polygon(
                        center=center,
                        angle_degrees=angle_degrees,
                        spec=spec,
                    ),
                )
            )

        fitting = [
            proposal
            for proposal in proposals
            if envelope_geometry.covers(proposal[1])
        ]
        if fitting:
            center, proposed = min(
                fitting,
                key=lambda item: (
                    round(item[0].x, 12),
                    round(item[0].y, 12),
                ),
            )
            return center, proposed, BuildingFootprintStatus.READY

        center, proposed = min(
            proposals,
            key=lambda item: (
                round(item[0].x, 12),
                round(item[0].y, 12),
            ),
        )
        return center, proposed, BuildingFootprintStatus.OUTSIDE_ENVELOPE


def _axis_angle_degrees(axis: LineString) -> float:
    coordinates = tuple(axis.coords)
    start = coordinates[0]
    end = coordinates[1]
    angle = math.degrees(
        math.atan2(end[1] - start[1], end[0] - start[0])
    ) % 180.0
    if math.isclose(angle, 180.0, abs_tol=1e-12):
        return 0.0
    return angle


def _bar_polygon(
    *,
    center: Point,
    angle_degrees: float,
    spec: BarFootprintSpec,
) -> Polygon:
    half_length = spec.length_m / 2.0
    half_depth = spec.depth_m / 2.0
    axis_aligned = box(
        center.x - half_length,
        center.y - half_depth,
        center.x + half_length,
        center.y + half_depth,
    )
    rotated = rotate(
        axis_aligned,
        angle_degrees,
        origin=(center.x, center.y),
        use_radians=False,
    )
    if not isinstance(rotated, Polygon):
        raise BarFootprintError(
            "bar rotation produced non-polygonal geometry"
        )
    return rotated


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise BarFootprintError(f"invalid {field_name}: {value!r}")


def _require_positive_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BarFootprintError(
            f"{field_name} must be a positive finite number"
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise BarFootprintError(
            f"{field_name} must be a positive finite number"
        )
    return number
