from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from core.urban_generator.infrastructure.accessibility import (
    MAX_CANDIDATE_ACCESSIBILITY_CANDIDATES,
    MAX_CANDIDATE_ACCESSIBILITY_DEMANDS,
    MAX_CANDIDATE_ACCESSIBILITY_RESULTS,
    InfrastructureAccessibilityBatchResult,
    InfrastructureAccessibilityMode,
    InfrastructureAccessibilityResult,
    InfrastructureAccessibilityUnavailable,
)
from core.urban_generator.infrastructure.config import InfrastructureType
from core.urban_generator.infrastructure.demand import BlockInfrastructureDemand
from core.urban_generator.infrastructure.feasibility import (
    InfrastructureFeasibilityResult,
)
from core.urban_generator.infrastructure.network_snap import (
    InfrastructureCandidateRef,
    InfrastructureDemandRef,
)

MAX_INFRASTRUCTURE_PLACEMENT_CANDIDATES = MAX_CANDIDATE_ACCESSIBILITY_CANDIDATES
MAX_INFRASTRUCTURE_PLACEMENT_DEMANDS = MAX_CANDIDATE_ACCESSIBILITY_DEMANDS
MAX_INFRASTRUCTURE_PLACEMENT_COVERAGE_ROWS = MAX_CANDIDATE_ACCESSIBILITY_RESULTS
MAX_INFRASTRUCTURE_PLACEMENT_FACILITIES = MAX_INFRASTRUCTURE_PLACEMENT_CANDIDATES
MAX_INFRASTRUCTURE_PLACEMENT_ITERATIONS = MAX_INFRASTRUCTURE_PLACEMENT_CANDIDATES


class InfrastructurePlacementError(ValueError):
    """Raised when S10-T08 greedy placement state violates its contract."""


