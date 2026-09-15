from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

import numpy as np

from core.urban_generator.domain import require_working_crs
from core.urban_generator.roads.candidate_anchors import CandidateRoadAnchor
from core.urban_generator.roads.least_cost_connector import (
    LeastCostConnectionResult,
    LeastCostConnectionStatus,
    LeastCostConnector,
    LeastCostConnectorPolicy,
)
from core.urban_generator.roads.mst_connector import AnchorConnectivityResult
from core.urban_generator.suitability import HardExclusionMask, WeightedSuitabilityResult

DEFAULT_MAX_GROWTH_ITERATIONS = 200
DEFAULT_MAX_GROWTH_LENGTH_M = 25_000.0
DEFAULT_MAX_GROWTH_CANDIDATE_PAIRS = 499_500
_LENGTH_TOLERANCE_M = 1e-9


class RuleBasedRoadGrowthError(ValueError):
    """Raised when rule-based road growth violates its bounded core contract."""


class RoadGrowthIntent(StrEnum):
    """Provisional growth intent; authoritative road class is assigned by S06-T12."""

    LOCAL = "LOCAL"
    COLLECTOR = "COLLECTOR"


class RoadGrowthAttemptStatus(StrEnum):
    """Stable outcome of one routed growth candidate."""

    ADDED = "ADDED"
    NO_PATH = "NO_PATH"
    SEARCH_LIMIT_REACHED = "SEARCH_LIMIT_REACHED"
    LENGTH_BUDGET_REJECTED = "LENGTH_BUDGET_REJECTED"


class RoadGrowthStopReason(StrEnum):
    """Why a bounded rule-based growth pass stopped."""

    CANDIDATES_EXHAUSTED = "CANDIDATES_EXHAUSTED"
    ITERATION_LIMIT = "ITERATION_LIMIT"
    LENGTH_BUDGET = "LENGTH_BUDGET"


@dataclass(frozen=True, slots=True)
class RuleBasedRoadGrowthPolicy:
    """Explicit work and output budgets for deterministic anchor-pair growth."""

    max_iterations: int = DEFAULT_MAX_GROWTH_ITERATIONS
    max_added_length_m: float = DEFAULT_MAX_GROWTH_LENGTH_M
    max_candidate_pairs: int = DEFAULT_MAX_GROWTH_CANDIDATE_PAIRS

    def __post_init__(self) -> None:
        _require_positive_int("max_iterations", self.max_iterations)
        length_m = _require_positive_finite("max_added_length_m", self.max_added_length_m)
        object.__setattr__(self, "max_added_length_m", length_m)
        _require_positive_int("max_candidate_pairs", self.max_candidate_pairs)


