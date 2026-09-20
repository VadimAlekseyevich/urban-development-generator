from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from core.urban_generator.domain import NetworkBackend, NetworkDistanceResult, NetworkNodeRef
from core.urban_generator.infrastructure.config import InfrastructureType
from core.urban_generator.infrastructure.network_snap import (
    MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE,
    ExistingInfrastructureFacilityRef,
    ExistingInfrastructureFacilitySnap,
    ExistingInfrastructureFacilityUnsnapped,
    InfrastructureCandidateRef,
    InfrastructureCandidateSnap,
    InfrastructureCandidateUnsnapped,
    InfrastructureDemandRef,
    InfrastructureDemandSnap,
    InfrastructureDemandUnsnapped,
    InfrastructureNetworkSnapBatchResult,
    InfrastructureNetworkUnsnappedReason,
)

MAX_EXISTING_ACCESSIBILITY_SUBJECTS = MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE
MAX_CANDIDATE_ACCESSIBILITY_CANDIDATES = MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE
MAX_CANDIDATE_ACCESSIBILITY_DEMANDS = MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE
MAX_CANDIDATE_ACCESSIBILITY_DEMAND_BATCH_SIZE = 10_000
MAX_CANDIDATE_ACCESSIBILITY_ROUTING_CALLS = 100_000
MAX_CANDIDATE_ACCESSIBILITY_RESULTS = 1_000_000


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


class InfrastructureAccessibilityMode(StrEnum):
    EXISTING_FACILITY = "existing_facility"
    CANDIDATE_SITE = "candidate_site"


class InfrastructureAccessibilityUnavailableReason(StrEnum):
    DEMAND_UNSNAPPED = "demand_unsnapped"
    FACILITY_SITE_UNSNAPPED = "facility_site_unsnapped"
    BOTH_UNSNAPPED = "both_unsnapped"
    NO_PATH_WITHIN_MAX_DISTANCE = "no_path_within_max_distance"
    NO_SNAPPED_FACILITY_SITE = "no_snapped_facility_site"


@dataclass(frozen=True, slots=True)
class InfrastructureAccessibilityUnavailable:
    """Typed non-numeric accessibility outcome for one demand relationship/search."""

    snapshot_id: str
    infrastructure_type_code: str
    demand_ref: InfrastructureDemandRef
    facility_site_ref: InfrastructureAccessibilityFacilitySiteRef | None
    max_network_distance_m: float
    reason: InfrastructureAccessibilityUnavailableReason
    demand_snap_reason: InfrastructureNetworkUnsnappedReason | None = None
    facility_site_snap_reason: InfrastructureNetworkUnsnappedReason | None = None

    def __post_init__(self) -> None:
        _require_id("snapshot_id", self.snapshot_id)
        _require_id("infrastructure_type_code", self.infrastructure_type_code)
        if not isinstance(self.demand_ref, InfrastructureDemandRef):
            raise InfrastructureAccessibilityError(
                "demand_ref must be InfrastructureDemandRef"
            )
        if self.demand_ref.infrastructure_type_code != self.infrastructure_type_code:
            raise InfrastructureAccessibilityError(
                "demand_ref infrastructure type must match infrastructure_type_code"
            )
        if self.facility_site_ref is not None:
            if not isinstance(
                self.facility_site_ref,
                (ExistingInfrastructureFacilityRef, InfrastructureCandidateRef),
            ):
                raise InfrastructureAccessibilityError(
                    "facility_site_ref must be an existing facility, candidate, or None"
                )
            if (
                self.facility_site_ref.infrastructure_type_code
                != self.infrastructure_type_code
            ):
                raise InfrastructureAccessibilityError(
                    "facility_site_ref infrastructure type must match "
                    "infrastructure_type_code"
                )
        max_distance = _require_positive_finite(
            "max_network_distance_m",
            self.max_network_distance_m,
        )
        if not isinstance(self.reason, InfrastructureAccessibilityUnavailableReason):
            raise InfrastructureAccessibilityError(
                "reason must be InfrastructureAccessibilityUnavailableReason"
            )
        for field_name, value in (
            ("demand_snap_reason", self.demand_snap_reason),
            ("facility_site_snap_reason", self.facility_site_snap_reason),
        ):
            if value is not None and not isinstance(
                value,
                InfrastructureNetworkUnsnappedReason,
            ):
                raise InfrastructureAccessibilityError(
                    f"{field_name} must be InfrastructureNetworkUnsnappedReason or None"
                )

        _validate_unavailable_reason_fields(self)
        object.__setattr__(self, "max_network_distance_m", max_distance)

    @property
    def key(self) -> tuple[str, str, str, str]:
        if self.facility_site_ref is None:
            return (
                self.infrastructure_type_code,
                self.demand_ref.block_id,
                "existing_facility_search",
                "",
            )
        family, subject_id = _facility_site_identity(self.facility_site_ref)
        return (
            self.infrastructure_type_code,
            self.demand_ref.block_id,
            family,
            subject_id,
        )


