from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from core.urban_generator.domain import require_working_crs
from core.urban_generator.infrastructure.config import InfrastructureType
from core.urban_generator.infrastructure.site_geometry import (
    InfrastructureCandidateGeometry,
    InfrastructureCandidateGeometryKind,
)


class InfrastructureFeasibilityError(ValueError):
    """Raised when S10-T09 feasibility data violates its typed contract."""


class InfrastructureFeasibilityRejectionReason(StrEnum):
    """Hard reasons why a proposed infrastructure candidate cannot be accepted."""

    CAPACITY_EXCEEDS_TYPE_CAPACITY = "capacity_exceeds_type_capacity"
    SITE_AREA_BELOW_MINIMUM = "site_area_below_minimum"
    HOST_BUILDING_GEOMETRY_UNAVAILABLE = "host_building_geometry_unavailable"
    HOST_BUILDING_AREA_BELOW_MINIMUM = "host_building_area_below_minimum"


_SITE_ONLY_REASONS = {
    InfrastructureFeasibilityRejectionReason.SITE_AREA_BELOW_MINIMUM,
}
_HOST_BUILDING_ONLY_REASONS = {
    InfrastructureFeasibilityRejectionReason.HOST_BUILDING_GEOMETRY_UNAVAILABLE,
    InfrastructureFeasibilityRejectionReason.HOST_BUILDING_AREA_BELOW_MINIMUM,
}


@dataclass(frozen=True, slots=True)
class InfrastructureFeasibilityResult:
    """One deterministic capacity/site or host-building feasibility decision."""

    candidate_id: str
    infrastructure_type_code: str
    working_srid: int
    geometry_kind: InfrastructureCandidateGeometryKind
    proposed_capacity: float
    is_feasible: bool
    rejection_reasons: tuple[InfrastructureFeasibilityRejectionReason, ...] = ()

    def __post_init__(self) -> None:
        _require_id("candidate_id", self.candidate_id)
        _require_id(
            "infrastructure_type_code",
            self.infrastructure_type_code,
        )
        require_working_crs(self.working_srid)
        if not isinstance(
            self.geometry_kind,
            InfrastructureCandidateGeometryKind,
        ):
            raise InfrastructureFeasibilityError(
                "geometry_kind must be InfrastructureCandidateGeometryKind"
            )
        capacity = _require_positive_finite(
            "proposed_capacity",
            self.proposed_capacity,
        )
        if not isinstance(self.is_feasible, bool):
            raise InfrastructureFeasibilityError(
                "is_feasible must be a bool"
            )
        if not isinstance(self.rejection_reasons, tuple):
            raise InfrastructureFeasibilityError(
                "rejection_reasons must be an immutable tuple"
            )
        if any(
            not isinstance(
                item,
                InfrastructureFeasibilityRejectionReason,
            )
            for item in self.rejection_reasons
        ):
            raise InfrastructureFeasibilityError(
                "rejection_reasons must contain "
                "InfrastructureFeasibilityRejectionReason values"
            )

        canonical_reasons = tuple(
            sorted(set(self.rejection_reasons), key=lambda item: item.value)
        )
        if self.is_feasible != (not canonical_reasons):
            raise InfrastructureFeasibilityError(
                "is_feasible must be true exactly when rejection_reasons is empty"
            )

        reason_set = set(canonical_reasons)
        if (
            self.geometry_kind is InfrastructureCandidateGeometryKind.SITE
            and reason_set & _HOST_BUILDING_ONLY_REASONS
        ):
            raise InfrastructureFeasibilityError(
                "site feasibility result cannot use host-building rejection reasons"
            )
        if (
            self.geometry_kind
            is InfrastructureCandidateGeometryKind.HOST_BUILDING
            and reason_set & _SITE_ONLY_REASONS
        ):
            raise InfrastructureFeasibilityError(
                "host-building feasibility result cannot use site rejection reasons"
            )

        object.__setattr__(self, "proposed_capacity", capacity)
        object.__setattr__(self, "rejection_reasons", canonical_reasons)

    @property
    def key(self) -> tuple[str, str]:
        return self.candidate_id, self.infrastructure_type_code