@dataclass(frozen=True, slots=True)
class RoadGrowthAttempt:
    """One deterministic candidate routing attempt with preserved pair result."""

    attempt_index: int
    intent: RoadGrowthIntent
    start_anchor_id: str
    target_anchor_id: str
    direct_distance_m: float
    status: RoadGrowthAttemptStatus
    connection: LeastCostConnectionResult

    def __post_init__(self) -> None:
        _require_non_negative_int("attempt_index", self.attempt_index)
        if not isinstance(self.intent, RoadGrowthIntent):
            raise RuleBasedRoadGrowthError("intent must be a RoadGrowthIntent value")
        _require_non_empty_string("start_anchor_id", self.start_anchor_id)
        _require_non_empty_string("target_anchor_id", self.target_anchor_id)
        if self.start_anchor_id >= self.target_anchor_id:
            raise RuleBasedRoadGrowthError(
                "growth attempt anchor ids must use canonical ascending order"
            )
        direct_distance_m = _require_positive_finite(
            "direct_distance_m", self.direct_distance_m
        )
        object.__setattr__(self, "direct_distance_m", direct_distance_m)
        if not isinstance(self.status, RoadGrowthAttemptStatus):
            raise RuleBasedRoadGrowthError(
                "status must be a RoadGrowthAttemptStatus value"
            )
        if not isinstance(self.connection, LeastCostConnectionResult):
            raise RuleBasedRoadGrowthError(
                "connection must be a LeastCostConnectionResult"
            )
        if self.connection.start_anchor_id != self.start_anchor_id:
            raise RuleBasedRoadGrowthError(
                "connection start_anchor_id must match growth attempt"
            )
        if self.connection.target_anchor_id != self.target_anchor_id:
            raise RuleBasedRoadGrowthError(
                "connection target_anchor_id must match growth attempt"
            )
        self._validate_status()

    def _validate_status(self) -> None:
        if self.status in {
            RoadGrowthAttemptStatus.ADDED,
            RoadGrowthAttemptStatus.LENGTH_BUDGET_REJECTED,
        }:
            if self.connection.status is not LeastCostConnectionStatus.CONNECTED:
                raise RuleBasedRoadGrowthError(
                    "added/budget-rejected growth attempt requires CONNECTED pair result"
                )
            return
        if self.status is RoadGrowthAttemptStatus.NO_PATH:
            expected = LeastCostConnectionStatus.NO_PATH
        else:
            expected = LeastCostConnectionStatus.SEARCH_LIMIT_REACHED
        if self.connection.status is not expected:
            raise RuleBasedRoadGrowthError(
                "growth attempt status must agree with delegated pair result"
            )

    @property
    def added(self) -> bool:
        return self.status is RoadGrowthAttemptStatus.ADDED