@dataclass(frozen=True, slots=True)
class InfrastructureAccessibilityDiagnostics:
    subject_count: int
    reachable_count: int
    unavailable_count: int
    demand_unsnapped_count: int
    facility_site_unsnapped_count: int
    both_unsnapped_count: int
    no_path_within_max_distance_count: int
    no_snapped_facility_site_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "subject_count",
            "reachable_count",
            "unavailable_count",
            "demand_unsnapped_count",
            "facility_site_unsnapped_count",
            "both_unsnapped_count",
            "no_path_within_max_distance_count",
            "no_snapped_facility_site_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if self.reachable_count + self.unavailable_count != self.subject_count:
            raise InfrastructureAccessibilityError(
                "reachable_count + unavailable_count must equal subject_count"
            )
        reason_total = (
            self.demand_unsnapped_count
            + self.facility_site_unsnapped_count
            + self.both_unsnapped_count
            + self.no_path_within_max_distance_count
            + self.no_snapped_facility_site_count
        )
        if reason_total != self.unavailable_count:
            raise InfrastructureAccessibilityError(
                "unavailable reason counts must equal unavailable_count"
            )


@dataclass(frozen=True, slots=True)
class InfrastructureAccessibilityBatchResult:
    mode: InfrastructureAccessibilityMode
    snapshot_id: str
    infrastructure_type_code: str
    reachable: tuple[InfrastructureAccessibilityResult, ...]
    unavailable: tuple[InfrastructureAccessibilityUnavailable, ...]
    diagnostics: InfrastructureAccessibilityDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.mode, InfrastructureAccessibilityMode):
            raise InfrastructureAccessibilityError(
                "mode must be InfrastructureAccessibilityMode"
            )
        _require_id("snapshot_id", self.snapshot_id)
        _require_id("infrastructure_type_code", self.infrastructure_type_code)
        if not isinstance(self.reachable, tuple):
            raise InfrastructureAccessibilityError("reachable must be a tuple")
        if not isinstance(self.unavailable, tuple):
            raise InfrastructureAccessibilityError("unavailable must be a tuple")
        if any(
            not isinstance(item, InfrastructureAccessibilityResult)
            for item in self.reachable
        ):
            raise InfrastructureAccessibilityError(
                "reachable must contain InfrastructureAccessibilityResult values"
            )
        if any(
            not isinstance(item, InfrastructureAccessibilityUnavailable)
            for item in self.unavailable
        ):
            raise InfrastructureAccessibilityError(
                "unavailable must contain InfrastructureAccessibilityUnavailable values"
            )
        if not isinstance(self.diagnostics, InfrastructureAccessibilityDiagnostics):
            raise InfrastructureAccessibilityError(
                "diagnostics must be InfrastructureAccessibilityDiagnostics"
            )
        for item in self.reachable:
            if item.snapshot_id != self.snapshot_id:
                raise InfrastructureAccessibilityError(
                    "batch outcome snapshot_id must match batch snapshot_id"
                )
            if item.infrastructure_type_code != self.infrastructure_type_code:
                raise InfrastructureAccessibilityError(
                    "batch outcome infrastructure type must match batch type"
                )
        for unavailable_item in self.unavailable:
            if unavailable_item.snapshot_id != self.snapshot_id:
                raise InfrastructureAccessibilityError(
                    "batch outcome snapshot_id must match batch snapshot_id"
                )
            if (
                unavailable_item.infrastructure_type_code
                != self.infrastructure_type_code
            ):
                raise InfrastructureAccessibilityError(
                    "batch outcome infrastructure type must match batch type"
                )

        if tuple(sorted(self.reachable, key=lambda item: item.key)) != self.reachable:
            raise InfrastructureAccessibilityError(
                "reachable outcomes must be canonically sorted"
            )
        if tuple(sorted(self.unavailable, key=lambda item: item.key)) != self.unavailable:
            raise InfrastructureAccessibilityError(
                "unavailable outcomes must be canonically sorted"
            )
        if self.diagnostics.reachable_count != len(self.reachable):
            raise InfrastructureAccessibilityError(
                "diagnostic reachable_count must match reachable outcomes"
            )
        if self.diagnostics.unavailable_count != len(self.unavailable):
            raise InfrastructureAccessibilityError(
                "diagnostic unavailable_count must match unavailable outcomes"
            )

        identities = [
            _batch_subject_identity(self.mode, item)
            for item in self.reachable
        ]
        identities.extend(
            _batch_subject_identity(self.mode, item)
            for item in self.unavailable
        )
        if len(identities) != len(set(identities)):
            raise InfrastructureAccessibilityError(
                "accessibility batch contains duplicate subject outcomes"
            )

        if self.mode is InfrastructureAccessibilityMode.EXISTING_FACILITY:
            if any(
                not isinstance(item.facility_site_ref, ExistingInfrastructureFacilityRef)
                for item in self.reachable
            ):
                raise InfrastructureAccessibilityError(
                    "existing-facility reachable outcomes must reference existing facilities"
                )
            if any(item.facility_site_ref is not None for item in self.unavailable):
                raise InfrastructureAccessibilityError(
                    "existing-facility unavailable outcomes must be demand-level"
                )
        else:
            if any(
                not isinstance(item.facility_site_ref, InfrastructureCandidateRef)
                for item in self.reachable
            ):
                raise InfrastructureAccessibilityError(
                    "candidate reachable outcomes must reference candidates"
                )
            if any(
                not isinstance(item.facility_site_ref, InfrastructureCandidateRef)
                for item in self.unavailable
            ):
                raise InfrastructureAccessibilityError(
                    "candidate unavailable outcomes must reference candidates"
                )