def validate_infrastructure_candidate_feasibility(
    candidate: InfrastructureCandidateGeometry,
    *,
    infrastructure_type: InfrastructureType,
    proposed_capacity: float,
    host_building_geometry: BaseGeometry | None = None,
    host_building_working_srid: int | None = None,
) -> InfrastructureFeasibilityResult:
    """Validate one prepared T05 candidate without regenerating site geometry."""

    if not isinstance(candidate, InfrastructureCandidateGeometry):
        raise InfrastructureFeasibilityError(
            "candidate must be InfrastructureCandidateGeometry"
        )
    if not isinstance(infrastructure_type, InfrastructureType):
        raise InfrastructureFeasibilityError(
            "infrastructure_type must be InfrastructureType"
        )
    if candidate.infrastructure_type_code != infrastructure_type.code:
        raise InfrastructureFeasibilityError(
            "candidate infrastructure type must match InfrastructureType code"
        )

    capacity = _require_positive_finite(
        "proposed_capacity",
        proposed_capacity,
    )
    reasons: list[InfrastructureFeasibilityRejectionReason] = []
    if capacity > infrastructure_type.capacity and not math.isclose(
        capacity,
        infrastructure_type.capacity,
        rel_tol=1e-12,
        abs_tol=1e-9,
    ):
        reasons.append(
            InfrastructureFeasibilityRejectionReason.CAPACITY_EXCEEDS_TYPE_CAPACITY
        )

    if candidate.kind is InfrastructureCandidateGeometryKind.SITE:
        if host_building_geometry is not None or host_building_working_srid is not None:
            raise InfrastructureFeasibilityError(
                "site candidate must not receive host-building geometry inputs"
            )
        site_geometry = candidate.site_geometry
        site_area_m2 = candidate.site_area_m2
        if site_geometry is None or site_area_m2 is None:
            raise InfrastructureFeasibilityError(
                "site candidate must carry explicit T05 site geometry"
            )
        _require_polygonal_geometry(
            "candidate site_geometry",
            site_geometry,
        )
        if (
            site_area_m2 < infrastructure_type.minimum_site_area_m2
            and not math.isclose(
                site_area_m2,
                infrastructure_type.minimum_site_area_m2,
                rel_tol=1e-12,
                abs_tol=1e-6,
            )
        ):
            reasons.append(
                InfrastructureFeasibilityRejectionReason.SITE_AREA_BELOW_MINIMUM
            )
    else:
        if host_building_geometry is None:
            if host_building_working_srid is not None:
                raise InfrastructureFeasibilityError(
                    "host_building_working_srid requires host_building_geometry"
                )
            reasons.append(
                InfrastructureFeasibilityRejectionReason.HOST_BUILDING_GEOMETRY_UNAVAILABLE
            )
        else:
            if host_building_working_srid is None:
                raise InfrastructureFeasibilityError(
                    "host_building_geometry requires host_building_working_srid"
                )
            require_working_crs(host_building_working_srid)
            if host_building_working_srid != candidate.working_srid:
                raise InfrastructureFeasibilityError(
                    "host-building working SRID must match candidate working SRID"
                )
            host_area_m2 = _require_polygonal_geometry(
                "host_building_geometry",
                host_building_geometry,
            )
            if (
                host_area_m2 < infrastructure_type.minimum_site_area_m2
                and not math.isclose(
                    host_area_m2,
                    infrastructure_type.minimum_site_area_m2,
                    rel_tol=1e-12,
                    abs_tol=1e-6,
                )
            ):
                reasons.append(
                    InfrastructureFeasibilityRejectionReason.HOST_BUILDING_AREA_BELOW_MINIMUM
                )

    return InfrastructureFeasibilityResult(
        candidate_id=candidate.candidate_id,
        infrastructure_type_code=candidate.infrastructure_type_code,
        working_srid=candidate.working_srid,
        geometry_kind=candidate.kind,
        proposed_capacity=capacity,
        is_feasible=not reasons,
        rejection_reasons=tuple(reasons),
    )


def _require_polygonal_geometry(
    field_name: str,
    geometry: BaseGeometry,
) -> float:
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise InfrastructureFeasibilityError(
            f"{field_name} must be Polygon or MultiPolygon"
        )
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise InfrastructureFeasibilityError(
            f"{field_name} must be non-empty, valid and 2D"
        )
    area_m2 = float(geometry.area)
    if not math.isfinite(area_m2) or area_m2 <= 0.0:
        raise InfrastructureFeasibilityError(
            f"{field_name} must have positive finite area"
        )
    return area_m2


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InfrastructureFeasibilityError(
            f"{field_name} must be a non-empty string"
        )
    if "\n" in value or "\r" in value:
        raise InfrastructureFeasibilityError(
            f"{field_name} must not contain line breaks"
        )


def _require_positive_finite(field_name: str, value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0.0
    ):
        raise InfrastructureFeasibilityError(
            f"{field_name} must be a finite positive number"
        )
    return float(value)
