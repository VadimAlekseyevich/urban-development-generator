from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from core.urban_generator.domain import require_working_crs
from core.urban_generator.infrastructure.site_geometry import (
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