@dataclass(frozen=True, slots=True)
class InfrastructureCandidateAccessibilityPolicy:
    """Bounds candidate-to-demand routing work and successful result materialization."""

    max_candidates: int = 10_000
    max_demands: int = MAX_CANDIDATE_ACCESSIBILITY_DEMANDS
    demand_batch_size: int = 5_000
    max_routing_calls: int = 10_000
    max_results: int = MAX_CANDIDATE_ACCESSIBILITY_RESULTS

    def __post_init__(self) -> None:
        limits = (
            ("max_candidates", self.max_candidates, MAX_CANDIDATE_ACCESSIBILITY_CANDIDATES),
            ("max_demands", self.max_demands, MAX_CANDIDATE_ACCESSIBILITY_DEMANDS),
            (
                "demand_batch_size",
                self.demand_batch_size,
                MAX_CANDIDATE_ACCESSIBILITY_DEMAND_BATCH_SIZE,
            ),
            (
                "max_routing_calls",
                self.max_routing_calls,
                MAX_CANDIDATE_ACCESSIBILITY_ROUTING_CALLS,
            ),
            ("max_results", self.max_results, MAX_CANDIDATE_ACCESSIBILITY_RESULTS),
        )
        for field_name, value, hard_limit in limits:
            _require_positive_int(field_name, value)
            if value > hard_limit:
                raise InfrastructureAccessibilityError(
                    f"{field_name} exceeds candidate accessibility hard limit: "
                    f"{value} > {hard_limit}"
                )


