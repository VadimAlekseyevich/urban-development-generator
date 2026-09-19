from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from core.urban_generator.buildings.spacing import (
    DEFAULT_MAX_SPACING_CANDIDATES,
    DEFAULT_MAX_SPACING_FOOTPRINTS,
    BuildingSpacingHit,
    BuildingSpacingIndex,
    BuildingSpacingPolicy,
    BuildingSpacingSubject,
    PlacedBuildingFootprint,
)
from core.urban_generator.domain.crs import require_working_crs

DEFAULT_MAX_CONVERGENCE_PROPOSALS = 50_000
DEFAULT_MAX_CONVERGENCE_ITERATIONS = 50_000

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")
_METRIC_EPSILON = 1e-12


class BuildingPlacementConvergenceError(ValueError):
    """Raised when S08-T09 placement convergence inputs violate the contract."""


class BuildingPlacementConvergenceStatus(StrEnum):
    """Bounded deterministic outcome of one placement convergence pass."""

    CONVERGED = "CONVERGED"
    CANDIDATES_EXHAUSTED = "CANDIDATES_EXHAUSTED"
    MAX_ITERATIONS = "MAX_ITERATIONS"


@dataclass(frozen=True, slots=True)
class BuildingPlacementProposal:
    """One ready 2D footprint proposal plus provisional FAR contribution.

    ``planning_floor_area_multiplier`` is an intensity hint used only by S08-T09
    convergence. It is deliberately not an assigned floor count or final GFA:
    authoritative floors/use belong to S08-T10 and final area/GFA calculation to
    S08-T11.
    """

    proposal_id: str
    source_id: str
    geometry: BaseGeometry
    working_srid: int
    planning_floor_area_multiplier: float = 1.0
    priority: float = 0.0

    def __post_init__(self) -> None:
        _require_id("proposal_id", self.proposal_id)
        _require_id("source_id", self.source_id)
        require_working_crs(self.working_srid)
        _require_polygonal_geometry("proposal geometry", self.geometry)
        multiplier = _require_positive_finite(
            "planning_floor_area_multiplier",
            self.planning_floor_area_multiplier,
        )
        priority = _require_finite("priority", self.priority)
        object.__setattr__(self, "planning_floor_area_multiplier", multiplier)
        object.__setattr__(self, "priority", priority)

    @property
    def footprint_area_m2(self) -> float:
        return float(self.geometry.area)

    @property
    def planning_floor_area_m2(self) -> float:
        return self.footprint_area_m2 * self.planning_floor_area_multiplier


@dataclass(frozen=True, slots=True)
class BuildingPlacementTargets:
    """Coverage/FAR target window for one bounded placement scope."""

    site_area_m2: float
    target_coverage_ratio: float
    target_far: float
    coverage_tolerance: float = 0.02
    far_tolerance: float = 0.10

    def __post_init__(self) -> None:
        site_area = _require_positive_finite("site_area_m2", self.site_area_m2)
        coverage = _require_ratio("target_coverage_ratio", self.target_coverage_ratio)
        far = _require_non_negative_finite("target_far", self.target_far)
        coverage_tolerance = _require_non_negative_finite(
            "coverage_tolerance",
            self.coverage_tolerance,
        )
        if coverage_tolerance > 1.0:
            raise BuildingPlacementConvergenceError(
                "coverage_tolerance must be <= 1.0"
            )
        far_tolerance = _require_non_negative_finite(
            "far_tolerance",
            self.far_tolerance,
        )
        object.__setattr__(self, "site_area_m2", site_area)
        object.__setattr__(self, "target_coverage_ratio", coverage)
        object.__setattr__(self, "target_far", far)
        object.__setattr__(self, "coverage_tolerance", coverage_tolerance)
        object.__setattr__(self, "far_tolerance", far_tolerance)

    @property
    def coverage_lower_bound(self) -> float:
        return max(0.0, self.target_coverage_ratio - self.coverage_tolerance)

    @property
    def coverage_upper_bound(self) -> float:
        return min(1.0, self.target_coverage_ratio + self.coverage_tolerance)

    @property
    def far_lower_bound(self) -> float:
        return max(0.0, self.target_far - self.far_tolerance)

    @property
    def far_upper_bound(self) -> float:
        return self.target_far + self.far_tolerance