@dataclass(frozen=True, slots=True)
class RuleBasedRoadGrowthDiagnostics:
    """Stable candidate, routing, intent and budget diagnostics for one growth pass."""

    anchor_count: int
    baseline_pair_count: int
    candidate_pair_evaluation_count: int
    eligible_candidate_count: int
    attempted_connection_count: int
    added_edge_count: int
    added_local_count: int
    added_collector_count: int
    no_path_count: int
    search_limit_reached_count: int
    length_budget_rejected_count: int
    visited_cell_count: int
    added_length_m: float
    stop_reason: RoadGrowthStopReason

    def __post_init__(self) -> None:
        for field_name in (
            "anchor_count",
            "baseline_pair_count",
            "candidate_pair_evaluation_count",
            "eligible_candidate_count",
            "attempted_connection_count",
            "added_edge_count",
            "added_local_count",
            "added_collector_count",
            "no_path_count",
            "search_limit_reached_count",
            "length_budget_rejected_count",
            "visited_cell_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if self.baseline_pair_count > self.candidate_pair_evaluation_count:
            raise RuleBasedRoadGrowthError(
                "baseline_pair_count cannot exceed candidate pair evaluations"
            )
        if self.eligible_candidate_count > self.candidate_pair_evaluation_count:
            raise RuleBasedRoadGrowthError(
                "eligible candidates cannot exceed candidate pair evaluations"
            )
        if self.attempted_connection_count > self.eligible_candidate_count:
            raise RuleBasedRoadGrowthError(
                "attempted connections cannot exceed eligible candidates"
            )
        status_total = (
            self.added_edge_count
            + self.no_path_count
            + self.search_limit_reached_count
            + self.length_budget_rejected_count
        )
        if status_total != self.attempted_connection_count:
            raise RuleBasedRoadGrowthError(
                "growth attempt status counts must sum to attempted_connection_count"
            )
        if self.added_local_count + self.added_collector_count != self.added_edge_count:
            raise RuleBasedRoadGrowthError(
                "growth intent counts must sum to added_edge_count"
            )
        object.__setattr__(
            self,
            "added_length_m",
            _require_non_negative_finite("added_length_m", self.added_length_m),
        )
        if not isinstance(self.stop_reason, RoadGrowthStopReason):
            raise RuleBasedRoadGrowthError(
                "stop_reason must be a RoadGrowthStopReason value"
            )


@dataclass(frozen=True, slots=True)
class RuleBasedRoadGrowthResult:
    """Immutable S06-T11 growth delta with full attempted-pair provenance."""

    working_srid: int
    anchor_ids: tuple[str, ...]
    attempts: tuple[RoadGrowthAttempt, ...]
    diagnostics: RuleBasedRoadGrowthDiagnostics
    strategy_name: str
    strategy_version: str
    baseline_strategy_name: str
    baseline_strategy_version: str

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        if not isinstance(self.anchor_ids, tuple):
            raise RuleBasedRoadGrowthError("anchor_ids must be an immutable tuple")
        if tuple(sorted(self.anchor_ids)) != self.anchor_ids:
            raise RuleBasedRoadGrowthError("anchor_ids must be sorted deterministically")
        if len(set(self.anchor_ids)) != len(self.anchor_ids):
            raise RuleBasedRoadGrowthError("anchor_ids must be unique")
        for anchor_id in self.anchor_ids:
            _require_non_empty_string("anchor_id", anchor_id)
        if not isinstance(self.attempts, tuple):
            raise RuleBasedRoadGrowthError("attempts must be an immutable tuple")
        if any(not isinstance(item, RoadGrowthAttempt) for item in self.attempts):
            raise RuleBasedRoadGrowthError(
                "attempts must contain only RoadGrowthAttempt values"
            )
        if tuple(item.attempt_index for item in self.attempts) != tuple(
            range(len(self.attempts))
        ):
            raise RuleBasedRoadGrowthError(
                "growth attempt indexes must be contiguous from zero"
            )
        anchor_id_set = set(self.anchor_ids)
        for attempt in self.attempts:
            if attempt.start_anchor_id not in anchor_id_set:
                raise RuleBasedRoadGrowthError(
                    "growth attempt start anchor must exist in anchor_ids"
                )
            if attempt.target_anchor_id not in anchor_id_set:
                raise RuleBasedRoadGrowthError(
                    "growth attempt target anchor must exist in anchor_ids"
                )
            if attempt.connection.working_srid != self.working_srid:
                raise RuleBasedRoadGrowthError(
                    "growth connection working_srid must match result working_srid"
                )
        if not isinstance(self.diagnostics, RuleBasedRoadGrowthDiagnostics):
            raise RuleBasedRoadGrowthError(
                "diagnostics must be RuleBasedRoadGrowthDiagnostics"
            )
        if self.diagnostics.anchor_count != len(self.anchor_ids):
            raise RuleBasedRoadGrowthError(
                "diagnostics anchor_count must match anchor_ids"
            )
        if self.diagnostics.attempted_connection_count != len(self.attempts):
            raise RuleBasedRoadGrowthError(
                "diagnostics attempted_connection_count must match attempts"
            )
        for field_name in (
            "strategy_name",
            "strategy_version",
            "baseline_strategy_name",
            "baseline_strategy_version",
        ):
            _require_non_empty_string(field_name, getattr(self, field_name))

    @property
    def added_edges(self) -> tuple[RoadGrowthAttempt, ...]:
        return tuple(attempt for attempt in self.attempts if attempt.added)


@runtime_checkable
class RuleBasedRoadGrowthStrategy(Protocol):
    """Pluggable core strategy port for bounded post-baseline road growth."""

    name: str
    version: str

    def grow(
        self,
        *,
        anchors: tuple[CandidateRoadAnchor, ...],
        baseline: AnchorConnectivityResult,
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        pair_policy: LeastCostConnectorPolicy | None = None,
    ) -> RuleBasedRoadGrowthResult:
        """Add a bounded deterministic set of post-baseline anchor connections."""

        ...


class _AnchorPairConnector(Protocol):
    def connect(
        self,
        *,
        start: CandidateRoadAnchor,
        target: CandidateRoadAnchor,
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        policy: LeastCostConnectorPolicy | None = None,
    ) -> LeastCostConnectionResult: ...


@dataclass(frozen=True, slots=True)
class _GrowthCandidate:
    intent: RoadGrowthIntent
    start: CandidateRoadAnchor
    target: CandidateRoadAnchor
    direct_distance_m: float


class RuleBasedRoadGrower:
    """Densify an MST baseline through deterministic local/collector anchor rules."""

    name = "rule-based-local-collector-growth"
    version = "1"

    def __init__(
        self,
        *,
        policy: RuleBasedRoadGrowthPolicy | None = None,
        pair_connector: _AnchorPairConnector | None = None,
    ) -> None:
        if policy is None:
            policy = RuleBasedRoadGrowthPolicy()
        if not isinstance(policy, RuleBasedRoadGrowthPolicy):
            raise RuleBasedRoadGrowthError(
                "policy must be a RuleBasedRoadGrowthPolicy"
            )
        if pair_connector is not None and not callable(
            getattr(pair_connector, "connect", None)
        ):
            raise RuleBasedRoadGrowthError("pair_connector must expose connect()")
        self._policy = policy
        self._pair_connector = pair_connector or LeastCostConnector()

    @property
    def policy(self) -> RuleBasedRoadGrowthPolicy:
        return self._policy

    def grow(
        self,
        *,
        anchors: tuple[CandidateRoadAnchor, ...],
        baseline: AnchorConnectivityResult,
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        pair_policy: LeastCostConnectorPolicy | None = None,
    ) -> RuleBasedRoadGrowthResult:
        ordered_anchors = _validate_inputs(
            anchors=anchors,
            baseline=baseline,
            suitability=suitability,
            hard_mask=hard_mask,
            pair_policy=pair_policy,
            policy=self._policy,
        )
        baseline_pairs = {
            _canonical_pair(edge.start_anchor_id, edge.target_anchor_id)
            for edge in baseline.edges
        }
        local_candidates, collector_candidates, evaluation_count = _build_candidates(
            anchors=ordered_anchors,
            baseline_pairs=baseline_pairs,
        )
        attempts, added_length_m, visited_cell_count, stop_reason = self._run_candidates(
            local_candidates=local_candidates,
            collector_candidates=collector_candidates,
            suitability=suitability,
            hard_mask=hard_mask,
            pair_policy=pair_policy,
        )
        diagnostics = _build_diagnostics(
            anchor_count=len(ordered_anchors),
            baseline_pair_count=len(baseline_pairs),
            candidate_pair_evaluation_count=evaluation_count,
            eligible_candidate_count=len(local_candidates) + len(collector_candidates),
            attempts=attempts,
            visited_cell_count=visited_cell_count,
            added_length_m=added_length_m,
            stop_reason=stop_reason,
        )
        return RuleBasedRoadGrowthResult(
            working_srid=suitability.grid.working_srid,
            anchor_ids=tuple(anchor.anchor_id for anchor in ordered_anchors),
            attempts=attempts,
            diagnostics=diagnostics,
            strategy_name=self.name,
            strategy_version=self.version,
            baseline_strategy_name=baseline.strategy_name,
            baseline_strategy_version=baseline.strategy_version,
        )

    def _run_candidates(
        self,
        *,
        local_candidates: tuple[_GrowthCandidate, ...],
        collector_candidates: tuple[_GrowthCandidate, ...],
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        pair_policy: LeastCostConnectorPolicy | None,
    ) -> tuple[
        tuple[RoadGrowthAttempt, ...],
        float,
        int,
        RoadGrowthStopReason,
    ]:
        indexes = {
            RoadGrowthIntent.LOCAL: 0,
            RoadGrowthIntent.COLLECTOR: 0,
        }
        candidates = {
            RoadGrowthIntent.LOCAL: local_candidates,
            RoadGrowthIntent.COLLECTOR: collector_candidates,
        }
        preferred = RoadGrowthIntent.COLLECTOR
        attempts: list[RoadGrowthAttempt] = []
        added_length_m = 0.0
        visited_cell_count = 0

        while True:
            if len(attempts) >= self._policy.max_iterations:
                stop_reason = RoadGrowthStopReason.ITERATION_LIMIT
                break

            remaining_length_m = self._policy.max_added_length_m - added_length_m
            if remaining_length_m <= _LENGTH_TOLERANCE_M:
                stop_reason = RoadGrowthStopReason.LENGTH_BUDGET
                break

            candidate, chosen_intent, exhausted = _choose_next_candidate(
                candidates=candidates,
                indexes=indexes,
                preferred=preferred,
                remaining_length_m=remaining_length_m,
            )
            if candidate is None:
                stop_reason = (
                    RoadGrowthStopReason.CANDIDATES_EXHAUSTED
                    if exhausted
                    else RoadGrowthStopReason.LENGTH_BUDGET
                )
                break

            indexes[chosen_intent] += 1
            preferred = _other_intent(chosen_intent)
            connection = self._pair_connector.connect(
                start=candidate.start,
                target=candidate.target,
                suitability=suitability,
                hard_mask=hard_mask,
                policy=pair_policy,
            )
            if not isinstance(connection, LeastCostConnectionResult):
                raise RuleBasedRoadGrowthError(
                    "pair_connector must return LeastCostConnectionResult"
                )
            if connection.start_anchor_id != candidate.start.anchor_id:
                raise RuleBasedRoadGrowthError(
                    "pair connector start_anchor_id must match requested candidate"
                )
            if connection.target_anchor_id != candidate.target.anchor_id:
                raise RuleBasedRoadGrowthError(
                    "pair connector target_anchor_id must match requested candidate"
                )
            if connection.working_srid != suitability.grid.working_srid:
                raise RuleBasedRoadGrowthError(
                    "pair connector result must use the growth working_srid"
                )
            visited_cell_count += connection.visited_cell_count

            attempt_status = _attempt_status(
                connection=connection,
                remaining_length_m=remaining_length_m,
            )
            attempt = RoadGrowthAttempt(
                attempt_index=len(attempts),
                intent=candidate.intent,
                start_anchor_id=candidate.start.anchor_id,
                target_anchor_id=candidate.target.anchor_id,
                direct_distance_m=candidate.direct_distance_m,
                status=attempt_status,
                connection=connection,
            )
            attempts.append(attempt)
            if attempt.added:
                if connection.length_m is None:
                    raise RuleBasedRoadGrowthError(
                        "CONNECTED pair result must provide length_m"
                    )
                added_length_m += connection.length_m

        return (
            tuple(attempts),
            added_length_m,
            visited_cell_count,
            stop_reason,
        )


def _validate_inputs(
    *,
    anchors: tuple[CandidateRoadAnchor, ...],
    baseline: AnchorConnectivityResult,
    suitability: WeightedSuitabilityResult,
    hard_mask: HardExclusionMask,
    pair_policy: LeastCostConnectorPolicy | None,
    policy: RuleBasedRoadGrowthPolicy,
) -> tuple[CandidateRoadAnchor, ...]:
    if not isinstance(anchors, tuple):
        raise RuleBasedRoadGrowthError("anchors must be an immutable tuple")
    if any(not isinstance(anchor, CandidateRoadAnchor) for anchor in anchors):
        raise RuleBasedRoadGrowthError(
            "anchors must contain only CandidateRoadAnchor values"
        )
    if len({anchor.anchor_id for anchor in anchors}) != len(anchors):
        raise RuleBasedRoadGrowthError("anchor_id values must be unique")
    raster_cells = tuple((anchor.raster_row, anchor.raster_col) for anchor in anchors)
    if len(set(raster_cells)) != len(raster_cells):
        raise RuleBasedRoadGrowthError("anchors must occupy unique raster cells")
    if not isinstance(baseline, AnchorConnectivityResult):
        raise RuleBasedRoadGrowthError(
            "baseline must be an AnchorConnectivityResult"
        )
    if not isinstance(suitability, WeightedSuitabilityResult):
        raise RuleBasedRoadGrowthError(
            "suitability must be a WeightedSuitabilityResult"
        )
    if not isinstance(hard_mask, HardExclusionMask):
        raise RuleBasedRoadGrowthError("hard_mask must be a HardExclusionMask")
    if pair_policy is not None and not isinstance(pair_policy, LeastCostConnectorPolicy):
        raise RuleBasedRoadGrowthError(
            "pair_policy must be a LeastCostConnectorPolicy"
        )

    require_working_crs(suitability.grid.working_srid)
    if baseline.working_srid != suitability.grid.working_srid:
        raise RuleBasedRoadGrowthError(
            "baseline and suitability must use the same working_srid"
        )
    if hard_mask.grid != suitability.grid:
        raise RuleBasedRoadGrowthError(
            "hard_mask must use exactly the suitability grid"
        )
    if not np.array_equal(suitability.hard_excluded_mask, hard_mask.excluded):
        raise RuleBasedRoadGrowthError(
            "suitability hard-excluded cells must match hard_mask exactly"
        )

    ordered = tuple(sorted(anchors, key=lambda anchor: anchor.anchor_id))
    anchor_ids = tuple(anchor.anchor_id for anchor in ordered)
    if baseline.anchor_ids != anchor_ids:
        raise RuleBasedRoadGrowthError(
            "baseline anchor_ids must exactly match the growth anchor set"
        )
    evaluation_count = len(ordered) * (len(ordered) - 1) // 2
    if evaluation_count > policy.max_candidate_pairs:
        raise RuleBasedRoadGrowthError(
            "rule-based growth candidate-pair limit exceeded: "
            f"{evaluation_count} > {policy.max_candidate_pairs}"
        )
    return ordered


def _build_candidates(
    *,
    anchors: tuple[CandidateRoadAnchor, ...],
    baseline_pairs: set[tuple[str, str]],
) -> tuple[tuple[_GrowthCandidate, ...], tuple[_GrowthCandidate, ...], int]:
    local: list[_GrowthCandidate] = []
    collector: list[_GrowthCandidate] = []
    evaluation_count = 0
    for start_index, start in enumerate(anchors):
        for target in anchors[start_index + 1 :]:
            evaluation_count += 1
            pair = (start.anchor_id, target.anchor_id)
            if pair in baseline_pairs:
                continue
            intent = (
                RoadGrowthIntent.LOCAL
                if start.zone_class is target.zone_class
                else RoadGrowthIntent.COLLECTOR
            )
            candidate = _GrowthCandidate(
                intent=intent,
                start=start,
                target=target,
                direct_distance_m=_direct_distance_m(start, target),
            )
            if candidate.direct_distance_m <= 0.0:
                raise RuleBasedRoadGrowthError(
                    "growth anchors must occupy unique metric point coordinates"
                )
            if intent is RoadGrowthIntent.LOCAL:
                local.append(candidate)
            else:
                collector.append(candidate)

    local.sort(key=_candidate_sort_key)
    collector.sort(key=_candidate_sort_key)
    return tuple(local), tuple(collector), evaluation_count


def _candidate_sort_key(candidate: _GrowthCandidate) -> tuple[float, str, str]:
    return (
        candidate.direct_distance_m,
        candidate.start.anchor_id,
        candidate.target.anchor_id,
    )


def _choose_next_candidate(
    *,
    candidates: dict[RoadGrowthIntent, tuple[_GrowthCandidate, ...]],
    indexes: dict[RoadGrowthIntent, int],
    preferred: RoadGrowthIntent,
    remaining_length_m: float,
) -> tuple[_GrowthCandidate | None, RoadGrowthIntent, bool]:
    other = _other_intent(preferred)
    for intent in (preferred, other):
        index = indexes[intent]
        items = candidates[intent]
        if index >= len(items):
            continue
        candidate = items[index]
        if candidate.direct_distance_m <= remaining_length_m + _LENGTH_TOLERANCE_M:
            return candidate, intent, False

    exhausted = all(indexes[intent] >= len(candidates[intent]) for intent in RoadGrowthIntent)
    return None, preferred, exhausted


def _attempt_status(
    *,
    connection: LeastCostConnectionResult,
    remaining_length_m: float,
) -> RoadGrowthAttemptStatus:
    if connection.status is LeastCostConnectionStatus.NO_PATH:
        return RoadGrowthAttemptStatus.NO_PATH
    if connection.status is LeastCostConnectionStatus.SEARCH_LIMIT_REACHED:
        return RoadGrowthAttemptStatus.SEARCH_LIMIT_REACHED
    if connection.status is not LeastCostConnectionStatus.CONNECTED:
        raise RuleBasedRoadGrowthError("unsupported pair connector status")
    if connection.length_m is None:
        raise RuleBasedRoadGrowthError("CONNECTED pair result must provide length_m")
    if connection.length_m <= remaining_length_m + _LENGTH_TOLERANCE_M:
        return RoadGrowthAttemptStatus.ADDED
    return RoadGrowthAttemptStatus.LENGTH_BUDGET_REJECTED


def _build_diagnostics(
    *,
    anchor_count: int,
    baseline_pair_count: int,
    candidate_pair_evaluation_count: int,
    eligible_candidate_count: int,
    attempts: tuple[RoadGrowthAttempt, ...],
    visited_cell_count: int,
    added_length_m: float,
    stop_reason: RoadGrowthStopReason,
) -> RuleBasedRoadGrowthDiagnostics:
    added = tuple(item for item in attempts if item.added)
    return RuleBasedRoadGrowthDiagnostics(
        anchor_count=anchor_count,
        baseline_pair_count=baseline_pair_count,
        candidate_pair_evaluation_count=candidate_pair_evaluation_count,
        eligible_candidate_count=eligible_candidate_count,
        attempted_connection_count=len(attempts),
        added_edge_count=len(added),
        added_local_count=sum(item.intent is RoadGrowthIntent.LOCAL for item in added),
        added_collector_count=sum(
            item.intent is RoadGrowthIntent.COLLECTOR for item in added
        ),
        no_path_count=sum(
            item.status is RoadGrowthAttemptStatus.NO_PATH for item in attempts
        ),
        search_limit_reached_count=sum(
            item.status is RoadGrowthAttemptStatus.SEARCH_LIMIT_REACHED
            for item in attempts
        ),
        length_budget_rejected_count=sum(
            item.status is RoadGrowthAttemptStatus.LENGTH_BUDGET_REJECTED
            for item in attempts
        ),
        visited_cell_count=visited_cell_count,
        added_length_m=added_length_m,
        stop_reason=stop_reason,
    )


def _canonical_pair(first: str, second: str) -> tuple[str, str]:
    if first <= second:
        return first, second
    return second, first


def _other_intent(intent: RoadGrowthIntent) -> RoadGrowthIntent:
    if intent is RoadGrowthIntent.COLLECTOR:
        return RoadGrowthIntent.LOCAL
    return RoadGrowthIntent.COLLECTOR


def _direct_distance_m(first: CandidateRoadAnchor, second: CandidateRoadAnchor) -> float:
    return math.hypot(
        second.point.x_m - first.point.x_m,
        second.point.y_m - first.point.y_m,
    )


def _require_non_empty_string(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RuleBasedRoadGrowthError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise RuleBasedRoadGrowthError(f"{field_name} must not contain line breaks")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuleBasedRoadGrowthError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuleBasedRoadGrowthError(f"{field_name} must be a non-negative integer")


def _require_positive_finite(field_name: str, value: float | int) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number <= 0.0:
        raise RuleBasedRoadGrowthError(f"{field_name} must be greater than zero")
    return number


def _require_non_negative_finite(field_name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleBasedRoadGrowthError(
            f"{field_name} must be a non-negative finite number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise RuleBasedRoadGrowthError(
            f"{field_name} must be a non-negative finite number"
        )
    return number