def compute_existing_facility_accessibility(
    backend: NetworkBackend,
    snap_batch: InfrastructureNetworkSnapBatchResult,
    *,
    infrastructure_type: InfrastructureType,
    max_subjects: int = MAX_EXISTING_ACCESSIBILITY_SUBJECTS,
) -> tuple[InfrastructureAccessibilityResult, ...]:
    """Return nearest reachable existing facility for each snapped demand item."""

    if not isinstance(backend, NetworkBackend):
        raise InfrastructureAccessibilityError("backend must satisfy NetworkBackend")
    if not isinstance(snap_batch, InfrastructureNetworkSnapBatchResult):
        raise InfrastructureAccessibilityError(
            "snap_batch must be InfrastructureNetworkSnapBatchResult"
        )
    if not isinstance(infrastructure_type, InfrastructureType):
        raise InfrastructureAccessibilityError(
            "infrastructure_type must be InfrastructureType"
        )
    if not isinstance(backend, NetworkBackend):
        raise InfrastructureAccessibilityError("backend must satisfy NetworkBackend")
    if not isinstance(snap_batch, InfrastructureNetworkSnapBatchResult):
        raise InfrastructureAccessibilityError(
            "snap_batch must be InfrastructureNetworkSnapBatchResult"
        )
    if not isinstance(infrastructure_type, InfrastructureType):
        raise InfrastructureAccessibilityError(
            "infrastructure_type must be InfrastructureType"
        )
    _require_positive_int("max_subjects", max_subjects)
    if max_subjects > MAX_EXISTING_ACCESSIBILITY_SUBJECTS:
        raise InfrastructureAccessibilityError(
            "max_subjects exceeds existing accessibility hard limit: "
            f"{max_subjects} > {MAX_EXISTING_ACCESSIBILITY_SUBJECTS}"
        )

    snapshot = backend.snapshot
    if snap_batch.snapshot_id != snapshot.snapshot_id:
        raise InfrastructureAccessibilityError(
            "snap batch snapshot_id must match NetworkBackend snapshot"
        )
    if snap_batch.working_srid != snapshot.working_crs.srid:
        raise InfrastructureAccessibilityError(
            "snap batch working_srid must match NetworkBackend snapshot"
        )

    type_code = infrastructure_type.code
    demands = tuple(
        item
        for item in snap_batch.snapped
        if isinstance(item, InfrastructureDemandSnap)
        and item.ref.infrastructure_type_code == type_code
    )
    facilities = tuple(
        item
        for item in snap_batch.snapped
        if isinstance(item, ExistingInfrastructureFacilitySnap)
        and item.ref.infrastructure_type_code == type_code
    )

    subject_count = len(demands) + len(facilities)
    if subject_count > max_subjects:
        raise InfrastructureAccessibilityError(
            "existing accessibility subject limit exceeded: "
            f"{subject_count} > {max_subjects}"
        )
    if not demands or not facilities:
        return ()

    facility_by_node: dict[str, ExistingInfrastructureFacilitySnap] = {}
    for facility in sorted(facilities, key=lambda item: item.ref.key):
        facility_by_node.setdefault(facility.node.node_id, facility)

    demands_by_node: dict[str, list[InfrastructureDemandSnap]] = {}
    for demand in sorted(demands, key=lambda item: item.ref.key):
        demands_by_node.setdefault(demand.node.node_id, []).append(demand)

    sources = tuple(
        facility_by_node[node_id].node
        for node_id in sorted(facility_by_node)
    )
    targets = tuple(
        demands_by_node[node_id][0].node
        for node_id in sorted(demands_by_node)
    )

    distances = backend.multi_source_distances(
        sources,
        targets,
        max_distance_m=infrastructure_type.max_network_distance_m,
    )
    if not isinstance(distances, tuple):
        raise InfrastructureAccessibilityError(
            "NetworkBackend.multi_source_distances must return a tuple"
        )

    results: list[InfrastructureAccessibilityResult] = []
    seen_targets: set[str] = set()
    for distance in distances:
        if not isinstance(distance, NetworkDistanceResult):
            raise InfrastructureAccessibilityError(
                "NetworkBackend.multi_source_distances returned an invalid result"
            )
        source_id = distance.source.node_id
        target_id = distance.target.node_id
        source_facility = facility_by_node.get(source_id)
        target_demands = demands_by_node.get(target_id)
        if source_facility is None:
            raise InfrastructureAccessibilityError(
                "NetworkBackend returned a source outside the requested facility nodes"
            )
        if target_demands is None:
            raise InfrastructureAccessibilityError(
                "NetworkBackend returned a target outside the requested demand nodes"
            )
        if target_id in seen_targets:
            raise InfrastructureAccessibilityError(
                "NetworkBackend returned duplicate results for one demand node"
            )
        seen_targets.add(target_id)

        for demand in target_demands:
            results.append(
                InfrastructureAccessibilityResult(
                    snapshot_id=snapshot.snapshot_id,
                    infrastructure_type_code=type_code,
                    demand_ref=demand.ref,
                    facility_site_ref=source_facility.ref,
                    demand_node=demand.node,
                    facility_site_node=source_facility.node,
                    max_network_distance_m=(
                        infrastructure_type.max_network_distance_m
                    ),
                    distance_m=distance.distance_m,
                )
            )

    return tuple(sorted(results, key=lambda item: item.key))


