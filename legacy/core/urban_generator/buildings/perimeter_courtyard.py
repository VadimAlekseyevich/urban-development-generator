from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry

from core.urban_generator.buildings.config import BuildingFootprintStrategy
from core.urban_generator.buildings.envelope import (
    BuildingEnvelopeResult,
    BuildingEnvelopeSourceKind,
    BuildingEnvelopeStatus,
)
from core.urban_generator.domain.crs import require_working_crs

_GEOMETRY_EPSILON_M = 1e-6
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")


class PerimeterCourtyardFootprintError(ValueError):
    """Raised when S08-T06 perimeter/courtyard inputs violate the core contract."""


class PerimeterCourtyardKind(StrEnum):
    """Simplified block-scale polygonal building archetypes."""

    PERIMETER = "perimeter"
    COURTYARD = "courtyard"


class PerimeterOpeningSide(StrEnum):
    """Explicit side used to open a perimeter ring to the outside."""

    SOUTH = "south"
    EAST = "east"
    NORTH = "north"
    WEST = "west"


class PerimeterCourtyardFootprintStatus(StrEnum):
    """Deterministic outcome of one simplified polygonal footprint attempt."""

    READY = "READY"
    EMPTY_AFTER_SETBACK = "EMPTY_AFTER_SETBACK"
    NO_COURT_SPACE = "NO_COURT_SPACE"
    MULTIPART_UNSUPPORTED = "MULTIPART_UNSUPPORTED"
    INVALID_OPENING = "INVALID_OPENING"


@dataclass(frozen=True, slots=True)
class PerimeterCourtyardFootprintSpec:
    """Metric geometry policy without any interior room/layout planning."""

    kind: PerimeterCourtyardKind
    edge_setback_m: float
    wing_depth_m: float
    opening_width_m: float | None = None
    opening_side: PerimeterOpeningSide | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PerimeterCourtyardKind):
            raise PerimeterCourtyardFootprintError(
                "kind must be a PerimeterCourtyardKind value"
            )
        setback = _require_non_negative_finite(
            "edge_setback_m",
            self.edge_setback_m,
        )
        depth = _require_positive_finite(
            "wing_depth_m",
            self.wing_depth_m,
        )
        object.__setattr__(self, "edge_setback_m", setback)
        object.__setattr__(self, "wing_depth_m", depth)

        if self.kind is PerimeterCourtyardKind.COURTYARD:
            if self.opening_width_m is not None or self.opening_side is not None:
                raise PerimeterCourtyardFootprintError(
                    "courtyard footprint must not define an opening"
                )
            return

        if self.opening_width_m is None:
            raise PerimeterCourtyardFootprintError(
                "perimeter footprint requires opening_width_m"
            )
        opening_width = _require_positive_finite(
            "opening_width_m",
            self.opening_width_m,
        )
        if not isinstance(self.opening_side, PerimeterOpeningSide):
            raise PerimeterCourtyardFootprintError(
                "perimeter footprint requires a PerimeterOpeningSide"
            )
        object.__setattr__(self, "opening_width_m", opening_width)

    @classmethod
    def courtyard(
        cls,
        *,
        edge_setback_m: float,
        wing_depth_m: float,
    ) -> PerimeterCourtyardFootprintSpec:
        return cls(
            kind=PerimeterCourtyardKind.COURTYARD,
            edge_setback_m=edge_setback_m,
            wing_depth_m=wing_depth_m,
        )

    @classmethod
    def perimeter(
        cls,
        *,
        edge_setback_m: float,
        wing_depth_m: float,
        opening_width_m: float,
        opening_side: PerimeterOpeningSide = PerimeterOpeningSide.SOUTH,
    ) -> PerimeterCourtyardFootprintSpec:
        return cls(
            kind=PerimeterCourtyardKind.PERIMETER,
            edge_setback_m=edge_setback_m,
            wing_depth_m=wing_depth_m,
            opening_width_m=opening_width_m,
            opening_side=opening_side,
        )


