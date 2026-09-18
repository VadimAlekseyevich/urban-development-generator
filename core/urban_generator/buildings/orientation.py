from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from core.urban_generator.buildings.envelope import (
    BuildingEnvelopeResult,
    BuildingEnvelopeStatus,
)
from core.urban_generator.buildings.placement import (
    BuildingPlacementCandidate,
    BuildingPlacementCandidateKind,
)
from core.urban_generator.domain.crs import require_working_crs

DEFAULT_MAX_ORIENTATION_AXES = 10_000
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")


class BuildingOrientationError(ValueError):
    """Raised when S08-T07 orientation inputs violate the core contract."""


class BuildingOrientationSource(StrEnum):
    """Canonical orientation provenance for building footprint strategies."""

    FRONTAGE = "frontage"
    ROAD = "road"
    PRINCIPAL_AXIS = "principal_axis"


@dataclass(frozen=True, slots=True)
class BuildingOrientationAxis:
    """One explicit metric axis that may orient a building footprint."""

    source: BuildingOrientationSource
    source_ref: str
    geometry: LineString
    working_srid: int

    def __post_init__(self) -> None:
        if not isinstance(self.source, BuildingOrientationSource):
            raise BuildingOrientationError(
                "source must be a BuildingOrientationSource value"
            )
        _require_id("source_ref", self.source_ref)
        require_working_crs(self.working_srid)
        _require_axis_geometry(self.geometry)


@dataclass(frozen=True, slots=True)
class BuildingOrientationPolicy:
    """Bounded deterministic source-priority policy.

    Frontage wins when the placement candidate itself was created from frontage.
    Otherwise explicit road axes are preferred, then the envelope principal axis.
    """

    max_axis_distance_m: float | None = None

    def __post_init__(self) -> None:
        if self.max_axis_distance_m is None:
            return
        value = _require_non_negative_finite(
            "max_axis_distance_m",
            self.max_axis_distance_m,
        )
        object.__setattr__(self, "max_axis_distance_m", value)


@dataclass(frozen=True, slots=True)
class BuildingOrientationResult:
    """Selected axis and normalized orientation angle in the working CRS."""

    source_id: str
    placement_candidate_id: str
    source: BuildingOrientationSource
    source_ref: str
    working_srid: int
    axis: LineString
    angle_degrees: float
    distance_to_anchor_m: float

    def __post_init__(self) -> None:
        _require_id("source_id", self.source_id)
        _require_id("placement_candidate_id", self.placement_candidate_id)
        if not isinstance(self.source, BuildingOrientationSource):
            raise BuildingOrientationError(
                "source must be a BuildingOrientationSource value"
            )
        _require_id("source_ref", self.source_ref)
        require_working_crs(self.working_srid)
        _require_axis_geometry(self.axis)
        angle = float(self.angle_degrees)
        if not math.isfinite(angle) or angle < 0.0 or angle >= 180.0:
            raise BuildingOrientationError(
                "angle_degrees must be finite in [0, 180)"
            )
        distance = _require_non_negative_finite(
            "distance_to_anchor_m",
            self.distance_to_anchor_m,
        )
        object.__setattr__(self, "angle_degrees", angle)
        object.__setattr__(self, "distance_to_anchor_m", distance)