def compute_candidate_site_accessibility(
    backend: NetworkBackend,
    snap_batch: InfrastructureNetworkSnapBatchResult,
    *,
    infrastructure_type: InfrastructureType,
    policy: InfrastructureCandidateAccessibilityPolicy | None = None,
) -> tuple[InfrastructureAccessibilityResult, ...]:
    """Return bounded candidate-to-demand accessibility rows in deterministic batches."""

    if policy is None:
        policy = InfrastructureCandidateAccessibilityPolicy()
    if not isinstance(backend, NetworkBackend):
        raise InfrastructureAccessibilityError("backend must satisfy NetworkBackend")
    if not isinstance(snap_batch, InfrastructureNetworkSnapBatchResult):
        raise InfrastructureAccessibilityError(
            "snap_batch must be InfrastructureNetworkSnapBatchResult"
        )
    if not isinstance(infrastructure_type, InfrastructureType):
        raise InfrastructureAccessibilityError(
            "infrastructure_type must be InfrastructureType"
        )
    if not isinstance(policy, InfrastructureCandidateAccessibilityPolicy):
        raise InfrastructureAccessibilityError(
            "policy must be InfrastructureCandidateAccessibilityPolicy"
        )

    snapshot = backend.snapshot
    if snap_batch.snapshot_id != snapshot.snapshot_id:
        raise InfrastructureAccessibilityError(
            "snap batch snapshot_id must match NetworkBackend snapshot"
        )
    if snap_batch.working_srid != snapshot.working_crs.srid:
        raise InfrastructureAccessibilityError(
            "snap batch working_srid must match NetworkBackend snapshot"
        )

    type_code = infrastructure_type.code
    demands = tuple(
        item
        for item in snap_batch.snapped
        if isinstance(item, InfrastructureDemandSnap)
        and item.ref.infrastructure_type_code == type_code
    )
    candidates = tuple(
        item
        for item in snap_batch.snapped
        if isinstance(item, InfrastructureCandidateSnap)
        and item.ref.infrastructure_type_code == type_code
    )

    if len(demands) > policy.max_demands:
        raise InfrastructureAccessibilityError(
            "candidate accessibility demand limit exceeded: "
            f"{len(demands)} > {policy.max_demands}"
        )
    if len(candidates) > policy.max_candidates:
        raise InfrastructureAccessibilityError(
            "candidate accessibility candidate limit exceeded: "
            f"{len(candidates)} > {policy.max_candidates}"
        )
    if not demands or not candidates:
        return ()

    possible_results = len(demands) * len(candidates)
    if possible_results > policy.max_results:
        raise InfrastructureAccessibilityError(
            "candidate accessibility result budget exceeded before routing: "
            f"{possible_results} > {policy.max_results}"
        )

    demands_by_node: dict[str, list[InfrastructureDemandSnap]] = {}
    for demand in sorted(demands, key=lambda item: item.ref.key):
        demands_by_node.setdefault(demand.node.node_id, []).append(demand)

    candidates_by_node: dict[str, list[InfrastructureCandidateSnap]] = {}
    for candidate in sorted(candidates, key=lambda item: item.ref.key):
        candidates_by_node.setdefault(candidate.node.node_id, []).append(candidate)

    target_nodes = tuple(
        demands_by_node[node_id][0].node
        for node_id in sorted(demands_by_node)
    )
    target_batches = tuple(
        target_nodes[index : index + policy.demand_batch_size]
        for index in range(0, len(target_nodes), policy.demand_batch_size)
    )
    projected_calls = len(candidates_by_node) * len(target_batches)
    if projected_calls > policy.max_routing_calls:
        raise InfrastructureAccessibilityError(
            "candidate accessibility routing-call budget exceeded before routing: "
            f"{projected_calls} > {policy.max_routing_calls}"
        )

    results: list[InfrastructureAccessibilityResult] = []
    for candidate_node_id in sorted(candidates_by_node):
        node_candidates = candidates_by_node[candidate_node_id]
        source = node_candidates[0].node

        for target_batch in target_batches:
            batch_target_ids = {target.node_id for target in target_batch}
            distances = backend.multi_source_distances(
                (source,),
                target_batch,
                max_distance_m=infrastructure_type.max_network_distance_m,
            )
            if not isinstance(distances, tuple):
                raise InfrastructureAccessibilityError(
                    "NetworkBackend.multi_source_distances must return a tuple"
                )

            seen_targets: set[str] = set()
            for distance in distances:
                if not isinstance(distance, NetworkDistanceResult):
                    raise InfrastructureAccessibilityError(
                        "NetworkBackend.multi_source_distances returned an invalid result"
                    )
                if distance.source.node_id != candidate_node_id:
                    raise InfrastructureAccessibilityError(
                        "NetworkBackend returned a source outside the requested candidate node"
                    )
                target_id = distance.target.node_id
                if target_id not in batch_target_ids:
                    raise InfrastructureAccessibilityError(
                        "NetworkBackend returned a target outside the requested demand batch"
                    )
                if target_id in seen_targets:
                    raise InfrastructureAccessibilityError(
                        "NetworkBackend returned duplicate results for one demand node"
                    )
                seen_targets.add(target_id)

                target_demands = demands_by_node[target_id]
                for candidate in node_candidates:
                    for demand in target_demands:
                        results.append(
                            InfrastructureAccessibilityResult(
                                snapshot_id=snapshot.snapshot_id,
                                infrastructure_type_code=type_code,
                                demand_ref=demand.ref,
                                facility_site_ref=candidate.ref,
                                demand_node=demand.node,
                                facility_site_node=candidate.node,
                                max_network_distance_m=(
                                    infrastructure_type.max_network_distance_m
                                ),
                                distance_m=distance.distance_m,
                            )
                        )

    if len(results) > policy.max_results:
        raise InfrastructureAccessibilityError(
            "candidate accessibility successful result budget exceeded"
        )
    return tuple(sorted(results, key=lambda item: item.key))