@dataclass(frozen=True, slots=True)
class PerimeterCourtyardFootprintResult:
    """Single-polygon proposal or an explicit bounded rejection status."""

    source_id: str
    kind: PerimeterCourtyardKind
    strategy: BuildingFootprintStrategy
    working_srid: int
    status: PerimeterCourtyardFootprintStatus
    proposed_geometry: Polygon | None

    def __post_init__(self) -> None:
        _require_id("source_id", self.source_id)
        if not isinstance(self.kind, PerimeterCourtyardKind):
            raise PerimeterCourtyardFootprintError(
                "kind must be a PerimeterCourtyardKind value"
            )
        expected_strategy = _strategy_for_kind(self.kind)
        if self.strategy is not expected_strategy:
            raise PerimeterCourtyardFootprintError(
                "strategy must match perimeter/courtyard kind"
            )
        require_working_crs(self.working_srid)
        if not isinstance(self.status, PerimeterCourtyardFootprintStatus):
            raise PerimeterCourtyardFootprintError(
                "status must be a PerimeterCourtyardFootprintStatus value"
            )
        if self.status is PerimeterCourtyardFootprintStatus.READY:
            if self.proposed_geometry is None:
                raise PerimeterCourtyardFootprintError(
                    "READY result requires proposed_geometry"
                )
            _require_polygon("proposed_geometry", self.proposed_geometry)
        elif self.proposed_geometry is not None:
            raise PerimeterCourtyardFootprintError(
                "rejected result must not carry proposed_geometry"
            )

    @property
    def is_ready(self) -> bool:
        return self.status is PerimeterCourtyardFootprintStatus.READY

    @property
    def footprint_geometry(self) -> Polygon | None:
        return self.proposed_geometry if self.is_ready else None

    @property
    def area_m2(self) -> float:
        if self.proposed_geometry is None:
            return 0.0
        return float(self.proposed_geometry.area)