class BuildingOrientationStrategy:
    """Select frontage/road orientation with deterministic principal-axis fallback."""

    def __init__(
        self,
        *,
        working_srid: int,
        policy: BuildingOrientationPolicy | None = None,
        max_axes: int = DEFAULT_MAX_ORIENTATION_AXES,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        self.policy = policy if policy is not None else BuildingOrientationPolicy()
        if not isinstance(self.policy, BuildingOrientationPolicy):
            raise BuildingOrientationError(
                "policy must be a BuildingOrientationPolicy"
            )
        if isinstance(max_axes, bool) or not isinstance(max_axes, int) or max_axes <= 0:
            raise BuildingOrientationError("max_axes must be a positive integer")
        self.max_axes = max_axes

    def orient(
        self,
        candidate: BuildingPlacementCandidate,
        *,
        envelope: BuildingEnvelopeResult,
        frontage_axes: tuple[BuildingOrientationAxis, ...] = (),
        road_axes: tuple[BuildingOrientationAxis, ...] = (),
    ) -> BuildingOrientationResult:
        geometry = self._validate_inputs(
            candidate=candidate,
            envelope=envelope,
            frontage_axes=frontage_axes,
            road_axes=road_axes,
        )

        if candidate.kind is BuildingPlacementCandidateKind.FRONTAGE:
            matching = tuple(
                axis
                for axis in frontage_axes
                if axis.source is BuildingOrientationSource.FRONTAGE
                and axis.source_ref == candidate.frontage_road_id
            )
            selected = self._nearest_axis(candidate.point, matching)
            if selected is not None:
                return self._result(
                    candidate=candidate,
                    axis=selected,
                )

        selected_road = self._nearest_axis(
            candidate.point,
            tuple(
                axis
                for axis in road_axes
                if axis.source is BuildingOrientationSource.ROAD
            ),
        )
        if selected_road is not None:
            return self._result(
                candidate=candidate,
                axis=selected_road,
            )

        principal = BuildingOrientationAxis(
            source=BuildingOrientationSource.PRINCIPAL_AXIS,
            source_ref=envelope.source_id,
            geometry=_principal_axis(geometry),
            working_srid=self.working_crs.srid,
        )
        return self._result(candidate=candidate, axis=principal)

    def _validate_inputs(
        self,
        *,
        candidate: BuildingPlacementCandidate,
        envelope: BuildingEnvelopeResult,
        frontage_axes: tuple[BuildingOrientationAxis, ...],
        road_axes: tuple[BuildingOrientationAxis, ...],
    ) -> BaseGeometry:
        if not isinstance(candidate, BuildingPlacementCandidate):
            raise BuildingOrientationError(
                "candidate must be a BuildingPlacementCandidate"
            )
        if not isinstance(envelope, BuildingEnvelopeResult):
            raise BuildingOrientationError(
                "envelope must be a BuildingEnvelopeResult"
            )
        if envelope.working_srid != self.working_crs.srid:
            raise BuildingOrientationError(
                "envelope working_srid must match strategy working CRS"
            )
        if envelope.status is not BuildingEnvelopeStatus.READY:
            raise BuildingOrientationError(
                "orientation requires a READY envelope"
            )
        if candidate.source_id != envelope.source_id:
            raise BuildingOrientationError(
                "candidate source_id must match envelope source_id"
            )
        for name, axes in (
            ("frontage_axes", frontage_axes),
            ("road_axes", road_axes),
        ):
            if not isinstance(axes, tuple):
                raise BuildingOrientationError(
                    f"{name} must be an immutable tuple"
                )
            if any(not isinstance(axis, BuildingOrientationAxis) for axis in axes):
                raise BuildingOrientationError(
                    f"{name} must contain BuildingOrientationAxis values"
                )
            if any(axis.working_srid != self.working_crs.srid for axis in axes):
                raise BuildingOrientationError(
                    f"{name} working_srid must match strategy working CRS"
                )
        total_axes = len(frontage_axes) + len(road_axes)
        if total_axes > self.max_axes:
            raise BuildingOrientationError(
                "orientation axis limit exceeded: "
                f"{total_axes} > {self.max_axes}"
            )
        geometry = envelope.buildable_geometry
        if geometry is None:
            raise BuildingOrientationError(
                "READY envelope must carry buildable geometry"
            )
        return geometry

    def _nearest_axis(
        self,
        anchor: Point,
        axes: tuple[BuildingOrientationAxis, ...],
    ) -> BuildingOrientationAxis | None:
        ranked = sorted(
            axes,
            key=lambda axis: (
                float(axis.geometry.distance(anchor)),
                axis.source_ref,
                round(_axis_angle_degrees(axis.geometry), 12),
                axis.geometry.wkb_hex,
            ),
        )
        if not ranked:
            return None
        selected = ranked[0]
        distance = float(selected.geometry.distance(anchor))
        maximum = self.policy.max_axis_distance_m
        if maximum is not None and distance > maximum:
            return None
        return selected

    def _result(
        self,
        *,
        candidate: BuildingPlacementCandidate,
        axis: BuildingOrientationAxis,
    ) -> BuildingOrientationResult:
        return BuildingOrientationResult(
            source_id=candidate.source_id,
            placement_candidate_id=candidate.candidate_id,
            source=axis.source,
            source_ref=axis.source_ref,
            working_srid=self.working_crs.srid,
            axis=axis.geometry,
            angle_degrees=_axis_angle_degrees(axis.geometry),
            distance_to_anchor_m=float(axis.geometry.distance(candidate.point)),
        )


def _principal_axis(geometry: BaseGeometry) -> LineString:
    rectangle = geometry.minimum_rotated_rectangle
    if not isinstance(rectangle, Polygon):
        raise BuildingOrientationError(
            "minimum rotated rectangle did not produce a Polygon"
        )
    coordinates = tuple(rectangle.exterior.coords)
    edges: list[tuple[float, float, tuple[float, float], tuple[float, float]]] = []
    for start, end in zip(coordinates, coordinates[1:], strict=False):
        line = LineString((start, end))
        length = float(line.length)
        if length <= 0.0:
            continue
        angle = _axis_angle_degrees(line)
        edges.append((length, angle, start[:2], end[:2]))
    if not edges:
        raise BuildingOrientationError(
            "buildable envelope has no usable principal axis"
        )
    max_length = max(item[0] for item in edges)
    candidates = [
        item
        for item in edges
        if math.isclose(item[0], max_length, rel_tol=0.0, abs_tol=1e-9)
    ]
    _length, angle, _start, _end = min(
        candidates,
        key=lambda item: (round(item[1], 12), item[2], item[3]),
    )

    centroid = geometry.centroid
    half = max_length / 2.0
    radians = math.radians(angle)
    dx = math.cos(radians) * half
    dy = math.sin(radians) * half
    axis = LineString(
        (
            (centroid.x - dx, centroid.y - dy),
            (centroid.x + dx, centroid.y + dy),
        )
    )
    _require_axis_geometry(axis)
    return axis


def _axis_angle_degrees(axis: LineString) -> float:
    coordinates = tuple(axis.coords)
    start = coordinates[0]
    end = coordinates[-1]
    angle = math.degrees(
        math.atan2(end[1] - start[1], end[0] - start[0])
    ) % 180.0
    if math.isclose(angle, 180.0, abs_tol=1e-12):
        return 0.0
    return angle


def _require_axis_geometry(geometry: LineString) -> None:
    if not isinstance(geometry, LineString):
        raise BuildingOrientationError("axis geometry must be a LineString")
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise BuildingOrientationError(
            "axis geometry must be non-empty, valid and 2D"
        )
    coordinates = tuple(geometry.coords)
    if len(coordinates) < 2:
        raise BuildingOrientationError(
            "axis geometry must contain at least two coordinates"
        )
    if not all(
        math.isfinite(value)
        for coordinate in coordinates
        for value in coordinate[:2]
    ):
        raise BuildingOrientationError("axis coordinates must be finite")
    if float(geometry.length) <= 0.0:
        raise BuildingOrientationError("axis length must be positive")


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise BuildingOrientationError(f"invalid {field_name}: {value!r}")


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuildingOrientationError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise BuildingOrientationError(
            f"{field_name} must be a finite non-negative number"
        )
    return number