def compute_existing_facility_accessibility_batch(
    backend: NetworkBackend,
    snap_batch: InfrastructureNetworkSnapBatchResult,
    *,
    infrastructure_type: InfrastructureType,
    max_subjects: int = MAX_EXISTING_ACCESSIBILITY_SUBJECTS,
) -> InfrastructureAccessibilityBatchResult:
    """Return reachable and explicit unavailable outcomes for existing-facility service."""

    _require_positive_int("max_subjects", max_subjects)
    if max_subjects > MAX_EXISTING_ACCESSIBILITY_SUBJECTS:
        raise InfrastructureAccessibilityError(
            "max_subjects exceeds existing accessibility hard limit: "
            f"{max_subjects} > {MAX_EXISTING_ACCESSIBILITY_SUBJECTS}"
        )

    type_code = infrastructure_type.code
    demand_snaps = {
        item.ref.key: item
        for item in snap_batch.snapped
        if isinstance(item, InfrastructureDemandSnap)
        and item.ref.infrastructure_type_code == type_code
    }
    demand_unsnapped = {
        item.ref.key: item
        for item in snap_batch.unsnapped
        if isinstance(item, InfrastructureDemandUnsnapped)
        and item.ref.infrastructure_type_code == type_code
    }
    facility_snaps = tuple(
        item
        for item in snap_batch.snapped
        if isinstance(item, ExistingInfrastructureFacilitySnap)
        and item.ref.infrastructure_type_code == type_code
    )
    facility_unsnapped = tuple(
        item
        for item in snap_batch.unsnapped
        if isinstance(item, ExistingInfrastructureFacilityUnsnapped)
        and item.ref.infrastructure_type_code == type_code
    )

    demand_keys = tuple(sorted((*demand_snaps.keys(), *demand_unsnapped.keys())))
    subject_input_count = (
        len(demand_keys) + len(facility_snaps) + len(facility_unsnapped)
    )
    if subject_input_count > max_subjects:
        raise InfrastructureAccessibilityError(
            "existing accessibility subject limit exceeded: "
            f"{subject_input_count} > {max_subjects}"
        )

    reachable = compute_existing_facility_accessibility(
        backend,
        snap_batch,
        infrastructure_type=infrastructure_type,
        max_subjects=max_subjects,
    )
    reachable_by_demand = {item.demand_ref.key: item for item in reachable}

    unavailable: list[InfrastructureAccessibilityUnavailable] = []
    for demand_key in demand_keys:
        if demand_key in reachable_by_demand:
            continue
        unsnapped = demand_unsnapped.get(demand_key)
        demand_ref = (
            unsnapped.ref
            if unsnapped is not None
            else demand_snaps[demand_key].ref
        )
        if unsnapped is not None:
            reason = InfrastructureAccessibilityUnavailableReason.DEMAND_UNSNAPPED
            demand_snap_reason = unsnapped.reason
        elif not facility_snaps:
            reason = (
                InfrastructureAccessibilityUnavailableReason.NO_SNAPPED_FACILITY_SITE
            )
            demand_snap_reason = None
        else:
            reason = (
                InfrastructureAccessibilityUnavailableReason.NO_PATH_WITHIN_MAX_DISTANCE
            )
            demand_snap_reason = None

        unavailable.append(
            InfrastructureAccessibilityUnavailable(
                snapshot_id=snap_batch.snapshot_id,
                infrastructure_type_code=type_code,
                demand_ref=demand_ref,
                facility_site_ref=None,
                max_network_distance_m=infrastructure_type.max_network_distance_m,
                reason=reason,
                demand_snap_reason=demand_snap_reason,
            )
        )

    unavailable_result = tuple(sorted(unavailable, key=lambda item: item.key))
    return InfrastructureAccessibilityBatchResult(
        mode=InfrastructureAccessibilityMode.EXISTING_FACILITY,
        snapshot_id=snap_batch.snapshot_id,
        infrastructure_type_code=type_code,
        reachable=reachable,
        unavailable=unavailable_result,
        diagnostics=_make_accessibility_diagnostics(
            subject_count=len(demand_keys),
            reachable_count=len(reachable),
            unavailable=unavailable_result,
        ),
    )


