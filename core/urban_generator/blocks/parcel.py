from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.zoning.config import ZoneClass

PLANNING_PARCEL_SEMANTICS = "planning_lot_non_cadastral"
MAX_PLANNING_PARCEL_FRONTAGES = 128

_AREA_REL_TOLERANCE = 1e-9
_AREA_ABS_TOLERANCE_M2 = 1e-6
_LENGTH_REL_TOLERANCE = 1e-9
_LENGTH_ABS_TOLERANCE_M = 1e-6


class ParcelDomainError(ValueError):
    """Raised when the S07-T08 planning-parcel contract is violated."""


@dataclass(frozen=True, slots=True)
class ParcelFrontageSegment:
    """One road-backed frontage segment lying on a planning parcel boundary."""

    road_id: str
    geometry: BaseGeometry

    def __post_init__(self) -> None:
        _require_id("road_id", self.road_id)
        _require_linear_geometry("frontage geometry", self.geometry)

    @property
    def length_m(self) -> float:
        return float(self.geometry.length)

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.road_id, self.geometry.wkb_hex)


@dataclass(frozen=True, slots=True)
class PlanningParcel:
    """Non-cadastral planning lot used only for procedural building placement.

    A planning parcel is subordinate to one generated block. It can carry the explicit zone
    relation established upstream by S07-T07, road-backed frontage geometry, and a polygonal
    buildable envelope. It is not a legal/cadastral land record.
    """

    semantics: ClassVar[str] = PLANNING_PARCEL_SEMANTICS

    parcel_id: str
    block_id: str
    working_srid: int
    geometry: Polygon
    buildable_envelope: BaseGeometry
    frontages: tuple[ParcelFrontageSegment, ...] = ()
    zone_id: str | None = None
    zone_class: ZoneClass | None = None

    def __post_init__(self) -> None:
        _require_id("parcel_id", self.parcel_id)
        _require_id("block_id", self.block_id)
        require_working_crs(self.working_srid)
        _require_polygon("parcel geometry", self.geometry)
        _require_polygonal_geometry("buildable envelope", self.buildable_envelope)

        parcel_area_m2 = float(self.geometry.area)
        tolerance_m2 = max(
            _AREA_ABS_TOLERANCE_M2,
            parcel_area_m2 * _AREA_REL_TOLERANCE,
        )
        outside_area_m2 = float(self.buildable_envelope.difference(self.geometry).area)
        if outside_area_m2 > tolerance_m2:
            raise ParcelDomainError(
                "buildable envelope must stay inside the planning parcel"
            )

        if not isinstance(self.frontages, tuple):
            raise ParcelDomainError("frontages must be an immutable tuple")
        if len(self.frontages) > MAX_PLANNING_PARCEL_FRONTAGES:
            raise ParcelDomainError(
                "planning parcel frontage limit exceeded: "
                f"{len(self.frontages)} > {MAX_PLANNING_PARCEL_FRONTAGES}"
            )
        if any(not isinstance(item, ParcelFrontageSegment) for item in self.frontages):
            raise ParcelDomainError(
                "frontages must contain only ParcelFrontageSegment values"
            )
        frontage_keys = tuple(item.sort_key for item in self.frontages)
        if frontage_keys != tuple(sorted(frontage_keys)):
            raise ParcelDomainError("frontages must use canonical deterministic order")
        if len(frontage_keys) != len(set(frontage_keys)):
            raise ParcelDomainError("frontages must be unique")

        parcel_boundary = self.geometry.boundary
        for frontage in self.frontages:
            tolerance_m = max(
                _LENGTH_ABS_TOLERANCE_M,
                frontage.length_m * _LENGTH_REL_TOLERANCE,
            )
            off_boundary_length_m = float(
                frontage.geometry.difference(parcel_boundary).length
            )
            if off_boundary_length_m > tolerance_m:
                raise ParcelDomainError(
                    f"frontage for road {frontage.road_id!r} must lie on parcel boundary"
                )

        if self.zone_id is None and self.zone_class is None:
            return
        if self.zone_id is None or self.zone_class is None:
            raise ParcelDomainError(
                "zone_id and zone_class must either both be set or both be omitted"
            )
        _require_id("zone_id", self.zone_id)
        if not isinstance(self.zone_class, ZoneClass):
            raise ParcelDomainError("zone_class must be a ZoneClass")

    @property
    def area_m2(self) -> float:
        return float(self.geometry.area)

    @property
    def buildable_area_m2(self) -> float:
        return float(self.buildable_envelope.area)

    @property
    def buildable_ratio(self) -> float:
        return min(1.0, self.buildable_area_m2 / self.area_m2)

    @property
    def frontage_length_m(self) -> float:
        return math.fsum(frontage.length_m for frontage in self.frontages)

    @property
    def frontage_road_ids(self) -> tuple[str, ...]:
        return tuple(sorted({frontage.road_id for frontage in self.frontages}))

    @property
    def has_frontage(self) -> bool:
        return bool(self.frontages)

    @property
    def is_zone_associated(self) -> bool:
        return self.zone_id is not None


def _require_polygon(field_name: str, geometry: BaseGeometry) -> None:
    if not isinstance(geometry, Polygon):
        raise ParcelDomainError(f"{field_name} must be a Polygon")
    _require_valid_2d_positive_area(field_name, geometry)


def _require_polygonal_geometry(field_name: str, geometry: BaseGeometry) -> None:
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise ParcelDomainError(f"{field_name} must be Polygon or MultiPolygon")
    _require_valid_2d_positive_area(field_name, geometry)


def _require_linear_geometry(field_name: str, geometry: BaseGeometry) -> None:
    if not isinstance(geometry, (LineString, MultiLineString)):
        raise ParcelDomainError(f"{field_name} must be LineString or MultiLineString")
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise ParcelDomainError(
            f"{field_name} must be non-empty, valid and 2D"
        )
    length_m = float(geometry.length)
    if not math.isfinite(length_m) or length_m <= 0.0:
        raise ParcelDomainError(f"{field_name} must have positive finite length")


def _require_valid_2d_positive_area(field_name: str, geometry: BaseGeometry) -> None:
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise ParcelDomainError(
            f"{field_name} must be non-empty, valid and 2D"
        )
    area_m2 = float(geometry.area)
    if not math.isfinite(area_m2) or area_m2 <= 0.0:
        raise ParcelDomainError(f"{field_name} must have positive finite area")


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ParcelDomainError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise ParcelDomainError(f"{field_name} must not contain line breaks")
