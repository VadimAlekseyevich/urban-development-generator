from __future__ import annotations

import math
from dataclasses import dataclass
from core.urban_generator.domain import NetworkNodeRef, NetworkPoint
from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.infrastructure.demand import BlockInfrastructureDemand
from core.urban_generator.infrastructure.existing import ExistingInfrastructureFacility
from core.urban_generator.infrastructure.site_geometry import InfrastructureCandidateGeometry


class InfrastructureNetworkSnapError(ValueError):
    """Raised when an S10-T06 network-snap record violates its contract."""


@dataclass(frozen=True, slots=True)
class InfrastructureDemandRef:
    """Stable identity of one block/type infrastructure demand item."""

    block_id: str
    infrastructure_type_code: str

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        _require_id("infrastructure_type_code", self.infrastructure_type_code)

    @classmethod
    def from_demand(cls, value: BlockInfrastructureDemand) -> InfrastructureDemandRef:
        if not isinstance(value, BlockInfrastructureDemand):
            raise InfrastructureNetworkSnapError(
                "value must be BlockInfrastructureDemand"
            )
        return cls(
            block_id=value.block_id,
            infrastructure_type_code=value.infrastructure_type_code,
        )

    @property
    def key(self) -> tuple[str, str]:
        return self.block_id, self.infrastructure_type_code


@dataclass(frozen=True, slots=True)
class ExistingInfrastructureFacilityRef:
    """Stable identity of one fixed facility/type pair."""

    facility_id: str
    infrastructure_type_code: str

    def __post_init__(self) -> None:
        _require_id("facility_id", self.facility_id)
        _require_id("infrastructure_type_code", self.infrastructure_type_code)

    @classmethod
    def from_facility(
        cls,
        value: ExistingInfrastructureFacility,
    ) -> ExistingInfrastructureFacilityRef:
        if not isinstance(value, ExistingInfrastructureFacility):
            raise InfrastructureNetworkSnapError(
                "value must be ExistingInfrastructureFacility"
            )
        return cls(
            facility_id=value.facility_id,
            infrastructure_type_code=value.infrastructure_type_code,
        )

    @property
    def key(self) -> tuple[str, str]:
        return self.facility_id, self.infrastructure_type_code


@dataclass(frozen=True, slots=True)
class InfrastructureCandidateRef:
    """Stable identity of one generated candidate/type pair."""

    candidate_id: str
    infrastructure_type_code: str

    def __post_init__(self) -> None:
        _require_id("candidate_id", self.candidate_id)
        _require_id("infrastructure_type_code", self.infrastructure_type_code)

    @classmethod
    def from_candidate(
        cls,
        value: InfrastructureCandidateGeometry,
    ) -> InfrastructureCandidateRef:
        if not isinstance(value, InfrastructureCandidateGeometry):
            raise InfrastructureNetworkSnapError(
                "value must be InfrastructureCandidateGeometry"
            )
        return cls(
            candidate_id=value.candidate_id,
            infrastructure_type_code=value.infrastructure_type_code,
        )

    @property
    def key(self) -> tuple[str, str]:
        return self.candidate_id, self.infrastructure_type_code


type InfrastructureNetworkSubjectRef = (
    InfrastructureDemandRef
    | ExistingInfrastructureFacilityRef
    | InfrastructureCandidateRef
)


@dataclass(frozen=True, slots=True)
class InfrastructureDemandSnapInput:
    """Resolved metric point for one demand item before graph snapping."""

    ref: InfrastructureDemandRef
    point: NetworkPoint
    working_srid: int

    def __post_init__(self) -> None:
        _validate_input(
            ref=self.ref,
            expected_ref_type=InfrastructureDemandRef,
            point=self.point,
            working_srid=self.working_srid,
        )


