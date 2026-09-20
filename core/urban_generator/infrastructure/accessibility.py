from __future__ import annotations

import math
from dataclasses import dataclass

from core.urban_generator.domain import NetworkBackend, NetworkDistanceResult, NetworkNodeRef
from core.urban_generator.infrastructure.config import InfrastructureType
from core.urban_generator.infrastructure.network_snap import (
    MAX_INFRASTRUCTURE_SNAP_BATCH_SIZE,
    ExistingInfrastructureFacilityRef,
    ExistingInfrastructureFacilitySnap,
    InfrastructureCandidateRef,
    InfrastructureCandidateSnap,
    InfrastructureDemandRef,
    InfrastructureDemandSnap,
    InfrastructureNetworkSnapBatchResult,
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