class PerimeterCourtyardFootprintStrategy:
    """Create a closed courtyard ring or an opened perimeter ring from a block envelope."""

    def __init__(self, *, working_srid: int) -> None:
        self.working_crs = require_working_crs(working_srid)

    def create(
        self,
        envelope: BuildingEnvelopeResult,
        *,
        spec: PerimeterCourtyardFootprintSpec,
    ) -> PerimeterCourtyardFootprintResult:
        geometry = self._validate_inputs(envelope=envelope, spec=spec)
        strategy = _strategy_for_kind(spec.kind)

        if isinstance(geometry, MultiPolygon):
            return self._rejected(
                envelope=envelope,
                spec=spec,
                strategy=strategy,
                status=PerimeterCourtyardFootprintStatus.MULTIPART_UNSUPPORTED,
            )
        if not isinstance(geometry, Polygon):
            raise PerimeterCourtyardFootprintError(
                "READY envelope geometry must be Polygon or MultiPolygon"
            )

        outer_raw = (
            geometry
            if spec.edge_setback_m == 0.0
            else geometry.buffer(
                -spec.edge_setback_m,
                join_style="mitre",
            )
        )
        if outer_raw.is_empty:
            return self._rejected(
                envelope=envelope,
                spec=spec,
                strategy=strategy,
                status=PerimeterCourtyardFootprintStatus.EMPTY_AFTER_SETBACK,
            )
        if isinstance(outer_raw, MultiPolygon):
            return self._rejected(
                envelope=envelope,
                spec=spec,
                strategy=strategy,
                status=PerimeterCourtyardFootprintStatus.MULTIPART_UNSUPPORTED,
            )
        if not isinstance(outer_raw, Polygon):
            return self._rejected(
                envelope=envelope,
                spec=spec,
                strategy=strategy,
                status=PerimeterCourtyardFootprintStatus.EMPTY_AFTER_SETBACK,
            )
        outer = outer_raw

        inner_raw = outer.buffer(
            -spec.wing_depth_m,
            join_style="mitre",
        )
        if inner_raw.is_empty:
            return self._rejected(
                envelope=envelope,
                spec=spec,
                strategy=strategy,
                status=PerimeterCourtyardFootprintStatus.NO_COURT_SPACE,
            )
        if isinstance(inner_raw, MultiPolygon):
            return self._rejected(
                envelope=envelope,
                spec=spec,
                strategy=strategy,
                status=PerimeterCourtyardFootprintStatus.MULTIPART_UNSUPPORTED,
            )
        if not isinstance(inner_raw, Polygon):
            return self._rejected(
                envelope=envelope,
                spec=spec,
                strategy=strategy,
                status=PerimeterCourtyardFootprintStatus.NO_COURT_SPACE,
            )
        inner = inner_raw

        ring_raw = outer.difference(inner)
        if not isinstance(ring_raw, Polygon):
            return self._rejected(
                envelope=envelope,
                spec=spec,
                strategy=strategy,
                status=PerimeterCourtyardFootprintStatus.MULTIPART_UNSUPPORTED,
            )

        if spec.kind is PerimeterCourtyardKind.COURTYARD:
            if len(ring_raw.interiors) != 1:
                return self._rejected(
                    envelope=envelope,
                    spec=spec,
                    strategy=strategy,
                    status=PerimeterCourtyardFootprintStatus.MULTIPART_UNSUPPORTED,
                )
            _require_polygon("courtyard footprint", ring_raw)
            return PerimeterCourtyardFootprintResult(
                source_id=envelope.source_id,
                kind=spec.kind,
                strategy=strategy,
                working_srid=self.working_crs.srid,
                status=PerimeterCourtyardFootprintStatus.READY,
                proposed_geometry=ring_raw,
            )

        opened = self._open_perimeter(
            ring=ring_raw,
            outer=outer,
            inner=inner,
            spec=spec,
        )
        if opened is None:
            return self._rejected(
                envelope=envelope,
                spec=spec,
                strategy=strategy,
                status=PerimeterCourtyardFootprintStatus.INVALID_OPENING,
            )
        _require_polygon("perimeter footprint", opened)
        return PerimeterCourtyardFootprintResult(
            source_id=envelope.source_id,
            kind=spec.kind,
            strategy=strategy,
            working_srid=self.working_crs.srid,
            status=PerimeterCourtyardFootprintStatus.READY,
            proposed_geometry=opened,
        )

    def _validate_inputs(
        self,
        *,
        envelope: BuildingEnvelopeResult,
        spec: PerimeterCourtyardFootprintSpec,
    ) -> BaseGeometry:
        if not isinstance(envelope, BuildingEnvelopeResult):
            raise PerimeterCourtyardFootprintError(
                "envelope must be a BuildingEnvelopeResult"
            )
        if envelope.working_srid != self.working_crs.srid:
            raise PerimeterCourtyardFootprintError(
                "envelope working_srid must match strategy working CRS"
            )
        if envelope.status is not BuildingEnvelopeStatus.READY:
            raise PerimeterCourtyardFootprintError(
                "perimeter/courtyard footprints require a READY envelope"
            )
        if envelope.source_kind is not BuildingEnvelopeSourceKind.BLOCK:
            raise PerimeterCourtyardFootprintError(
                "perimeter/courtyard footprints require a BLOCK envelope"
            )
        if not isinstance(spec, PerimeterCourtyardFootprintSpec):
            raise PerimeterCourtyardFootprintError(
                "spec must be a PerimeterCourtyardFootprintSpec"
            )
        geometry = envelope.buildable_geometry
        if geometry is None:
            raise PerimeterCourtyardFootprintError(
                "READY envelope must carry buildable geometry"
            )
        return geometry

    def _open_perimeter(
        self,
        *,
        ring: Polygon,
        outer: Polygon,
        inner: Polygon,
        spec: PerimeterCourtyardFootprintSpec,
    ) -> Polygon | None:
        opening_width = spec.opening_width_m
        opening_side = spec.opening_side
        if opening_width is None or opening_side is None:
            raise PerimeterCourtyardFootprintError(
                "perimeter opening metadata is incomplete"
            )

        outer_min_x, outer_min_y, outer_max_x, outer_max_y = outer.bounds
        inner_min_x, inner_min_y, inner_max_x, inner_max_y = inner.bounds
        center_x = float(inner.centroid.x)
        center_y = float(inner.centroid.y)
        half_opening = opening_width / 2.0

        if opening_side is PerimeterOpeningSide.SOUTH:
            gate = box(
                center_x - half_opening,
                outer_min_y - _GEOMETRY_EPSILON_M,
                center_x + half_opening,
                inner_min_y + _GEOMETRY_EPSILON_M,
            )
        elif opening_side is PerimeterOpeningSide.NORTH:
            gate = box(
                center_x - half_opening,
                inner_max_y - _GEOMETRY_EPSILON_M,
                center_x + half_opening,
                outer_max_y + _GEOMETRY_EPSILON_M,
            )
        elif opening_side is PerimeterOpeningSide.WEST:
            gate = box(
                outer_min_x - _GEOMETRY_EPSILON_M,
                center_y - half_opening,
                inner_min_x + _GEOMETRY_EPSILON_M,
                center_y + half_opening,
            )
        else:
            gate = box(
                inner_max_x - _GEOMETRY_EPSILON_M,
                center_y - half_opening,
                outer_max_x + _GEOMETRY_EPSILON_M,
                center_y + half_opening,
            )

        opened_raw = ring.difference(gate)
        if not isinstance(opened_raw, Polygon):
            return None
        if opened_raw.is_empty or not opened_raw.is_valid:
            return None
        if len(opened_raw.interiors) != 0:
            return None
        if not outer.covers(opened_raw):
            return None
        return opened_raw

    def _rejected(
        self,
        *,
        envelope: BuildingEnvelopeResult,
        spec: PerimeterCourtyardFootprintSpec,
        strategy: BuildingFootprintStrategy,
        status: PerimeterCourtyardFootprintStatus,
    ) -> PerimeterCourtyardFootprintResult:
        return PerimeterCourtyardFootprintResult(
            source_id=envelope.source_id,
            kind=spec.kind,
            strategy=strategy,
            working_srid=self.working_crs.srid,
            status=status,
            proposed_geometry=None,
        )


def _strategy_for_kind(
    kind: PerimeterCourtyardKind,
) -> BuildingFootprintStrategy:
    if kind is PerimeterCourtyardKind.PERIMETER:
        return BuildingFootprintStrategy.PERIMETER
    return BuildingFootprintStrategy.COURTYARD


def _require_polygon(field_name: str, geometry: Polygon) -> None:
    if not isinstance(geometry, Polygon):
        raise PerimeterCourtyardFootprintError(
            f"{field_name} must be a Polygon"
        )
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise PerimeterCourtyardFootprintError(
            f"{field_name} must be non-empty, valid and 2D"
        )
    area = float(geometry.area)
    if not math.isfinite(area) or area <= 0.0:
        raise PerimeterCourtyardFootprintError(
            f"{field_name} must have positive finite area"
        )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise PerimeterCourtyardFootprintError(
            f"invalid {field_name}: {value!r}"
        )


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PerimeterCourtyardFootprintError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise PerimeterCourtyardFootprintError(
            f"{field_name} must be a finite non-negative number"
        )
    return number


def _require_positive_finite(field_name: str, value: float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number <= 0.0:
        raise PerimeterCourtyardFootprintError(
            f"{field_name} must be greater than zero"
        )
    return number
