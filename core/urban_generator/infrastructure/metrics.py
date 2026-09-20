from __future__ import annotations

import math
from dataclasses import dataclass

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import RawMetricId
from core.urban_generator.infrastructure.accessibility import (
    InfrastructureAccessibilityBatchResult,
    InfrastructureAccessibilityMode,
    InfrastructureAccessibilityResult,
)
from core.urban_generator.infrastructure.config import InfrastructureType
from core.urban_generator.infrastructure.demand import (
    BlockInfrastructureDemand,
    InfrastructureDemandSummary,
    UnmetDemandResult,
)
from core.urban_generator.infrastructure.network_snap import (
    ExistingInfrastructureFacilityRef,
)
from core.urban_generator.infrastructure.placement import (
    InfrastructureGreedyPlacementState,
)

DEFAULT_MAX_INFRASTRUCTURE_METRIC_TYPES = 10_000


class InfrastructureMetricsError(ValueError):
    """Raised when S10-T11 raw metric inputs violate the metric contract."""


@dataclass(frozen=True, slots=True)
class InfrastructureAgeCoverage:
    """Population-linked coverage for one demographic age-group code."""

    demographic_group: str
    population: float
    covered_population: float
    coverage_ratio: float

    def __post_init__(self) -> None:
        _require_id("demographic_group", self.demographic_group)
        population = _require_non_negative_finite(
            "population",
            self.population,
        )
        covered = _require_non_negative_finite(
            "covered_population",
            self.covered_population,
        )
        if covered > population and not math.isclose(
            covered,
            population,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise InfrastructureMetricsError(
                "covered_population cannot exceed population"
            )
        ratio = _require_ratio("coverage_ratio", self.coverage_ratio)
        expected = covered / population if population > 0.0 else 0.0
        if not math.isclose(
            ratio,
            expected,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise InfrastructureMetricsError(
                "coverage_ratio must equal covered_population / population"
            )
        object.__setattr__(self, "population", population)
        object.__setattr__(self, "covered_population", min(covered, population))
        object.__setattr__(self, "coverage_ratio", expected)


@dataclass(frozen=True, slots=True)
class InfrastructureRawMetricValue:
    """One canonical RawMetricId value produced directly by S10 infrastructure."""

    metric_id: RawMetricId
    scalar_value: float | None = None
    age_coverage: tuple[InfrastructureAgeCoverage, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, RawMetricId):
            raise InfrastructureMetricsError("metric_id must be RawMetricId")
        if self.metric_id is RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE:
            if self.scalar_value is not None:
                raise InfrastructureMetricsError(
                    "age-specific coverage must not carry scalar_value"
                )
            if not isinstance(self.age_coverage, tuple) or not self.age_coverage:
                raise InfrastructureMetricsError(
                    "age-specific coverage requires a non-empty distribution"
                )
            if any(
                not isinstance(item, InfrastructureAgeCoverage)
                for item in self.age_coverage
            ):
                raise InfrastructureMetricsError(
                    "age_coverage must contain InfrastructureAgeCoverage values"
                )
            groups = tuple(item.demographic_group for item in self.age_coverage)
            if groups != tuple(sorted(groups)) or len(groups) != len(set(groups)):
                raise InfrastructureMetricsError(
                    "age_coverage must be canonically sorted and unique"
                )
            return

        if self.age_coverage:
            raise InfrastructureMetricsError(
                "scalar infrastructure metric must not carry age_coverage"
            )
        if self.scalar_value is None:
            raise InfrastructureMetricsError(
                "scalar infrastructure metric requires scalar_value"
            )
        scalar = _require_non_negative_finite(
            "scalar_value",
            self.scalar_value,
        )
        if self.metric_id in {
            RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
            RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
        }:
            scalar = _require_ratio("scalar_value", scalar)
        object.__setattr__(self, "scalar_value", scalar)


@dataclass(frozen=True, slots=True)
class InfrastructureMetricsDiagnostics:
    infrastructure_type_count: int
    demand_item_count: int
    accepted_generated_facility_count: int
    existing_distance_sample_count: int
    generated_distance_sample_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "infrastructure_type_count",
            "demand_item_count",
            "accepted_generated_facility_count",
            "existing_distance_sample_count",
            "generated_distance_sample_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))


@dataclass(frozen=True, slots=True)
class InfrastructureMetricsResult:
    """Canonical S10-T11 raw infrastructure metrics plus audit diagnostics."""

    raw_metrics: tuple[InfrastructureRawMetricValue, ...]
    diagnostics: InfrastructureMetricsDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.raw_metrics, tuple):
            raise InfrastructureMetricsError(
                "raw_metrics must be an immutable tuple"
            )
        if any(
            not isinstance(item, InfrastructureRawMetricValue)
            for item in self.raw_metrics
        ):
            raise InfrastructureMetricsError(
                "raw_metrics must contain InfrastructureRawMetricValue values"
            )
        expected_ids = _INFRASTRUCTURE_RAW_METRIC_IDS
        actual_ids = tuple(item.metric_id for item in self.raw_metrics)
        if actual_ids != expected_ids:
            raise InfrastructureMetricsError(
                "raw_metrics must contain every canonical infrastructure metric "
                "in canonical order"
            )
        if not isinstance(self.diagnostics, InfrastructureMetricsDiagnostics):
            raise InfrastructureMetricsError(
                "diagnostics must be InfrastructureMetricsDiagnostics"
            )

    def require(self, metric_id: RawMetricId) -> InfrastructureRawMetricValue:
        if metric_id not in _INFRASTRUCTURE_RAW_METRIC_IDS:
            raise InfrastructureMetricsError(
                f"not an infrastructure RawMetricId: {metric_id!r}"
            )
        return self.raw_metrics[_INFRASTRUCTURE_RAW_METRIC_IDS.index(metric_id)]


@dataclass(frozen=True, slots=True)
class _DistanceSample:
    distance_m: float
    served_demand: float

    def __post_init__(self) -> None:
        distance = _require_non_negative_finite("distance_m", self.distance_m)
        served = _require_positive_finite("served_demand", self.served_demand)
        object.__setattr__(self, "distance_m", distance)
        object.__setattr__(self, "served_demand", served)


class InfrastructureMetricsBuilder:
    """Compute canonical S10 raw metrics without any routing/backend calls."""

    def __init__(
        self,
        *,
        max_types: int = DEFAULT_MAX_INFRASTRUCTURE_METRIC_TYPES,
    ) -> None:
        _require_positive_int("max_types", max_types)
        if max_types > DEFAULT_MAX_INFRASTRUCTURE_METRIC_TYPES:
            raise InfrastructureMetricsError(
                "max_types exceeds infrastructure metric hard limit"
            )
        self.max_types = max_types

    def build(
        self,
        unmet_demand: UnmetDemandResult,
        *,
        infrastructure_types: tuple[InfrastructureType, ...],
        placements: tuple[InfrastructureGreedyPlacementState, ...],
        existing_accessibility: tuple[InfrastructureAccessibilityBatchResult, ...],
    ) -> InfrastructureMetricsResult:
        if not isinstance(unmet_demand, UnmetDemandResult):
            raise InfrastructureMetricsError(
                "unmet_demand must be UnmetDemandResult"
            )
        if not isinstance(infrastructure_types, tuple):
            raise InfrastructureMetricsError(
                "infrastructure_types must be an immutable tuple"
            )
        if not isinstance(placements, tuple):
            raise InfrastructureMetricsError(
                "placements must be an immutable tuple"
            )
        if not isinstance(existing_accessibility, tuple):
            raise InfrastructureMetricsError(
                "existing_accessibility must be an immutable tuple"
            )
        if any(
            not isinstance(item, InfrastructureType)
            for item in infrastructure_types
        ):
            raise InfrastructureMetricsError(
                "infrastructure_types must contain InfrastructureType values"
            )
        if any(
            not isinstance(item, InfrastructureGreedyPlacementState)
            for item in placements
        ):
            raise InfrastructureMetricsError(
                "placements must contain InfrastructureGreedyPlacementState values"
            )
        if any(
            not isinstance(item, InfrastructureAccessibilityBatchResult)
            for item in existing_accessibility
        ):
            raise InfrastructureMetricsError(
                "existing_accessibility must contain "
                "InfrastructureAccessibilityBatchResult values"
            )

        types = tuple(sorted(infrastructure_types, key=lambda item: item.code))
        type_codes = tuple(item.code for item in types)
        if not type_codes:
            raise InfrastructureMetricsError(
                "empty infrastructure metric input policy belongs to UG-AI-037"
            )
        if len(type_codes) > self.max_types:
            raise InfrastructureMetricsError(
                "infrastructure metric type limit exceeded"
            )
        if len(type_codes) != len(set(type_codes)):
            raise InfrastructureMetricsError(
                "infrastructure type codes must be unique"
            )

        summaries = self._align_summaries(unmet_demand, type_codes)
        placement_by_code = self._align_placements(placements, type_codes)
        existing_by_code = self._align_existing_accessibility(
            existing_accessibility,
            type_codes,
        )
        demands_by_code = self._demands_by_type(unmet_demand, type_codes)

        final_remaining: dict[tuple[str, str], float] = {}
        distance_samples: list[_DistanceSample] = []
        existing_sample_count = 0
        generated_sample_count = 0
        accepted_count = 0
        total_capacity = 0.0

        for infrastructure_type in types:
            code = infrastructure_type.code
            demands = demands_by_code[code]
            placement = placement_by_code[code]
            existing_batch = existing_by_code[code]
            summary = summaries[code]

            self._validate_type_alignment(
                demands=demands,
                placement=placement,
                existing_batch=existing_batch,
                infrastructure_type=infrastructure_type,
            )
            type_final, existing_samples, generated_samples = (
                self._replay_served_demand(
                    demands=demands,
                    placement=placement,
                    existing_batch=existing_batch,
                    infrastructure_type=infrastructure_type,
                )
            )
            final_remaining.update(type_final)
            distance_samples.extend(existing_samples)
            distance_samples.extend(generated_samples)
            existing_sample_count += len(existing_samples)
            generated_sample_count += len(generated_samples)
            accepted_count += len(placement.accepted_facilities)
            total_capacity += summary.existing_capacity
            total_capacity += (
                len(placement.accepted_facilities)
                * infrastructure_type.capacity
            )

        population_total = 0.0
        population_covered = 0.0
        age_totals: dict[str, tuple[float, float]] = {}
        total_unmet = 0.0
        total_served = 0.0

        for demand in unmet_demand.demands:
            remaining = final_remaining[demand.key]
            total_unmet += remaining
            served = max(demand.gross_demand - remaining, 0.0)
            total_served += served

            if demand.demographic_signal not in {
                DemographicDemandCategory.TOTAL_POPULATION,
                DemographicDemandCategory.AGE_GROUP,
            }:
                continue
            covered_signal = _covered_source_signal(
                demand,
                remaining_demand=remaining,
            )
            population_total += demand.source_signal_value
            population_covered += covered_signal

            if demand.demographic_signal is DemographicDemandCategory.AGE_GROUP:
                assert demand.demographic_group is not None
                group_total, group_covered = age_totals.get(
                    demand.demographic_group,
                    (0.0, 0.0),
                )
                age_totals[demand.demographic_group] = (
                    group_total + demand.source_signal_value,
                    group_covered + covered_signal,
                )

        if population_total <= 0.0:
            raise InfrastructureMetricsError(
                "population coverage denominator policy belongs to UG-AI-037"
            )
        if not age_totals:
            raise InfrastructureMetricsError(
                "age-specific coverage empty-data policy belongs to UG-AI-037"
            )
        if not distance_samples:
            raise InfrastructureMetricsError(
                "network distance empty-data policy belongs to UG-AI-037"
            )
        if total_capacity <= 0.0:
            raise InfrastructureMetricsError(
                "capacity utilization empty-capacity policy belongs to UG-AI-037"
            )

        utilization = total_served / total_capacity
        if utilization > 1.0 and not math.isclose(
            utilization,
            1.0,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise InfrastructureMetricsError(
                "served demand cannot exceed existing + generated capacity"
            )

        age_coverage = tuple(
            InfrastructureAgeCoverage(
                demographic_group=group,
                population=population,
                covered_population=covered,
                coverage_ratio=covered / population,
            )
            for group, (population, covered) in sorted(age_totals.items())
            if population > 0.0
        )
        if not age_coverage:
            raise InfrastructureMetricsError(
                "age-specific zero-population policy belongs to UG-AI-037"
            )

        raw_metrics = (
            InfrastructureRawMetricValue(
                metric_id=RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
                scalar_value=population_covered / population_total,
            ),
            InfrastructureRawMetricValue(
                metric_id=RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE,
                age_coverage=age_coverage,
            ),
            InfrastructureRawMetricValue(
                metric_id=RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M,
                scalar_value=_weighted_percentile(distance_samples, 0.50),
            ),
            InfrastructureRawMetricValue(
                metric_id=RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M,
                scalar_value=_weighted_percentile(distance_samples, 0.90),
            ),
            InfrastructureRawMetricValue(
                metric_id=RawMetricId.INFRASTRUCTURE_UNMET_DEMAND,
                scalar_value=total_unmet,
            ),
            InfrastructureRawMetricValue(
                metric_id=RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
                scalar_value=min(utilization, 1.0),
            ),
        )
        return InfrastructureMetricsResult(
            raw_metrics=raw_metrics,
            diagnostics=InfrastructureMetricsDiagnostics(
                infrastructure_type_count=len(types),
                demand_item_count=len(unmet_demand.demands),
                accepted_generated_facility_count=accepted_count,
                existing_distance_sample_count=existing_sample_count,
                generated_distance_sample_count=generated_sample_count,
            ),
        )

    @staticmethod
    def _align_summaries(
        unmet_demand: UnmetDemandResult,
        type_codes: tuple[str, ...],
    ) -> dict[str, InfrastructureDemandSummary]:
        actual = tuple(
            item.infrastructure_type_code for item in unmet_demand.summaries
        )
        if actual != type_codes:
            raise InfrastructureMetricsError(
                "unmet-demand summaries must match infrastructure types exactly"
            )
        return {
            item.infrastructure_type_code: item
            for item in unmet_demand.summaries
        }

    @staticmethod
    def _align_placements(
        placements: tuple[InfrastructureGreedyPlacementState, ...],
        type_codes: tuple[str, ...],
    ) -> dict[str, InfrastructureGreedyPlacementState]:
        ordered = tuple(
            sorted(placements, key=lambda item: item.infrastructure_type_code)
        )
        actual = tuple(item.infrastructure_type_code for item in ordered)
        if actual != type_codes:
            raise InfrastructureMetricsError(
                "placements must match infrastructure types exactly"
            )
        return {
            item.infrastructure_type_code: item
            for item in ordered
        }

    @staticmethod
    def _align_existing_accessibility(
        batches: tuple[InfrastructureAccessibilityBatchResult, ...],
        type_codes: tuple[str, ...],
    ) -> dict[str, InfrastructureAccessibilityBatchResult]:
        ordered = tuple(
            sorted(batches, key=lambda item: item.infrastructure_type_code)
        )
        actual = tuple(item.infrastructure_type_code for item in ordered)
        if actual != type_codes:
            raise InfrastructureMetricsError(
                "existing accessibility must match infrastructure types exactly"
            )
        if any(
            item.mode is not InfrastructureAccessibilityMode.EXISTING_FACILITY
            for item in ordered
        ):
            raise InfrastructureMetricsError(
                "existing accessibility batches must use existing_facility mode"
            )
        return {
            item.infrastructure_type_code: item
            for item in ordered
        }

    @staticmethod
    def _demands_by_type(
        unmet_demand: UnmetDemandResult,
        type_codes: tuple[str, ...],
    ) -> dict[str, tuple[BlockInfrastructureDemand, ...]]:
        grouped = {
            code: tuple(
                item
                for item in unmet_demand.demands
                if item.infrastructure_type_code == code
            )
            for code in type_codes
        }
        if any(not values for values in grouped.values()):
            raise InfrastructureMetricsError(
                "every infrastructure type requires at least one demand item"
            )
        return grouped

    @staticmethod
    def _validate_type_alignment(
        *,
        demands: tuple[BlockInfrastructureDemand, ...],
        placement: InfrastructureGreedyPlacementState,
        existing_batch: InfrastructureAccessibilityBatchResult,
        infrastructure_type: InfrastructureType,
    ) -> None:
        code = infrastructure_type.code
        demand_keys = tuple(item.key for item in demands)
        placement_keys = tuple(item.key for item in placement.remaining_demand)
        if placement_keys != demand_keys:
            raise InfrastructureMetricsError(
                "placement demand state must match unmet-demand rows exactly"
            )
        for demand, state_item in zip(
            demands,
            placement.remaining_demand,
            strict=True,
        ):
            if not math.isclose(
                demand.unmet_demand,
                state_item.initial_demand,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise InfrastructureMetricsError(
                    "placement initial demand must match T03 unmet demand"
                )
        if placement.infrastructure_type_code != code:
            raise InfrastructureMetricsError(
                "placement infrastructure type must match InfrastructureType"
            )
        if existing_batch.infrastructure_type_code != code:
            raise InfrastructureMetricsError(
                "existing accessibility type must match InfrastructureType"
            )
        if existing_batch.snapshot_id != placement.snapshot_id:
            raise InfrastructureMetricsError(
                "T07 existing accessibility and T08 placement snapshots must match"
            )

        outcome_keys = {
            item.demand_ref.key for item in existing_batch.reachable
        }
        outcome_keys.update(
            item.demand_ref.key for item in existing_batch.unavailable
        )
        if outcome_keys != set(demand_keys):
            raise InfrastructureMetricsError(
                "existing accessibility must contain one outcome per demand row"
            )

    @staticmethod
    def _replay_served_demand(
        *,
        demands: tuple[BlockInfrastructureDemand, ...],
        placement: InfrastructureGreedyPlacementState,
        existing_batch: InfrastructureAccessibilityBatchResult,
        infrastructure_type: InfrastructureType,
    ) -> tuple[
        dict[tuple[str, str], float],
        tuple[_DistanceSample, ...],
        tuple[_DistanceSample, ...],
    ]:
        remaining = {
            demand.key: demand.unmet_demand for demand in demands
        }
        existing_reachable = {
            item.demand_ref.key: item
            for item in existing_batch.reachable
            if isinstance(item, InfrastructureAccessibilityResult)
            and isinstance(
                item.facility_site_ref,
                ExistingInfrastructureFacilityRef,
            )
        }
        existing_samples: list[_DistanceSample] = []
        for demand in demands:
            if demand.served_demand <= 0.0:
                continue
            reachable = existing_reachable.get(demand.key)
            if reachable is None:
                raise InfrastructureMetricsError(
                    "positive existing served demand requires a reachable T07 "
                    "existing-facility outcome"
                )
            existing_samples.append(
                _DistanceSample(
                    distance_m=reachable.distance_m,
                    served_demand=demand.served_demand,
                )
            )

        cache_by_key = {
            item.candidate_ref.key: item for item in placement.coverage_cache
        }
        generated_samples: list[_DistanceSample] = []
        for accepted in placement.accepted_facilities:
            cache = cache_by_key[accepted.candidate_ref.key]
            capacity_remaining = infrastructure_type.capacity
            for row in cache.accessibility:
                if capacity_remaining <= 0.0:
                    break
                key = row.demand_ref.key
                current = remaining[key]
                served = min(current, capacity_remaining)
                if served <= 0.0:
                    continue
                remaining[key] = max(current - served, 0.0)
                capacity_remaining = max(
                    capacity_remaining - served,
                    0.0,
                )
                generated_samples.append(
                    _DistanceSample(
                        distance_m=row.distance_m,
                        served_demand=served,
                    )
                )

        expected = {
            item.key: item.remaining_demand
            for item in placement.remaining_demand
        }
        for key, value in remaining.items():
            if not math.isclose(
                value,
                expected[key],
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise InfrastructureMetricsError(
                    "replayed generated service must match final T08 remaining demand"
                )
        return (
            remaining,
            tuple(existing_samples),
            tuple(generated_samples),
        )


def _covered_source_signal(
    demand: BlockInfrastructureDemand,
    *,
    remaining_demand: float,
) -> float:
    if demand.gross_demand <= 0.0:
        return 0.0
    served = max(demand.gross_demand - remaining_demand, 0.0)
    coverage = min(served / demand.gross_demand, 1.0)
    return demand.source_signal_value * coverage


def _weighted_percentile(
    samples: list[_DistanceSample],
    quantile: float,
) -> float:
    if not 0.0 < quantile <= 1.0:
        raise InfrastructureMetricsError("quantile must be inside (0, 1]")
    ordered = sorted(samples, key=lambda item: item.distance_m)
    total_weight = math.fsum(item.served_demand for item in ordered)
    threshold = total_weight * quantile
    cumulative = 0.0
    for item in ordered:
        cumulative += item.served_demand
        if cumulative >= threshold:
            return item.distance_m
    return ordered[-1].distance_m


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InfrastructureMetricsError(
            f"{field_name} must be a non-empty string"
        )
    if "\n" in value or "\r" in value:
        raise InfrastructureMetricsError(
            f"{field_name} must not contain line breaks"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InfrastructureMetricsError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InfrastructureMetricsError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(
    field_name: str,
    value: int | float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InfrastructureMetricsError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise InfrastructureMetricsError(
            f"{field_name} must be a finite non-negative number"
        )
    return number


def _require_positive_finite(
    field_name: str,
    value: int | float,
) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number <= 0.0:
        raise InfrastructureMetricsError(
            f"{field_name} must be greater than zero"
        )
    return number


def _require_ratio(field_name: str, value: int | float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number > 1.0 and not math.isclose(
        number,
        1.0,
        rel_tol=1e-12,
        abs_tol=1e-9,
    ):
        raise InfrastructureMetricsError(
            f"{field_name} must be inside 0..1"
        )
    return min(number, 1.0)


_INFRASTRUCTURE_RAW_METRIC_IDS = (
    RawMetricId.INFRASTRUCTURE_POPULATION_COVERAGE_RATIO,
    RawMetricId.INFRASTRUCTURE_AGE_SPECIFIC_COVERAGE,
    RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P50_M,
    RawMetricId.INFRASTRUCTURE_NETWORK_DISTANCE_P90_M,
    RawMetricId.INFRASTRUCTURE_UNMET_DEMAND,
    RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
)