@dataclass(frozen=True, slots=True)
class BuildingPlacementBaseline:
    """Existing fixed/generated intensity already counted in target metrics."""

    coverage_area_m2: float = 0.0
    planning_floor_area_m2: float = 0.0

    def __post_init__(self) -> None:
        coverage = _require_non_negative_finite(
            "coverage_area_m2",
            self.coverage_area_m2,
        )
        floor_area = _require_non_negative_finite(
            "planning_floor_area_m2",
            self.planning_floor_area_m2,
        )
        object.__setattr__(self, "coverage_area_m2", coverage)
        object.__setattr__(self, "planning_floor_area_m2", floor_area)


@dataclass(frozen=True, slots=True)
class BuildingPlacementConvergenceDiagnostics:
    """Target progress plus explicit reasons for proposals not being accepted."""

    status: BuildingPlacementConvergenceStatus
    proposal_count: int
    iteration_count: int
    accepted_count: int
    spacing_rejected_count: int
    target_window_rejected_count: int
    initial_coverage_ratio: float
    final_coverage_ratio: float
    initial_far: float
    final_far: float
    coverage_target_met: bool
    far_target_met: bool
    unmet_coverage_ratio: float
    unmet_far: float
    excess_coverage_ratio: float
    excess_far: float

    def __post_init__(self) -> None:
        if not isinstance(self.status, BuildingPlacementConvergenceStatus):
            raise BuildingPlacementConvergenceError(
                "status must be a BuildingPlacementConvergenceStatus"
            )
        for field_name in (
            "proposal_count",
            "iteration_count",
            "accepted_count",
            "spacing_rejected_count",
            "target_window_rejected_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if self.iteration_count > self.proposal_count:
            raise BuildingPlacementConvergenceError(
                "iteration_count cannot exceed proposal_count"
            )
        if self.accepted_count > self.iteration_count:
            raise BuildingPlacementConvergenceError(
                "accepted_count cannot exceed iteration_count"
            )
        if (
            self.spacing_rejected_count
            + self.target_window_rejected_count
            + self.accepted_count
            != self.iteration_count
        ):
            raise BuildingPlacementConvergenceError(
                "proposal outcome counts must sum to iteration_count"
            )
        for field_name in (
            "initial_coverage_ratio",
            "final_coverage_ratio",
            "initial_far",
            "final_far",
            "unmet_coverage_ratio",
            "unmet_far",
            "excess_coverage_ratio",
            "excess_far",
        ):
            _require_non_negative_finite(field_name, getattr(self, field_name))
        for field_name in ("coverage_target_met", "far_target_met"):
            if not isinstance(getattr(self, field_name), bool):
                raise BuildingPlacementConvergenceError(
                    f"{field_name} must be a bool"
                )

    @property
    def targets_met(self) -> bool:
        return self.coverage_target_met and self.far_target_met


@dataclass(frozen=True, slots=True)
class BuildingPlacementConvergenceResult:
    """Accepted proposals and bounded convergence diagnostics."""

    working_srid: int
    targets: BuildingPlacementTargets
    baseline: BuildingPlacementBaseline
    accepted: tuple[BuildingPlacementProposal, ...]
    diagnostics: BuildingPlacementConvergenceDiagnostics

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        if not isinstance(self.targets, BuildingPlacementTargets):
            raise BuildingPlacementConvergenceError(
                "targets must be BuildingPlacementTargets"
            )
        if not isinstance(self.baseline, BuildingPlacementBaseline):
            raise BuildingPlacementConvergenceError(
                "baseline must be BuildingPlacementBaseline"
            )
        if not isinstance(self.accepted, tuple):
            raise BuildingPlacementConvergenceError(
                "accepted must be an immutable tuple"
            )
        if any(
            not isinstance(item, BuildingPlacementProposal)
            for item in self.accepted
        ):
            raise BuildingPlacementConvergenceError(
                "accepted must contain BuildingPlacementProposal values"
            )
        if any(item.working_srid != self.working_srid for item in self.accepted):
            raise BuildingPlacementConvergenceError(
                "accepted proposal working_srid must match result working CRS"
            )
        ids = tuple(item.proposal_id for item in self.accepted)
        if len(ids) != len(set(ids)):
            raise BuildingPlacementConvergenceError(
                "accepted proposal ids must be unique"
            )
        if not isinstance(
            self.diagnostics,
            BuildingPlacementConvergenceDiagnostics,
        ):
            raise BuildingPlacementConvergenceError(
                "diagnostics must be BuildingPlacementConvergenceDiagnostics"
            )
        if self.diagnostics.accepted_count != len(self.accepted):
            raise BuildingPlacementConvergenceError(
                "diagnostics accepted_count must match accepted proposals"
            )


class _IncrementalBuildingSpacingIndex:
    """Incrementally index accepted footprints without rebuilding the full tree.

    Shapely STRtree is immutable, so accepted footprints are stored in power-of-two
    immutable chunks. Adding one footprint behaves like a binary counter: equal-sized
    chunks merge into one larger index. Each accepted footprint is therefore reindexed
    at most O(log N) times instead of once for every later acceptance.
    """

    def __init__(
        self,
        *,
        existing_footprints: tuple[PlacedBuildingFootprint, ...],
        working_srid: int,
        policy: BuildingSpacingPolicy,
        max_footprints: int,
        max_candidates: int,
    ) -> None:
        self.working_srid = working_srid
        self.policy = policy
        self.max_footprints = max_footprints
        self.max_candidates = max_candidates
        self._existing_count = len(existing_footprints)
        self._accepted_count = 0
        self._base = (
            BuildingSpacingIndex(
                footprints=existing_footprints,
                working_srid=working_srid,
                policy=policy,
                max_footprints=max_footprints,
                max_candidates=max_candidates,
            )
            if existing_footprints
            else None
        )
        self._chunks: list[tuple[PlacedBuildingFootprint, ...]] = []
        self._indexes: list[BuildingSpacingIndex] = []

    def check(
        self,
        subject: BuildingSpacingSubject,
    ) -> BuildingSpacingHit | None:
        hits: list[BuildingSpacingHit] = []
        if self._base is not None:
            hit = self._base.check(subject)
            if hit is not None:
                hits.append(hit)
        for index in self._indexes:
            hit = index.check(subject)
            if hit is not None:
                hits.append(hit)
        if not hits:
            return None
        return min(
            hits,
            key=lambda hit: (
                0 if hit.overlaps else 1,
                hit.actual_distance_m,
                hit.building_id,
            ),
        )

    def add(self, proposal: BuildingPlacementProposal) -> None:
        if self._existing_count + self._accepted_count >= self.max_footprints:
            raise BuildingPlacementConvergenceError(
                "placement footprint count exceeds spacing index limit"
            )
        chunk = (
            PlacedBuildingFootprint(
                building_id=proposal.proposal_id,
                geometry=proposal.geometry,
                working_srid=self.working_srid,
            ),
        )
        while self._chunks and len(self._chunks[-1]) == len(chunk):
            previous = self._chunks.pop()
            self._indexes.pop()
            chunk = previous + chunk
        self._chunks.append(chunk)
        self._indexes.append(self._build(chunk))
        self._accepted_count += 1

    def _build(
        self,
        footprints: tuple[PlacedBuildingFootprint, ...],
    ) -> BuildingSpacingIndex:
        return BuildingSpacingIndex(
            footprints=footprints,
            working_srid=self.working_srid,
            policy=self.policy,
            max_footprints=self.max_footprints,
            max_candidates=self.max_candidates,
        )


class BuildingPlacementConverger:
    """Greedy bounded placement loop for coverage/FAR target windows.

    Proposals are evaluated once in deterministic priority/id order. The loop never
    performs all-pairs geometry scans: each spacing decision delegates to S08-T08
    ``BuildingSpacingIndex`` instances. Existing footprints are indexed once; newly
    accepted footprints are maintained in power-of-two immutable chunks so each accepted
    footprint is reindexed only O(log N) times.
    """

    def __init__(
        self,
        *,
        working_srid: int,
        spacing_policy: BuildingSpacingPolicy | None = None,
        max_proposals: int = DEFAULT_MAX_CONVERGENCE_PROPOSALS,
        max_iterations: int = DEFAULT_MAX_CONVERGENCE_ITERATIONS,
        max_spacing_footprints: int = DEFAULT_MAX_SPACING_FOOTPRINTS,
        max_spacing_candidates: int = DEFAULT_MAX_SPACING_CANDIDATES,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        self.spacing_policy = (
            spacing_policy if spacing_policy is not None else BuildingSpacingPolicy()
        )
        if not isinstance(self.spacing_policy, BuildingSpacingPolicy):
            raise BuildingPlacementConvergenceError(
                "spacing_policy must be a BuildingSpacingPolicy"
            )
        for field_name, value in (
            ("max_proposals", max_proposals),
            ("max_iterations", max_iterations),
            ("max_spacing_footprints", max_spacing_footprints),
            ("max_spacing_candidates", max_spacing_candidates),
        ):
            _require_positive_int(field_name, value)
        self.max_proposals = max_proposals
        self.max_iterations = max_iterations
        self.max_spacing_footprints = max_spacing_footprints
        self.max_spacing_candidates = max_spacing_candidates

    def converge(
        self,
        proposals: tuple[BuildingPlacementProposal, ...],
        *,
        targets: BuildingPlacementTargets,
        baseline: BuildingPlacementBaseline | None = None,
        existing_footprints: tuple[PlacedBuildingFootprint, ...] = (),
    ) -> BuildingPlacementConvergenceResult:
        resolved_baseline = (
            baseline if baseline is not None else BuildingPlacementBaseline()
        )
        self._validate_inputs(
            proposals=proposals,
            targets=targets,
            baseline=resolved_baseline,
            existing_footprints=existing_footprints,
        )

        ordered = tuple(
            sorted(
                proposals,
                key=lambda item: (-item.priority, item.proposal_id),
            )
        )
        accepted: list[BuildingPlacementProposal] = []
        iteration_count = 0
        spacing_rejected_count = 0
        target_window_rejected_count = 0

        coverage_area = resolved_baseline.coverage_area_m2
        planning_floor_area = resolved_baseline.planning_floor_area_m2
        initial_coverage = coverage_area / targets.site_area_m2
        initial_far = planning_floor_area / targets.site_area_m2

        if _targets_met(
            coverage_ratio=initial_coverage,
            far=initial_far,
            targets=targets,
        ):
            return self._result(
                targets=targets,
                baseline=resolved_baseline,
                proposals=ordered,
                accepted=(),
                status=BuildingPlacementConvergenceStatus.CONVERGED,
                iteration_count=0,
                spacing_rejected_count=0,
                target_window_rejected_count=0,
                initial_coverage=initial_coverage,
                initial_far=initial_far,
                final_coverage=initial_coverage,
                final_far=initial_far,
            )

        spacing_index = self._incremental_spacing_index(
            existing_footprints=existing_footprints,
        )
        status = BuildingPlacementConvergenceStatus.CANDIDATES_EXHAUSTED

        for proposal in ordered:
            if iteration_count >= self.max_iterations:
                status = BuildingPlacementConvergenceStatus.MAX_ITERATIONS
                break
            iteration_count += 1

            spacing_hit = spacing_index.check(
                BuildingSpacingSubject(
                    geometry=proposal.geometry,
                    working_srid=self.working_crs.srid,
                )
            )
            if spacing_hit is not None:
                spacing_rejected_count += 1
                continue

            next_coverage_area = coverage_area + proposal.footprint_area_m2
            next_floor_area = (
                planning_floor_area + proposal.planning_floor_area_m2
            )
            next_coverage = next_coverage_area / targets.site_area_m2
            next_far = next_floor_area / targets.site_area_m2

            if (
                next_coverage > targets.coverage_upper_bound + _METRIC_EPSILON
                or next_far > targets.far_upper_bound + _METRIC_EPSILON
            ):
                target_window_rejected_count += 1
                continue

            accepted.append(proposal)
            coverage_area = next_coverage_area
            planning_floor_area = next_floor_area
            spacing_index.add(proposal)

            if _targets_met(
                coverage_ratio=next_coverage,
                far=next_far,
                targets=targets,
            ):
                status = BuildingPlacementConvergenceStatus.CONVERGED
                break

        final_coverage = coverage_area / targets.site_area_m2
        final_far = planning_floor_area / targets.site_area_m2
        return self._result(
            targets=targets,
            baseline=resolved_baseline,
            proposals=ordered,
            accepted=tuple(accepted),
            status=status,
            iteration_count=iteration_count,
            spacing_rejected_count=spacing_rejected_count,
            target_window_rejected_count=target_window_rejected_count,
            initial_coverage=initial_coverage,
            initial_far=initial_far,
            final_coverage=final_coverage,
            final_far=final_far,
        )

    def _validate_inputs(
        self,
        *,
        proposals: tuple[BuildingPlacementProposal, ...],
        targets: BuildingPlacementTargets,
        baseline: BuildingPlacementBaseline,
        existing_footprints: tuple[PlacedBuildingFootprint, ...],
    ) -> None:
        if not isinstance(proposals, tuple):
            raise BuildingPlacementConvergenceError(
                "proposals must be an immutable tuple"
            )
        if any(
            not isinstance(item, BuildingPlacementProposal)
            for item in proposals
        ):
            raise BuildingPlacementConvergenceError(
                "proposals must contain BuildingPlacementProposal values"
            )
        if len(proposals) > self.max_proposals:
            raise BuildingPlacementConvergenceError(
                "placement proposal limit exceeded: "
                f"{len(proposals)} > {self.max_proposals}"
            )
        if any(item.working_srid != self.working_crs.srid for item in proposals):
            raise BuildingPlacementConvergenceError(
                "proposal working_srid must match converger working CRS"
            )
        proposal_ids = tuple(item.proposal_id for item in proposals)
        if len(proposal_ids) != len(set(proposal_ids)):
            raise BuildingPlacementConvergenceError(
                "proposal ids must be unique"
            )

        if not isinstance(targets, BuildingPlacementTargets):
            raise BuildingPlacementConvergenceError(
                "targets must be BuildingPlacementTargets"
            )
        if not isinstance(baseline, BuildingPlacementBaseline):
            raise BuildingPlacementConvergenceError(
                "baseline must be BuildingPlacementBaseline"
            )
        if baseline.coverage_area_m2 > targets.site_area_m2 + _METRIC_EPSILON:
            raise BuildingPlacementConvergenceError(
                "baseline coverage_area_m2 cannot exceed site_area_m2"
            )

        if not isinstance(existing_footprints, tuple):
            raise BuildingPlacementConvergenceError(
                "existing_footprints must be an immutable tuple"
            )
        if any(
            not isinstance(item, PlacedBuildingFootprint)
            for item in existing_footprints
        ):
            raise BuildingPlacementConvergenceError(
                "existing_footprints must contain PlacedBuildingFootprint values"
            )
        if any(
            item.working_srid != self.working_crs.srid
            for item in existing_footprints
        ):
            raise BuildingPlacementConvergenceError(
                "existing footprint working_srid must match converger working CRS"
            )
        existing_ids = tuple(item.building_id for item in existing_footprints)
        if len(existing_ids) != len(set(existing_ids)):
            raise BuildingPlacementConvergenceError(
                "existing building ids must be unique"
            )
        if set(existing_ids).intersection(proposal_ids):
            raise BuildingPlacementConvergenceError(
                "proposal ids must not collide with existing building ids"
            )

        maximum_possible_footprints = len(existing_footprints) + min(
            len(proposals),
            self.max_iterations,
        )
        if maximum_possible_footprints > self.max_spacing_footprints:
            raise BuildingPlacementConvergenceError(
                "maximum placement footprint count exceeds spacing index limit: "
                f"{maximum_possible_footprints} > {self.max_spacing_footprints}"
            )

    def _incremental_spacing_index(
        self,
        *,
        existing_footprints: tuple[PlacedBuildingFootprint, ...],
    ) -> _IncrementalBuildingSpacingIndex:
        return _IncrementalBuildingSpacingIndex(
            existing_footprints=existing_footprints,
            working_srid=self.working_crs.srid,
            policy=self.spacing_policy,
            max_footprints=self.max_spacing_footprints,
            max_candidates=self.max_spacing_candidates,
        )

    def _result(
        self,
        *,
        targets: BuildingPlacementTargets,
        baseline: BuildingPlacementBaseline,
        proposals: tuple[BuildingPlacementProposal, ...],
        accepted: tuple[BuildingPlacementProposal, ...],
        status: BuildingPlacementConvergenceStatus,
        iteration_count: int,
        spacing_rejected_count: int,
        target_window_rejected_count: int,
        initial_coverage: float,
        initial_far: float,
        final_coverage: float,
        final_far: float,
    ) -> BuildingPlacementConvergenceResult:
        coverage_met = _within(
            final_coverage,
            targets.coverage_lower_bound,
            targets.coverage_upper_bound,
        )
        far_met = _within(
            final_far,
            targets.far_lower_bound,
            targets.far_upper_bound,
        )
        diagnostics = BuildingPlacementConvergenceDiagnostics(
            status=status,
            proposal_count=len(proposals),
            iteration_count=iteration_count,
            accepted_count=len(accepted),
            spacing_rejected_count=spacing_rejected_count,
            target_window_rejected_count=target_window_rejected_count,
            initial_coverage_ratio=initial_coverage,
            final_coverage_ratio=final_coverage,
            initial_far=initial_far,
            final_far=final_far,
            coverage_target_met=coverage_met,
            far_target_met=far_met,
            unmet_coverage_ratio=max(
                0.0,
                targets.coverage_lower_bound - final_coverage,
            ),
            unmet_far=max(0.0, targets.far_lower_bound - final_far),
            excess_coverage_ratio=max(
                0.0,
                final_coverage - targets.coverage_upper_bound,
            ),
            excess_far=max(0.0, final_far - targets.far_upper_bound),
        )
        return BuildingPlacementConvergenceResult(
            working_srid=self.working_crs.srid,
            targets=targets,
            baseline=baseline,
            accepted=accepted,
            diagnostics=diagnostics,
        )


def _targets_met(
    *,
    coverage_ratio: float,
    far: float,
    targets: BuildingPlacementTargets,
) -> bool:
    return _within(
        coverage_ratio,
        targets.coverage_lower_bound,
        targets.coverage_upper_bound,
    ) and _within(
        far,
        targets.far_lower_bound,
        targets.far_upper_bound,
    )


def _within(value: float, lower: float, upper: float) -> bool:
    return (
        value >= lower - _METRIC_EPSILON
        and value <= upper + _METRIC_EPSILON
    )


def _require_polygonal_geometry(
    field_name: str,
    geometry: BaseGeometry,
) -> None:
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise BuildingPlacementConvergenceError(
            f"{field_name} must be Polygon or MultiPolygon"
        )
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise BuildingPlacementConvergenceError(
            f"{field_name} must be non-empty, valid and 2D"
        )
    area = float(geometry.area)
    if not math.isfinite(area) or area <= 0.0:
        raise BuildingPlacementConvergenceError(
            f"{field_name} must have positive finite area"
        )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise BuildingPlacementConvergenceError(
            f"invalid {field_name}: {value!r}"
        )


def _require_ratio(field_name: str, value: float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number > 1.0:
        raise BuildingPlacementConvergenceError(
            f"{field_name} must be <= 1.0"
        )
    return number


def _require_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuildingPlacementConvergenceError(
            f"{field_name} must be a finite number"
        )
    number = float(value)
    if not math.isfinite(number):
        raise BuildingPlacementConvergenceError(
            f"{field_name} must be a finite number"
        )
    return number


def _require_non_negative_finite(field_name: str, value: float) -> float:
    number = _require_finite(field_name, value)
    if number < 0.0:
        raise BuildingPlacementConvergenceError(
            f"{field_name} must be non-negative"
        )
    return number


def _require_positive_finite(field_name: str, value: float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number <= 0.0:
        raise BuildingPlacementConvergenceError(
            f"{field_name} must be greater than zero"
        )
    return number


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BuildingPlacementConvergenceError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BuildingPlacementConvergenceError(
            f"{field_name} must be a non-negative integer"
        )
