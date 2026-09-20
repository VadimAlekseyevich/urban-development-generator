from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from core.urban_generator.buildings.area_metrics import BuildingAreaSubject
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
_CAPACITY_ABS_TOLERANCE = 1e-9
_AREA_ABS_TOLERANCE_M2 = 1e-6


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


def evaluate_infrastructure_feasibility(
    candidate: InfrastructureCandidateGeometry,
    *,
    infrastructure_type: InfrastructureType,
    proposed_capacity: float,
    host_building: BuildingAreaSubject | None = None,
) -> InfrastructureFeasibilityResult:
    """Evaluate one T05 candidate without regenerating site or host geometry."""

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
            "candidate infrastructure type must match InfrastructureType"
        )
    capacity = _require_positive_finite(
        "proposed_capacity",
        proposed_capacity,
    )

    reasons: list[InfrastructureFeasibilityRejectionReason] = []
    if _greater_than_with_tolerance(
        capacity,
        infrastructure_type.capacity,
        abs_tol=_CAPACITY_ABS_TOLERANCE,
    ):
        reasons.append(
            InfrastructureFeasibilityRejectionReason.
            CAPACITY_EXCEEDS_TYPE_CAPACITY
        )

    if candidate.kind is InfrastructureCandidateGeometryKind.SITE:
        if host_building is not None:
            raise InfrastructureFeasibilityError(
                "site candidate must not receive host_building geometry"
            )
        site_area = candidate.site_area_m2
        if site_area is None:
            raise InfrastructureFeasibilityError(
                "site candidate requires explicit site_area_m2"
            )
        if _less_than_with_tolerance(
            site_area,
            infrastructure_type.minimum_site_area_m2,
            abs_tol=_AREA_ABS_TOLERANCE_M2,
        ):
            reasons.append(
                InfrastructureFeasibilityRejectionReason.
                SITE_AREA_BELOW_MINIMUM
            )
    else:
        if host_building is None:
            reasons.append(
                InfrastructureFeasibilityRejectionReason.
                HOST_BUILDING_GEOMETRY_UNAVAILABLE
            )
        else:
            if not isinstance(host_building, BuildingAreaSubject):
                raise InfrastructureFeasibilityError(
                    "host_building must be BuildingAreaSubject or None"
                )
            if host_building.building_id != candidate.host_building_id:
                raise InfrastructureFeasibilityError(
                    "host_building id must match candidate host_building_id"
                )
            if host_building.working_srid != candidate.working_srid:
                raise InfrastructureFeasibilityError(
                    "host_building working_srid must match candidate working_srid"
                )
            host_area = float(host_building.geometry.area)
            if _less_than_with_tolerance(
                host_area,
                infrastructure_type.minimum_site_area_m2,
                abs_tol=_AREA_ABS_TOLERANCE_M2,
            ):
                reasons.append(
                    InfrastructureFeasibilityRejectionReason.
                    HOST_BUILDING_AREA_BELOW_MINIMUM
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


def _greater_than_with_tolerance(
    value: float,
    limit: float,
    *,
    abs_tol: float,
) -> bool:
    return value > limit and not math.isclose(
        value,
        limit,
        rel_tol=1e-12,
        abs_tol=abs_tol,
    )


def _less_than_with_tolerance(
    value: float,
    limit: float,
    *,
    abs_tol: float,
) -> bool:
    return value < limit and not math.isclose(
        value,
        limit,
        rel_tol=1e-12,
        abs_tol=abs_tol,
    )


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
