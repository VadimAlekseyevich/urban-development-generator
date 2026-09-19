from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from core.urban_generator.domain import (
    NetworkGraphSnapshot,
    NetworkNodeRef,
    NetworkPoint,
)
from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.infrastructure.demand import BlockInfrastructureDemand
from core.urban_generator.infrastructure.existing import ExistingInfrastructureFacility
from core.urban_generator.infrastructure.site_geometry import InfrastructureCandidateGeometry


MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE = 100_000


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


class InfrastructureNetworkUnsnappedReason(StrEnum):
    """Recoverable reasons why one valid subject has no snapped network node."""

    EMPTY_NETWORK = "empty_network"
    NO_NODE_WITHIN_MAX_DISTANCE = "no_node_within_max_distance"


@dataclass(frozen=True, slots=True)
class InfrastructureDemandUnsnapped:
    ref: InfrastructureDemandRef
    reason: InfrastructureNetworkUnsnappedReason

    def __post_init__(self) -> None:
        _validate_unsnapped(
            ref=self.ref,
            expected_ref_type=InfrastructureDemandRef,
            reason=self.reason,
        )


@dataclass(frozen=True, slots=True)
class ExistingInfrastructureFacilityUnsnapped:
    ref: ExistingInfrastructureFacilityRef
    reason: InfrastructureNetworkUnsnappedReason

    def __post_init__(self) -> None:
        _validate_unsnapped(
            ref=self.ref,
            expected_ref_type=ExistingInfrastructureFacilityRef,
            reason=self.reason,
        )


@dataclass(frozen=True, slots=True)
class InfrastructureCandidateUnsnapped:
    ref: InfrastructureCandidateRef
    reason: InfrastructureNetworkUnsnappedReason

    def __post_init__(self) -> None:
        _validate_unsnapped(
            ref=self.ref,
            expected_ref_type=InfrastructureCandidateRef,
            reason=self.reason,
        )


type InfrastructureNetworkUnsnapped = (
    InfrastructureDemandUnsnapped
    | ExistingInfrastructureFacilityUnsnapped
    | InfrastructureCandidateUnsnapped
)


@dataclass(frozen=True, slots=True)
class InfrastructureNetworkSnapPolicy:
    """Explicit metric tolerance and bounded batch size for infrastructure snapping."""

    max_snap_distance_m: float
    max_batch_size: int = MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE

    def __post_init__(self) -> None:
        distance = _require_non_negative_finite(
            "max_snap_distance_m",
            self.max_snap_distance_m,
        )
        _require_positive_int("max_batch_size", self.max_batch_size)
        if self.max_batch_size > MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE:
            raise InfrastructureNetworkSnapError(
                "max_batch_size exceeds infrastructure snap hard limit: "
                f"{self.max_batch_size} > "
                f"{MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE}"
            )
        object.__setattr__(self, "max_snap_distance_m", distance)


@dataclass(frozen=True, slots=True)
class InfrastructureNetworkSnapDiagnostics:
    input_count: int
    snapped_count: int
    unsnapped_count: int
    empty_network_count: int
    no_node_within_max_distance_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "input_count",
            "snapped_count",
            "unsnapped_count",
            "empty_network_count",
            "no_node_within_max_distance_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if self.snapped_count + self.unsnapped_count != self.input_count:
            raise InfrastructureNetworkSnapError(
                "snapped_count + unsnapped_count must equal input_count"
            )
        if (
            self.empty_network_count
            + self.no_node_within_max_distance_count
            != self.unsnapped_count
        ):
            raise InfrastructureNetworkSnapError(
                "unsnapped reason counts must equal unsnapped_count"
            )


def validate_infrastructure_network_snap_batch(
    inputs: tuple[InfrastructureNetworkSnapInput, ...],
    *,
    snapshot: NetworkGraphSnapshot,
    policy: InfrastructureNetworkSnapPolicy,
) -> None:
    """Validate batch size and exact metric CRS compatibility before backend calls."""

    if not isinstance(inputs, tuple):
        raise InfrastructureNetworkSnapError(
            "inputs must be an immutable tuple"
        )
    if not isinstance(snapshot, NetworkGraphSnapshot):
        raise InfrastructureNetworkSnapError(
            "snapshot must be a NetworkGraphSnapshot"
        )
    if not isinstance(policy, InfrastructureNetworkSnapPolicy):
        raise InfrastructureNetworkSnapError(
            "policy must be InfrastructureNetworkSnapPolicy"
        )
    if len(inputs) > policy.max_batch_size:
        raise InfrastructureNetworkSnapError(
            "snap batch size limit exceeded: "
            f"{len(inputs)} > {policy.max_batch_size}"
        )
    valid_input_types = (
        InfrastructureDemandSnapInput,
        ExistingInfrastructureFacilitySnapInput,
        InfrastructureCandidateSnapInput,
    )
    for index, item in enumerate(inputs):
        if not isinstance(item, valid_input_types):
            raise InfrastructureNetworkSnapError(
                f"inputs[{index}] must be a typed infrastructure snap input"
            )
        if item.working_srid != snapshot.working_crs.srid:
            raise InfrastructureNetworkSnapError(
                "snap input working_srid must match network snapshot: "
                f"{item.working_srid} != {snapshot.working_crs.srid}"
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


def _validate_unsnapped(
    *,
    ref: object,
    expected_ref_type: type[object],
    reason: InfrastructureNetworkUnsnappedReason,
) -> None:
    if not isinstance(ref, expected_ref_type):
        raise InfrastructureNetworkSnapError(
            f"ref must be {expected_ref_type.__name__}"
        )
    if not isinstance(reason, InfrastructureNetworkUnsnappedReason):
        raise InfrastructureNetworkSnapError(
            "reason must be InfrastructureNetworkUnsnappedReason"
        )


def _require_non_negative_finite(
    field_name: str,
    value: float,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0.0
    ):
        raise InfrastructureNetworkSnapError(
            f"{field_name} must be a finite non-negative number"
        )
    return float(value)


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InfrastructureNetworkSnapError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InfrastructureNetworkSnapError(
            f"{field_name} must be a non-negative integer"
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