@dataclass(frozen=True, slots=True)
class ExistingInfrastructureFacilitySnapInput:
    """Resolved metric point for one fixed facility before graph snapping."""

    ref: ExistingInfrastructureFacilityRef
    point: NetworkPoint
    working_srid: int

    def __post_init__(self) -> None:
        _validate_input(
            ref=self.ref,
            expected_ref_type=ExistingInfrastructureFacilityRef,
            point=self.point,
            working_srid=self.working_srid,
        )


@dataclass(frozen=True, slots=True)
class InfrastructureCandidateSnapInput:
    """Resolved metric anchor for one candidate before graph snapping."""

    ref: InfrastructureCandidateRef
    point: NetworkPoint
    working_srid: int

    def __post_init__(self) -> None:
        _validate_input(
            ref=self.ref,
            expected_ref_type=InfrastructureCandidateRef,
            point=self.point,
            working_srid=self.working_srid,
        )


type InfrastructureNetworkSnapInput = (
    InfrastructureDemandSnapInput
    | ExistingInfrastructureFacilitySnapInput
    | InfrastructureCandidateSnapInput
)


@dataclass(frozen=True, slots=True)
class InfrastructureDemandSnap:
    """Successful graph snap for one demand item."""

    ref: InfrastructureDemandRef
    node: NetworkNodeRef
    distance_m: float

    def __post_init__(self) -> None:
        _validate_output(
            ref=self.ref,
            expected_ref_type=InfrastructureDemandRef,
            node=self.node,
            distance_m=self.distance_m,
        )


@dataclass(frozen=True, slots=True)
class ExistingInfrastructureFacilitySnap:
    """Successful graph snap for one fixed facility."""

    ref: ExistingInfrastructureFacilityRef
    node: NetworkNodeRef
    distance_m: float

    def __post_init__(self) -> None:
        _validate_output(
            ref=self.ref,
            expected_ref_type=ExistingInfrastructureFacilityRef,
            node=self.node,
            distance_m=self.distance_m,
        )


@dataclass(frozen=True, slots=True)
class InfrastructureCandidateSnap:
    """Successful graph snap for one generated candidate."""

    ref: InfrastructureCandidateRef
    node: NetworkNodeRef
    distance_m: float

    def __post_init__(self) -> None:
        _validate_output(
            ref=self.ref,
            expected_ref_type=InfrastructureCandidateRef,
            node=self.node,
            distance_m=self.distance_m,
        )


type InfrastructureNetworkSnap = (
    InfrastructureDemandSnap
    | ExistingInfrastructureFacilitySnap
    | InfrastructureCandidateSnap
)


def _validate_input(
    *,
    ref: object,
    expected_ref_type: type[object],
    point: NetworkPoint,
    working_srid: int,
) -> None:
    if not isinstance(ref, expected_ref_type):
        raise InfrastructureNetworkSnapError(
            f"ref must be {expected_ref_type.__name__}"
        )
    if not isinstance(point, NetworkPoint):
        raise InfrastructureNetworkSnapError("point must be a NetworkPoint")
    try:
        require_working_crs(working_srid)
    except ValueError as exc:
        raise InfrastructureNetworkSnapError(str(exc)) from exc


def _validate_output(
    *,
    ref: object,
    expected_ref_type: type[object],
    node: NetworkNodeRef,
    distance_m: float,
) -> None:
    if not isinstance(ref, expected_ref_type):
        raise InfrastructureNetworkSnapError(
            f"ref must be {expected_ref_type.__name__}"
        )
    if not isinstance(node, NetworkNodeRef):
        raise InfrastructureNetworkSnapError("node must be a NetworkNodeRef")
    if (
        isinstance(distance_m, bool)
        or not isinstance(distance_m, (int, float))
        or not math.isfinite(distance_m)
        or distance_m < 0.0
    ):
        raise InfrastructureNetworkSnapError(
            "distance_m must be a finite non-negative number"
        )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InfrastructureNetworkSnapError(
            f"{field_name} must be a non-empty string"
        )
    if "\n" in value or "\r" in value:
        raise InfrastructureNetworkSnapError(
            f"{field_name} must not contain line breaks"
        )
