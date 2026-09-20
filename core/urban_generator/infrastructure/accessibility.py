from __future__ import annotations

import math
from dataclasses import dataclass

from core.urban_generator.domain import NetworkNodeRef
from core.urban_generator.infrastructure.network_snap import (
    ExistingInfrastructureFacilityRef,
    InfrastructureCandidateRef,
    InfrastructureDemandRef,
)


class InfrastructureAccessibilityError(ValueError):
    """Raised when an S10-T07 accessibility contract record is invalid."""


type InfrastructureAccessibilityFacilitySiteRef = (
    ExistingInfrastructureFacilityRef | InfrastructureCandidateRef
)


@dataclass(frozen=True, slots=True)
class InfrastructureAccessibilityQuery:
    """One bounded network-distance query between demand and a facility/site."""

    snapshot_id: str
    infrastructure_type_code: str
    demand_ref: InfrastructureDemandRef
    facility_site_ref: InfrastructureAccessibilityFacilitySiteRef
    demand_node: NetworkNodeRef
    facility_site_node: NetworkNodeRef
    max_network_distance_m: float

    def __post_init__(self) -> None:
        max_distance = _validate_common(
            snapshot_id=self.snapshot_id,
            infrastructure_type_code=self.infrastructure_type_code,
            demand_ref=self.demand_ref,
            facility_site_ref=self.facility_site_ref,
            demand_node=self.demand_node,
            facility_site_node=self.facility_site_node,
            max_network_distance_m=self.max_network_distance_m,
        )
        object.__setattr__(self, "max_network_distance_m", max_distance)

    @property
    def key(self) -> tuple[str, str, str, str]:
        family, subject_id = _facility_site_identity(self.facility_site_ref)
        return (
            self.infrastructure_type_code,
            self.demand_ref.block_id,
            family,
            subject_id,
        )


@dataclass(frozen=True, slots=True)
class InfrastructureAccessibilityResult:
    """Successful bounded network-distance result for one accessibility query."""

    snapshot_id: str
    infrastructure_type_code: str
    demand_ref: InfrastructureDemandRef
    facility_site_ref: InfrastructureAccessibilityFacilitySiteRef
    demand_node: NetworkNodeRef
    facility_site_node: NetworkNodeRef
    max_network_distance_m: float
    distance_m: float

    def __post_init__(self) -> None:
        max_distance = _validate_common(
            snapshot_id=self.snapshot_id,
            infrastructure_type_code=self.infrastructure_type_code,
            demand_ref=self.demand_ref,
            facility_site_ref=self.facility_site_ref,
            demand_node=self.demand_node,
            facility_site_node=self.facility_site_node,
            max_network_distance_m=self.max_network_distance_m,
        )
        distance = _require_non_negative_finite("distance_m", self.distance_m)
        if distance > max_distance:
            raise InfrastructureAccessibilityError(
                "distance_m must not exceed max_network_distance_m"
            )
        object.__setattr__(self, "max_network_distance_m", max_distance)
        object.__setattr__(self, "distance_m", distance)

    @property
    def key(self) -> tuple[str, str, str, str]:
        family, subject_id = _facility_site_identity(self.facility_site_ref)
        return (
            self.infrastructure_type_code,
            self.demand_ref.block_id,
            family,
            subject_id,
        )


def _validate_common(
    *,
    snapshot_id: str,
    infrastructure_type_code: str,
    demand_ref: InfrastructureDemandRef,
    facility_site_ref: InfrastructureAccessibilityFacilitySiteRef,
    demand_node: NetworkNodeRef,
    facility_site_node: NetworkNodeRef,
    max_network_distance_m: float,
) -> float:
    _require_id("snapshot_id", snapshot_id)
    _require_id("infrastructure_type_code", infrastructure_type_code)

    if not isinstance(demand_ref, InfrastructureDemandRef):
        raise InfrastructureAccessibilityError(
            "demand_ref must be InfrastructureDemandRef"
        )
    if not isinstance(
        facility_site_ref,
        (ExistingInfrastructureFacilityRef, InfrastructureCandidateRef),
    ):
        raise InfrastructureAccessibilityError(
            "facility_site_ref must be an existing facility or candidate ref"
        )

    if demand_ref.infrastructure_type_code != infrastructure_type_code:
        raise InfrastructureAccessibilityError(
            "demand_ref infrastructure type must match infrastructure_type_code"
        )
    if facility_site_ref.infrastructure_type_code != infrastructure_type_code:
        raise InfrastructureAccessibilityError(
            "facility_site_ref infrastructure type must match infrastructure_type_code"
        )

    if not isinstance(demand_node, NetworkNodeRef):
        raise InfrastructureAccessibilityError(
            "demand_node must be a NetworkNodeRef"
        )
    if not isinstance(facility_site_node, NetworkNodeRef):
        raise InfrastructureAccessibilityError(
            "facility_site_node must be a NetworkNodeRef"
        )

    return _require_positive_finite(
        "max_network_distance_m",
        max_network_distance_m,
    )


def _facility_site_identity(
    ref: InfrastructureAccessibilityFacilitySiteRef,
) -> tuple[str, str]:
    if isinstance(ref, ExistingInfrastructureFacilityRef):
        return "existing_facility", ref.facility_id
    if isinstance(ref, InfrastructureCandidateRef):
        return "candidate_site", ref.candidate_id
    raise InfrastructureAccessibilityError(
        "facility_site_ref must be an existing facility or candidate ref"
    )


def _require_positive_finite(field_name: str, value: float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number <= 0.0:
        raise InfrastructureAccessibilityError(
            f"{field_name} must be a finite positive number"
        )
    return number


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0.0
    ):
        raise InfrastructureAccessibilityError(
            f"{field_name} must be a finite non-negative number"
        )
    return float(value)


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InfrastructureAccessibilityError(
            f"{field_name} must be a non-empty string"
        )
    if "\n" in value or "\r" in value:
        raise InfrastructureAccessibilityError(
            f"{field_name} must not contain line breaks"
        )