@dataclass(frozen=True, slots=True)
class InfrastructurePlacementDemandState:
    """Immutable demand amount tracked by one greedy placement state version."""

    demand_ref: InfrastructureDemandRef
    initial_demand: float
    remaining_demand: float

    def __post_init__(self) -> None:
        if not isinstance(self.demand_ref, InfrastructureDemandRef):
            raise InfrastructurePlacementError(
                "demand_ref must be InfrastructureDemandRef"
            )
        initial = _require_non_negative_finite("initial_demand", self.initial_demand)
        remaining = _require_non_negative_finite(
            "remaining_demand",
            self.remaining_demand,
        )
        if remaining > initial and not math.isclose(
            remaining,
            initial,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise InfrastructurePlacementError(
                "remaining_demand cannot exceed initial_demand"
            )
        object.__setattr__(self, "initial_demand", initial)
        object.__setattr__(self, "remaining_demand", min(remaining, initial))

    @property
    def key(self) -> tuple[str, str]:
        return self.demand_ref.key


@dataclass(frozen=True, slots=True)
class InfrastructureCandidateBenefit:
    """Incremental coverable demand for one unaccepted candidate."""

    candidate_ref: InfrastructureCandidateRef
    reachable_remaining_demand: float
    capacity: float
    benefit: float

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_ref, InfrastructureCandidateRef):
            raise InfrastructurePlacementError(
                "candidate_ref must be InfrastructureCandidateRef"
            )
        reachable = _require_non_negative_finite(
            "reachable_remaining_demand",
            self.reachable_remaining_demand,
        )
        capacity = _require_positive_finite("capacity", self.capacity)
        benefit = _require_non_negative_finite("benefit", self.benefit)
        expected = min(reachable, capacity)
        if not math.isclose(
            benefit,
            expected,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise InfrastructurePlacementError(
                "benefit must equal min(reachable_remaining_demand, capacity)"
            )
        object.__setattr__(self, "reachable_remaining_demand", reachable)
        object.__setattr__(self, "capacity", capacity)
        object.__setattr__(self, "benefit", expected)

    @property
    def key(self) -> tuple[str, str]:
        return self.candidate_ref.key


class InfrastructurePlacementSelectionStatus(StrEnum):
    SELECTED = "selected"
    FACILITY_LIMIT_REACHED = "facility_limit_reached"
    ITERATION_LIMIT_REACHED = "iteration_limit_reached"
    NO_CANDIDATES = "no_candidates"
    NO_FEASIBLE_CANDIDATE = "no_feasible_candidate"
    NO_POSITIVE_BENEFIT = "no_positive_benefit"


@dataclass(frozen=True, slots=True)
class InfrastructureGreedyPlacementPolicy:
    """Explicit hard bounds for greedy facility selection."""

    max_facilities: int = 10_000
    max_iterations: int = 10_000

    def __post_init__(self) -> None:
        _require_positive_int("max_facilities", self.max_facilities)
        _require_positive_int("max_iterations", self.max_iterations)
        if self.max_facilities > MAX_INFRASTRUCTURE_PLACEMENT_FACILITIES:
            raise InfrastructurePlacementError(
                "max_facilities exceeds placement hard limit: "
                f"{self.max_facilities} > "
                f"{MAX_INFRASTRUCTURE_PLACEMENT_FACILITIES}"
            )
        if self.max_iterations > MAX_INFRASTRUCTURE_PLACEMENT_ITERATIONS:
            raise InfrastructurePlacementError(
                "max_iterations exceeds placement hard limit: "
                f"{self.max_iterations} > "
                f"{MAX_INFRASTRUCTURE_PLACEMENT_ITERATIONS}"
            )


@dataclass(frozen=True, slots=True)
class InfrastructurePlacementSelection:
    """One deterministic greedy selection decision without mutating placement state."""

    iteration_index: int
    status: InfrastructurePlacementSelectionStatus
    selected: InfrastructureCandidateBenefit | None = None
    selected_feasibility: InfrastructureFeasibilityResult | None = None

    def __post_init__(self) -> None:
        _require_non_negative_int("iteration_index", self.iteration_index)
        if not isinstance(self.status, InfrastructurePlacementSelectionStatus):
            raise InfrastructurePlacementError(
                "status must be InfrastructurePlacementSelectionStatus"
            )
        if self.status is InfrastructurePlacementSelectionStatus.SELECTED:
            if not isinstance(self.selected, InfrastructureCandidateBenefit):
                raise InfrastructurePlacementError(
                    "selected status requires InfrastructureCandidateBenefit"
                )
            if self.selected.benefit <= 0.0:
                raise InfrastructurePlacementError(
                    "selected candidate must have positive benefit"
                )
            if not isinstance(
                self.selected_feasibility,
                InfrastructureFeasibilityResult,
            ):
                raise InfrastructurePlacementError(
                    "selected status requires InfrastructureFeasibilityResult"
                )
            if not self.selected_feasibility.is_feasible:
                raise InfrastructurePlacementError(
                    "selected feasibility result must be feasible"
                )
            if self.selected_feasibility.key != self.selected.candidate_ref.key:
                raise InfrastructurePlacementError(
                    "selected feasibility must match selected candidate"
                )
            if not math.isclose(
                self.selected_feasibility.proposed_capacity,
                self.selected.capacity,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise InfrastructurePlacementError(
                    "selected feasibility capacity must match selected benefit capacity"
                )
        elif self.selected is not None or self.selected_feasibility is not None:
            raise InfrastructurePlacementError(
                "non-selected status must not carry selected candidate or feasibility"
            )


@dataclass(frozen=True, slots=True)
class InfrastructureAcceptedFacility:
    """One accepted candidate site in greedy selection order."""

    candidate_ref: InfrastructureCandidateRef
    acceptance_index: int

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_ref, InfrastructureCandidateRef):
            raise InfrastructurePlacementError(
                "candidate_ref must be InfrastructureCandidateRef"
            )
        _require_non_negative_int("acceptance_index", self.acceptance_index)


@dataclass(frozen=True, slots=True)
class InfrastructureCoverageCacheEntry:
    """Reachable T07 rows cached for one candidate without rerunning routing."""

    snapshot_id: str
    infrastructure_type_code: str
    candidate_ref: InfrastructureCandidateRef
    accessibility: tuple[InfrastructureAccessibilityResult, ...]

    def __post_init__(self) -> None:
        _require_id("snapshot_id", self.snapshot_id)
        _require_id("infrastructure_type_code", self.infrastructure_type_code)
        if not isinstance(self.candidate_ref, InfrastructureCandidateRef):
            raise InfrastructurePlacementError(
                "candidate_ref must be InfrastructureCandidateRef"
            )
        if (
            self.candidate_ref.infrastructure_type_code
            != self.infrastructure_type_code
        ):
            raise InfrastructurePlacementError(
                "candidate_ref infrastructure type must match cache entry"
            )
        if not isinstance(self.accessibility, tuple):
            raise InfrastructurePlacementError(
                "accessibility must be an immutable tuple"
            )
        if any(
            not isinstance(item, InfrastructureAccessibilityResult)
            for item in self.accessibility
        ):
            raise InfrastructurePlacementError(
                "accessibility must contain InfrastructureAccessibilityResult values"
            )

        demand_keys: list[tuple[str, str]] = []
        for item in self.accessibility:
            if item.snapshot_id != self.snapshot_id:
                raise InfrastructurePlacementError(
                    "cached accessibility snapshot_id must match cache entry"
                )
            if item.infrastructure_type_code != self.infrastructure_type_code:
                raise InfrastructurePlacementError(
                    "cached accessibility infrastructure type must match cache entry"
                )
            if not isinstance(item.facility_site_ref, InfrastructureCandidateRef):
                raise InfrastructurePlacementError(
                    "coverage cache accepts only candidate accessibility rows"
                )
            if item.facility_site_ref != self.candidate_ref:
                raise InfrastructurePlacementError(
                    "cached accessibility candidate ref must match cache entry"
                )
            demand_keys.append(item.demand_ref.key)

        if demand_keys != sorted(demand_keys) or len(demand_keys) != len(
            set(demand_keys)
        ):
            raise InfrastructurePlacementError(
                "cached accessibility must be sorted and unique by demand ref"
            )

    @property
    def key(self) -> tuple[str, str]:
        return self.candidate_ref.key


@dataclass(frozen=True, slots=True)
class InfrastructureGreedyPlacementState:
    """Immutable state consumed by the ordered S10-T08 greedy placement tasks."""

    snapshot_id: str
    infrastructure_type_code: str
    remaining_demand: tuple[InfrastructurePlacementDemandState, ...]
    accepted_facilities: tuple[InfrastructureAcceptedFacility, ...]
    coverage_cache: tuple[InfrastructureCoverageCacheEntry, ...]
    candidate_order: tuple[InfrastructureCandidateRef, ...]

    def __post_init__(self) -> None:
        _require_id("snapshot_id", self.snapshot_id)
        _require_id("infrastructure_type_code", self.infrastructure_type_code)

        if not isinstance(self.remaining_demand, tuple):
            raise InfrastructurePlacementError(
                "remaining_demand must be an immutable tuple"
            )
        if not isinstance(self.accepted_facilities, tuple):
            raise InfrastructurePlacementError(
                "accepted_facilities must be an immutable tuple"
            )
        if not isinstance(self.coverage_cache, tuple):
            raise InfrastructurePlacementError(
                "coverage_cache must be an immutable tuple"
            )
        if not isinstance(self.candidate_order, tuple):
            raise InfrastructurePlacementError(
                "candidate_order must be an immutable tuple"
            )

        if any(
            not isinstance(item, InfrastructurePlacementDemandState)
            for item in self.remaining_demand
        ):
            raise InfrastructurePlacementError(
                "remaining_demand must contain InfrastructurePlacementDemandState values"
            )
        if any(
            not isinstance(item, InfrastructureAcceptedFacility)
            for item in self.accepted_facilities
        ):
            raise InfrastructurePlacementError(
                "accepted_facilities must contain InfrastructureAcceptedFacility values"
            )
        if any(
            not isinstance(item, InfrastructureCoverageCacheEntry)
            for item in self.coverage_cache
        ):
            raise InfrastructurePlacementError(
                "coverage_cache must contain InfrastructureCoverageCacheEntry values"
            )
        if any(
            not isinstance(item, InfrastructureCandidateRef)
            for item in self.candidate_order
        ):
            raise InfrastructurePlacementError(
                "candidate_order must contain InfrastructureCandidateRef values"
            )

        demand_keys = tuple(item.key for item in self.remaining_demand)
        if demand_keys != tuple(sorted(demand_keys)) or len(demand_keys) != len(
            set(demand_keys)
        ):
            raise InfrastructurePlacementError(
                "remaining_demand must be canonically sorted and unique"
            )
        candidate_keys = tuple(item.key for item in self.candidate_order)
        if candidate_keys != tuple(sorted(candidate_keys)) or len(
            candidate_keys
        ) != len(set(candidate_keys)):
            raise InfrastructurePlacementError(
                "candidate_order must be canonically sorted and unique"
            )
        if len(self.remaining_demand) > MAX_INFRASTRUCTURE_PLACEMENT_DEMANDS:
            raise InfrastructurePlacementError(
                "placement demand limit exceeded"
            )
        if len(self.candidate_order) > MAX_INFRASTRUCTURE_PLACEMENT_CANDIDATES:
            raise InfrastructurePlacementError(
                "placement candidate limit exceeded"
            )

        if any(
            item.demand_ref.infrastructure_type_code
            != self.infrastructure_type_code
            for item in self.remaining_demand
        ):
            raise InfrastructurePlacementError(
                "remaining demand infrastructure type must match placement state"
            )
        if any(
            item.infrastructure_type_code != self.infrastructure_type_code
            or item.snapshot_id != self.snapshot_id
            for item in self.coverage_cache
        ):
            raise InfrastructurePlacementError(
                "coverage cache provenance must match placement state"
            )
        if any(
            item.infrastructure_type_code != self.infrastructure_type_code
            for item in self.candidate_order
        ):
            raise InfrastructurePlacementError(
                "candidate order infrastructure type must match placement state"
            )

        cache_keys = tuple(item.key for item in self.coverage_cache)
        if cache_keys != candidate_keys:
            raise InfrastructurePlacementError(
                "coverage_cache must contain exactly one ordered entry per candidate"
            )

        demand_key_set = set(demand_keys)
        cached_row_count = 0
        for cache_entry in self.coverage_cache:
            cached_row_count += len(cache_entry.accessibility)
            for row in cache_entry.accessibility:
                if row.demand_ref.key not in demand_key_set:
                    raise InfrastructurePlacementError(
                        "coverage cache references demand outside placement state"
                    )
        if cached_row_count > MAX_INFRASTRUCTURE_PLACEMENT_COVERAGE_ROWS:
            raise InfrastructurePlacementError(
                "placement coverage cache row limit exceeded"
            )

        accepted_indices = tuple(
            item.acceptance_index for item in self.accepted_facilities
        )
        if accepted_indices != tuple(range(len(self.accepted_facilities))):
            raise InfrastructurePlacementError(
                "accepted facility indices must be contiguous from zero"
            )
        accepted_keys = tuple(
            item.candidate_ref.key for item in self.accepted_facilities
        )
        if len(accepted_keys) != len(set(accepted_keys)):
            raise InfrastructurePlacementError(
                "accepted facilities must reference unique candidates"
            )
        candidate_key_set = set(candidate_keys)
        if any(key not in candidate_key_set for key in accepted_keys):
            raise InfrastructurePlacementError(
                "accepted facility must reference candidate_order"
            )
        if any(
            item.candidate_ref.infrastructure_type_code
            != self.infrastructure_type_code
            for item in self.accepted_facilities
        ):
            raise InfrastructurePlacementError(
                "accepted facility infrastructure type must match placement state"
            )


def initialize_infrastructure_greedy_placement_state(
    demands: tuple[BlockInfrastructureDemand, ...],
    candidate_refs: tuple[InfrastructureCandidateRef, ...],
    *,
    candidate_accessibility: InfrastructureAccessibilityBatchResult,
) -> InfrastructureGreedyPlacementState:
    """Build canonical greedy state from T03 demand and complete T07 candidate outcomes."""

    if not isinstance(demands, tuple):
        raise InfrastructurePlacementError(
            "demands must be an immutable tuple"
        )
    if not isinstance(candidate_refs, tuple):
        raise InfrastructurePlacementError(
            "candidate_refs must be an immutable tuple"
        )
    if not isinstance(
        candidate_accessibility,
        InfrastructureAccessibilityBatchResult,
    ):
        raise InfrastructurePlacementError(
            "candidate_accessibility must be InfrastructureAccessibilityBatchResult"
        )
    if candidate_accessibility.mode is not InfrastructureAccessibilityMode.CANDIDATE_SITE:
        raise InfrastructurePlacementError(
            "candidate_accessibility must use candidate_site mode"
        )
    if any(not isinstance(item, BlockInfrastructureDemand) for item in demands):
        raise InfrastructurePlacementError(
            "demands must contain BlockInfrastructureDemand values"
        )
    if any(
        not isinstance(item, InfrastructureCandidateRef)
        for item in candidate_refs
    ):
        raise InfrastructurePlacementError(
            "candidate_refs must contain InfrastructureCandidateRef values"
        )

    type_code = candidate_accessibility.infrastructure_type_code
    if any(item.infrastructure_type_code != type_code for item in demands):
        raise InfrastructurePlacementError(
            "all demands must match candidate accessibility infrastructure type"
        )
    if any(
        item.infrastructure_type_code != type_code for item in candidate_refs
    ):
        raise InfrastructurePlacementError(
            "all candidate refs must match candidate accessibility infrastructure type"
        )

    ordered_demands = tuple(sorted(demands, key=lambda item: item.key))
    demand_keys = tuple(item.key for item in ordered_demands)
    if len(demand_keys) != len(set(demand_keys)):
        raise InfrastructurePlacementError(
            "demands must have unique block/type identities"
        )

    ordered_candidates = tuple(
        sorted(candidate_refs, key=lambda item: item.key)
    )
    candidate_keys = tuple(item.key for item in ordered_candidates)
    if len(candidate_keys) != len(set(candidate_keys)):
        raise InfrastructurePlacementError(
            "candidate_refs must have unique candidate/type identities"
        )
    if len(ordered_demands) > MAX_INFRASTRUCTURE_PLACEMENT_DEMANDS:
        raise InfrastructurePlacementError(
            "placement demand limit exceeded"
        )
    if len(ordered_candidates) > MAX_INFRASTRUCTURE_PLACEMENT_CANDIDATES:
        raise InfrastructurePlacementError(
            "placement candidate limit exceeded"
        )

    expected_subject_count = len(ordered_demands) * len(ordered_candidates)
    if expected_subject_count > MAX_INFRASTRUCTURE_PLACEMENT_COVERAGE_ROWS:
        raise InfrastructurePlacementError(
            "placement candidate-demand subject limit exceeded"
        )
    if (
        candidate_accessibility.diagnostics.subject_count
        != expected_subject_count
    ):
        raise InfrastructurePlacementError(
            "candidate accessibility must cover the complete candidate-demand matrix"
        )

    expected_pairs = {
        (demand.key, candidate.key)
        for demand in ordered_demands
        for candidate in ordered_candidates
    }
    actual_pairs = {
        _candidate_accessibility_pair(item)
        for item in candidate_accessibility.reachable
    }
    actual_pairs.update(
        _candidate_accessibility_pair(item)
        for item in candidate_accessibility.unavailable
    )
    if actual_pairs != expected_pairs:
        raise InfrastructurePlacementError(
            "candidate accessibility outcomes must match placement subjects exactly"
        )

    demand_state = tuple(
        InfrastructurePlacementDemandState(
            demand_ref=InfrastructureDemandRef(
                block_id=item.block_id,
                infrastructure_type_code=item.infrastructure_type_code,
            ),
            initial_demand=item.unmet_demand,
            remaining_demand=item.unmet_demand,
        )
        for item in ordered_demands
    )

    reachable_by_candidate: dict[
        tuple[str, str],
        list[InfrastructureAccessibilityResult],
    ] = {candidate.key: [] for candidate in ordered_candidates}
    for row in candidate_accessibility.reachable:
        candidate_ref = _require_candidate_ref(row)
        reachable_by_candidate[candidate_ref.key].append(row)

    coverage_cache = tuple(
        InfrastructureCoverageCacheEntry(
            snapshot_id=candidate_accessibility.snapshot_id,
            infrastructure_type_code=type_code,
            candidate_ref=candidate,
            accessibility=tuple(
                sorted(
                    reachable_by_candidate[candidate.key],
                    key=lambda item: item.demand_ref.key,
                )
            ),
        )
        for candidate in ordered_candidates
    )

    return InfrastructureGreedyPlacementState(
        snapshot_id=candidate_accessibility.snapshot_id,
        infrastructure_type_code=type_code,
        remaining_demand=demand_state,
        accepted_facilities=(),
        coverage_cache=coverage_cache,
        candidate_order=ordered_candidates,
    )



def calculate_infrastructure_candidate_benefits(
    state: InfrastructureGreedyPlacementState,
    *,
    infrastructure_type: InfrastructureType,
) -> tuple[InfrastructureCandidateBenefit, ...]:
    """Calculate current capacity-capped benefit from the cached T07 reachability rows."""

    if not isinstance(state, InfrastructureGreedyPlacementState):
        raise InfrastructurePlacementError(
            "state must be InfrastructureGreedyPlacementState"
        )
    if not isinstance(infrastructure_type, InfrastructureType):
        raise InfrastructurePlacementError(
            "infrastructure_type must be InfrastructureType"
        )
    if infrastructure_type.code != state.infrastructure_type_code:
        raise InfrastructurePlacementError(
            "InfrastructureType code must match placement state"
        )

    remaining_by_key = {
        item.demand_ref.key: item.remaining_demand
        for item in state.remaining_demand
    }
    accepted_keys = {
        item.candidate_ref.key for item in state.accepted_facilities
    }

    benefits: list[InfrastructureCandidateBenefit] = []
    for candidate_ref, cache_entry in zip(
        state.candidate_order,
        state.coverage_cache,
        strict=True,
    ):
        if candidate_ref.key in accepted_keys:
            continue

        reachable_remaining = _require_non_negative_finite(
            "reachable_remaining_demand",
            math.fsum(
                remaining_by_key[row.demand_ref.key]
                for row in cache_entry.accessibility
            ),
        )
        benefits.append(
            InfrastructureCandidateBenefit(
                candidate_ref=candidate_ref,
                reachable_remaining_demand=reachable_remaining,
                capacity=infrastructure_type.capacity,
                benefit=min(
                    reachable_remaining,
                    infrastructure_type.capacity,
                ),
            )
        )

    return tuple(benefits)


def select_infrastructure_greedy_candidate(
    state: InfrastructureGreedyPlacementState,
    benefits: tuple[InfrastructureCandidateBenefit, ...],
    *,
    feasibility: tuple[InfrastructureFeasibilityResult, ...],
    iteration_index: int,
    policy: InfrastructureGreedyPlacementPolicy | None = None,
) -> InfrastructurePlacementSelection:
    """Select the first maximum-benefit candidate under explicit greedy bounds."""

    if not isinstance(state, InfrastructureGreedyPlacementState):
        raise InfrastructurePlacementError(
            "state must be InfrastructureGreedyPlacementState"
        )
    if not isinstance(benefits, tuple):
        raise InfrastructurePlacementError(
            "benefits must be an immutable tuple"
        )
    if any(
        not isinstance(item, InfrastructureCandidateBenefit)
        for item in benefits
    ):
        raise InfrastructurePlacementError(
            "benefits must contain InfrastructureCandidateBenefit values"
        )
    if not isinstance(feasibility, tuple):
        raise InfrastructurePlacementError(
            "feasibility must be an immutable tuple"
        )
    if any(
        not isinstance(item, InfrastructureFeasibilityResult)
        for item in feasibility
    ):
        raise InfrastructurePlacementError(
            "feasibility must contain InfrastructureFeasibilityResult values"
        )
    _require_non_negative_int("iteration_index", iteration_index)
    if policy is None:
        policy = InfrastructureGreedyPlacementPolicy()
    if not isinstance(policy, InfrastructureGreedyPlacementPolicy):
        raise InfrastructurePlacementError(
            "policy must be InfrastructureGreedyPlacementPolicy"
        )

    accepted_keys = {
        item.candidate_ref.key for item in state.accepted_facilities
    }
    expected_candidate_refs = tuple(
        candidate
        for candidate in state.candidate_order
        if candidate.key not in accepted_keys
    )
    actual_candidate_refs = tuple(item.candidate_ref for item in benefits)
    if actual_candidate_refs != expected_candidate_refs:
        raise InfrastructurePlacementError(
            "benefits must contain every unaccepted candidate in candidate_order"
        )
    expected_keys = tuple(item.key for item in expected_candidate_refs)
    feasibility_keys = tuple(item.key for item in feasibility)
    if feasibility_keys != expected_keys:
        raise InfrastructurePlacementError(
            "feasibility must contain every unaccepted candidate in candidate_order"
        )
    for benefit, feasibility_result in zip(
        benefits,
        feasibility,
        strict=True,
    ):
        if feasibility_result.infrastructure_type_code != state.infrastructure_type_code:
            raise InfrastructurePlacementError(
                "feasibility infrastructure type must match placement state"
            )
        if not math.isclose(
            feasibility_result.proposed_capacity,
            benefit.capacity,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise InfrastructurePlacementError(
                "feasibility proposed capacity must match candidate benefit capacity"
            )

    if len(state.accepted_facilities) >= policy.max_facilities:
        return InfrastructurePlacementSelection(
            iteration_index=iteration_index,
            status=InfrastructurePlacementSelectionStatus.FACILITY_LIMIT_REACHED,
        )
    if iteration_index >= policy.max_iterations:
        return InfrastructurePlacementSelection(
            iteration_index=iteration_index,
            status=InfrastructurePlacementSelectionStatus.ITERATION_LIMIT_REACHED,
        )
    if not benefits:
        return InfrastructurePlacementSelection(
            iteration_index=iteration_index,
            status=InfrastructurePlacementSelectionStatus.NO_CANDIDATES,
        )

    feasible_pairs = tuple(
        (benefit, feasibility_result)
        for benefit, feasibility_result in zip(
            benefits,
            feasibility,
            strict=True,
        )
        if feasibility_result.is_feasible
    )
    if not feasible_pairs:
        return InfrastructurePlacementSelection(
            iteration_index=iteration_index,
            status=InfrastructurePlacementSelectionStatus.NO_FEASIBLE_CANDIDATE,
        )

    selected, selected_feasibility = feasible_pairs[0]
    for benefit, feasibility_result in feasible_pairs[1:]:
        if benefit.benefit > selected.benefit:
            selected = benefit
            selected_feasibility = feasibility_result

    if selected.benefit <= 0.0:
        return InfrastructurePlacementSelection(
            iteration_index=iteration_index,
            status=InfrastructurePlacementSelectionStatus.NO_POSITIVE_BENEFIT,
        )
    return InfrastructurePlacementSelection(
        iteration_index=iteration_index,
        status=InfrastructurePlacementSelectionStatus.SELECTED,
        selected=selected,
        selected_feasibility=selected_feasibility,
    )


def apply_infrastructure_greedy_selection(
    state: InfrastructureGreedyPlacementState,
    selection: InfrastructurePlacementSelection,
    *,
    infrastructure_type: InfrastructureType,
) -> InfrastructureGreedyPlacementState:
    """Apply one selected facility to current remaining demand without recomputing routing."""

    if not isinstance(state, InfrastructureGreedyPlacementState):
        raise InfrastructurePlacementError(
            "state must be InfrastructureGreedyPlacementState"
        )
    if not isinstance(selection, InfrastructurePlacementSelection):
        raise InfrastructurePlacementError(
            "selection must be InfrastructurePlacementSelection"
        )
    if not isinstance(infrastructure_type, InfrastructureType):
        raise InfrastructurePlacementError(
            "infrastructure_type must be InfrastructureType"
        )
    if infrastructure_type.code != state.infrastructure_type_code:
        raise InfrastructurePlacementError(
            "InfrastructureType code must match placement state"
        )
    if selection.status is not InfrastructurePlacementSelectionStatus.SELECTED:
        raise InfrastructurePlacementError(
            "only a selected placement decision can update remaining demand"
        )
    selected = selection.selected
    assert selected is not None
    if selected.candidate_ref.infrastructure_type_code != state.infrastructure_type_code:
        raise InfrastructurePlacementError(
            "selected candidate infrastructure type must match placement state"
        )
    if not math.isclose(
        selected.capacity,
        infrastructure_type.capacity,
        rel_tol=1e-12,
        abs_tol=1e-9,
    ):
        raise InfrastructurePlacementError(
            "selected candidate capacity must match InfrastructureType capacity"
        )

    accepted_keys = {
        item.candidate_ref.key for item in state.accepted_facilities
    }
    if selected.candidate_ref.key in accepted_keys:
        raise InfrastructurePlacementError(
            "selected candidate has already been accepted"
        )

    cache_by_key = {
        item.candidate_ref.key: item for item in state.coverage_cache
    }
    cache_entry = cache_by_key.get(selected.candidate_ref.key)
    if cache_entry is None:
        raise InfrastructurePlacementError(
            "selected candidate is outside placement coverage cache"
        )

    remaining_by_key = {
        item.demand_ref.key: item for item in state.remaining_demand
    }
    current_reachable = _require_non_negative_finite(
        "reachable_remaining_demand",
        math.fsum(
            remaining_by_key[row.demand_ref.key].remaining_demand
            for row in cache_entry.accessibility
        ),
    )
    current_benefit = min(
        current_reachable,
        infrastructure_type.capacity,
    )
    if not math.isclose(
        selected.reachable_remaining_demand,
        current_reachable,
        rel_tol=1e-12,
        abs_tol=1e-9,
    ) or not math.isclose(
        selected.benefit,
        current_benefit,
        rel_tol=1e-12,
        abs_tol=1e-9,
    ):
        raise InfrastructurePlacementError(
            "selected candidate benefit is stale for current placement state"
        )

    capacity_remaining = infrastructure_type.capacity
    updated_remaining = {
        item.demand_ref.key: item.remaining_demand
        for item in state.remaining_demand
    }
    served_amounts: list[float] = []

    for row in cache_entry.accessibility:
        if capacity_remaining <= 0.0:
            break
        demand_key = row.demand_ref.key
        current = updated_remaining[demand_key]
        served = min(current, capacity_remaining)
        if served <= 0.0:
            continue
        updated_remaining[demand_key] = max(current - served, 0.0)
        capacity_remaining = max(capacity_remaining - served, 0.0)
        served_amounts.append(served)

    served_total = _require_non_negative_finite(
        "served_demand",
        math.fsum(served_amounts),
    )
    if not math.isclose(
        served_total,
        selected.benefit,
        rel_tol=1e-12,
        abs_tol=1e-9,
    ):
        raise InfrastructurePlacementError(
            "applied served demand must equal selected candidate benefit"
        )

    remaining_demand = tuple(
        InfrastructurePlacementDemandState(
            demand_ref=item.demand_ref,
            initial_demand=item.initial_demand,
            remaining_demand=updated_remaining[item.demand_ref.key],
        )
        for item in state.remaining_demand
    )
    accepted_facilities = (
        *state.accepted_facilities,
        InfrastructureAcceptedFacility(
            candidate_ref=selected.candidate_ref,
            acceptance_index=len(state.accepted_facilities),
        ),
    )

    return InfrastructureGreedyPlacementState(
        snapshot_id=state.snapshot_id,
        infrastructure_type_code=state.infrastructure_type_code,
        remaining_demand=remaining_demand,
        accepted_facilities=accepted_facilities,
        coverage_cache=state.coverage_cache,
        candidate_order=state.candidate_order,
    )


def _candidate_accessibility_pair(
    item: InfrastructureAccessibilityResult | InfrastructureAccessibilityUnavailable,
) -> tuple[tuple[str, str], tuple[str, str]]:
    candidate_ref = _require_candidate_ref(item)
    return item.demand_ref.key, candidate_ref.key


def _require_candidate_ref(
    item: InfrastructureAccessibilityResult | InfrastructureAccessibilityUnavailable,
) -> InfrastructureCandidateRef:
    ref = item.facility_site_ref
    if not isinstance(ref, InfrastructureCandidateRef):
        raise InfrastructurePlacementError(
            "candidate accessibility outcome must reference InfrastructureCandidateRef"
        )
    return ref


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InfrastructurePlacementError(
            f"{field_name} must be a non-empty string"
        )
    if "\n" in value or "\r" in value:
        raise InfrastructurePlacementError(
            f"{field_name} must not contain line breaks"
        )


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0.0
    ):
        raise InfrastructurePlacementError(
            f"{field_name} must be a finite non-negative number"
        )
    return float(value)


def _require_positive_finite(field_name: str, value: float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number <= 0.0:
        raise InfrastructurePlacementError(
            f"{field_name} must be a finite positive number"
        )
    return number


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InfrastructurePlacementError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InfrastructurePlacementError(
            f"{field_name} must be a non-negative integer"
        )