def compute_candidate_site_accessibility_batch(
    backend: NetworkBackend,
    snap_batch: InfrastructureNetworkSnapBatchResult,
    *,
    infrastructure_type: InfrastructureType,
    policy: InfrastructureCandidateAccessibilityPolicy | None = None,
) -> InfrastructureAccessibilityBatchResult:
    """Return one explicit reachable/unavailable outcome per bounded candidate-demand pair."""

    if policy is None:
        policy = InfrastructureCandidateAccessibilityPolicy()
    if not isinstance(backend, NetworkBackend):
        raise InfrastructureAccessibilityError("backend must satisfy NetworkBackend")
    if not isinstance(snap_batch, InfrastructureNetworkSnapBatchResult):
        raise InfrastructureAccessibilityError(
            "snap_batch must be InfrastructureNetworkSnapBatchResult"
        )
    if not isinstance(infrastructure_type, InfrastructureType):
        raise InfrastructureAccessibilityError(
            "infrastructure_type must be InfrastructureType"
        )
    if not isinstance(policy, InfrastructureCandidateAccessibilityPolicy):
        raise InfrastructureAccessibilityError(
            "policy must be InfrastructureCandidateAccessibilityPolicy"
        )

    type_code = infrastructure_type.code
    demand_snaps = {
        item.ref.key: item
        for item in snap_batch.snapped
        if isinstance(item, InfrastructureDemandSnap)
        and item.ref.infrastructure_type_code == type_code
    }
    demand_unsnapped = {
        item.ref.key: item
        for item in snap_batch.unsnapped
        if isinstance(item, InfrastructureDemandUnsnapped)
        and item.ref.infrastructure_type_code == type_code
    }
    candidate_snaps = {
        item.ref.key: item
        for item in snap_batch.snapped
        if isinstance(item, InfrastructureCandidateSnap)
        and item.ref.infrastructure_type_code == type_code
    }
    candidate_unsnapped = {
        item.ref.key: item
        for item in snap_batch.unsnapped
        if isinstance(item, InfrastructureCandidateUnsnapped)
        and item.ref.infrastructure_type_code == type_code
    }

    demand_keys = tuple(sorted((*demand_snaps.keys(), *demand_unsnapped.keys())))
    candidate_keys = tuple(
        sorted((*candidate_snaps.keys(), *candidate_unsnapped.keys()))
    )
    if len(demand_keys) > policy.max_demands:
        raise InfrastructureAccessibilityError(
            "candidate accessibility demand limit exceeded: "
            f"{len(demand_keys)} > {policy.max_demands}"
        )
    if len(candidate_keys) > policy.max_candidates:
        raise InfrastructureAccessibilityError(
            "candidate accessibility candidate limit exceeded: "
            f"{len(candidate_keys)} > {policy.max_candidates}"
        )
    subject_count = len(demand_keys) * len(candidate_keys)
    if subject_count > policy.max_results:
        raise InfrastructureAccessibilityError(
            "candidate accessibility result budget exceeded before routing: "
            f"{subject_count} > {policy.max_results}"
        )

    reachable = compute_candidate_site_accessibility(
        backend,
        snap_batch,
        infrastructure_type=infrastructure_type,
        policy=policy,
    )
    reachable_keys = {item.key for item in reachable}

    unavailable: list[InfrastructureAccessibilityUnavailable] = []
    for demand_key in demand_keys:
        demand_unsnapped_item = demand_unsnapped.get(demand_key)
        demand_ref = (
            demand_unsnapped_item.ref
            if demand_unsnapped_item is not None
            else demand_snaps[demand_key].ref
        )
        for candidate_key in candidate_keys:
            candidate_unsnapped_item = candidate_unsnapped.get(candidate_key)
            candidate_ref = (
                candidate_unsnapped_item.ref
                if candidate_unsnapped_item is not None
                else candidate_snaps[candidate_key].ref
            )
            outcome_key = (
                type_code,
                demand_ref.block_id,
                "candidate_site",
                candidate_ref.candidate_id,
            )
            if outcome_key in reachable_keys:
                continue

            if (
                demand_unsnapped_item is not None
                and candidate_unsnapped_item is not None
            ):
                reason = InfrastructureAccessibilityUnavailableReason.BOTH_UNSNAPPED
            elif demand_unsnapped_item is not None:
                reason = InfrastructureAccessibilityUnavailableReason.DEMAND_UNSNAPPED
            elif candidate_unsnapped_item is not None:
                reason = (
                    InfrastructureAccessibilityUnavailableReason.FACILITY_SITE_UNSNAPPED
                )
            else:
                reason = (
                    InfrastructureAccessibilityUnavailableReason.NO_PATH_WITHIN_MAX_DISTANCE
                )

            unavailable.append(
                InfrastructureAccessibilityUnavailable(
                    snapshot_id=snap_batch.snapshot_id,
                    infrastructure_type_code=type_code,
                    demand_ref=demand_ref,
                    facility_site_ref=candidate_ref,
                    max_network_distance_m=(
                        infrastructure_type.max_network_distance_m
                    ),
                    reason=reason,
                    demand_snap_reason=(
                        demand_unsnapped_item.reason
                        if demand_unsnapped_item is not None
                        else None
                    ),
                    facility_site_snap_reason=(
                        candidate_unsnapped_item.reason
                        if candidate_unsnapped_item is not None
                        else None
                    ),
                )
            )

    unavailable_result = tuple(sorted(unavailable, key=lambda item: item.key))
    return InfrastructureAccessibilityBatchResult(
        mode=InfrastructureAccessibilityMode.CANDIDATE_SITE,
        snapshot_id=snap_batch.snapshot_id,
        infrastructure_type_code=type_code,
        reachable=reachable,
        unavailable=unavailable_result,
        diagnostics=_make_accessibility_diagnostics(
            subject_count=subject_count,
            reachable_count=len(reachable),
            unavailable=unavailable_result,
        ),
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


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InfrastructureAccessibilityError(
            f"{field_name} must be a positive integer"
        )


def _validate_unavailable_reason_fields(
    value: InfrastructureAccessibilityUnavailable,
) -> None:
    reason = value.reason
    demand_reason = value.demand_snap_reason
    facility_reason = value.facility_site_snap_reason
    facility_ref = value.facility_site_ref

    if reason is InfrastructureAccessibilityUnavailableReason.DEMAND_UNSNAPPED:
        if demand_reason is None or facility_reason is not None:
            raise InfrastructureAccessibilityError(
                "demand_unsnapped requires only demand_snap_reason"
            )
    elif reason is InfrastructureAccessibilityUnavailableReason.FACILITY_SITE_UNSNAPPED:
        if facility_ref is None or facility_reason is None or demand_reason is not None:
            raise InfrastructureAccessibilityError(
                "facility_site_unsnapped requires a facility/site ref and "
                "only facility_site_snap_reason"
            )
    elif reason is InfrastructureAccessibilityUnavailableReason.BOTH_UNSNAPPED:
        if facility_ref is None or demand_reason is None or facility_reason is None:
            raise InfrastructureAccessibilityError(
                "both_unsnapped requires both snap reasons and a facility/site ref"
            )
    elif reason is InfrastructureAccessibilityUnavailableReason.NO_PATH_WITHIN_MAX_DISTANCE:
        if demand_reason is not None or facility_reason is not None:
            raise InfrastructureAccessibilityError(
                "no_path_within_max_distance must not carry snap reasons"
            )
    elif reason is InfrastructureAccessibilityUnavailableReason.NO_SNAPPED_FACILITY_SITE:
        if (
            facility_ref is not None
            or demand_reason is not None
            or facility_reason is not None
        ):
            raise InfrastructureAccessibilityError(
                "no_snapped_facility_site is a demand-level outcome without snap reasons"
            )


def _batch_subject_identity(
    mode: InfrastructureAccessibilityMode,
    item: InfrastructureAccessibilityResult | InfrastructureAccessibilityUnavailable,
) -> tuple[str, ...]:
    if mode is InfrastructureAccessibilityMode.EXISTING_FACILITY:
        return (
            item.infrastructure_type_code,
            item.demand_ref.block_id,
            "existing_facility_search",
        )
    facility_ref = item.facility_site_ref
    if not isinstance(facility_ref, InfrastructureCandidateRef):
        raise InfrastructureAccessibilityError(
            "candidate batch outcome must reference an InfrastructureCandidateRef"
        )
    return (
        item.infrastructure_type_code,
        item.demand_ref.block_id,
        "candidate_site",
        facility_ref.candidate_id,
    )


def _make_accessibility_diagnostics(
    *,
    subject_count: int,
    reachable_count: int,
    unavailable: tuple[InfrastructureAccessibilityUnavailable, ...],
) -> InfrastructureAccessibilityDiagnostics:
    counts = {
        reason: 0 for reason in InfrastructureAccessibilityUnavailableReason
    }
    for item in unavailable:
        counts[item.reason] += 1
    return InfrastructureAccessibilityDiagnostics(
        subject_count=subject_count,
        reachable_count=reachable_count,
        unavailable_count=len(unavailable),
        demand_unsnapped_count=counts[
            InfrastructureAccessibilityUnavailableReason.DEMAND_UNSNAPPED
        ],
        facility_site_unsnapped_count=counts[
            InfrastructureAccessibilityUnavailableReason.FACILITY_SITE_UNSNAPPED
        ],
        both_unsnapped_count=counts[
            InfrastructureAccessibilityUnavailableReason.BOTH_UNSNAPPED
        ],
        no_path_within_max_distance_count=counts[
            InfrastructureAccessibilityUnavailableReason.NO_PATH_WITHIN_MAX_DISTANCE
        ],
        no_snapped_facility_site_count=counts[
            InfrastructureAccessibilityUnavailableReason.NO_SNAPPED_FACILITY_SITE
        ],
    )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InfrastructureAccessibilityError(
            f"{field_name} must be a non-negative integer"
        )
