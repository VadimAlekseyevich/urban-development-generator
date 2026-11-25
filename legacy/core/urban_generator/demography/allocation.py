from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from core.urban_generator.demography.capacity import (
    ResidentialBuildingCapacity,
    ResidentialCapacityResult,
)
from core.urban_generator.demography.config import (
    DemographicScenario,
    PopulationTargetKind,
)

DEFAULT_MAX_POPULATION_ALLOCATION_BUILDINGS = 100_000


class PopulationAllocationError(ValueError):
    """Raised when S09-T03 population allocation violates the contract."""


class PopulationAllocationStatus(StrEnum):
    """Outcome of generated-resident allocation against target and capacity."""

    TARGET_MET = "TARGET_MET"
    CAPACITY_EXHAUSTED = "CAPACITY_EXHAUSTED"
    BASELINE_EXCEEDS_TARGET = "BASELINE_EXCEEDS_TARGET"


@dataclass(frozen=True, slots=True)
class BuildingPopulationAllocation:
    """Integer generated residents assigned to one building."""

    building_id: str
    resident_capacity: float
    integer_capacity: int
    residents: int
    utilization_ratio: float

    def __post_init__(self) -> None:
        if not isinstance(self.building_id, str) or not self.building_id:
            raise PopulationAllocationError(
                "building_id must be a non-empty string"
            )
        capacity = _require_non_negative_finite(
            "resident_capacity",
            self.resident_capacity,
        )
        _require_non_negative_int("integer_capacity", self.integer_capacity)
        _require_non_negative_int("residents", self.residents)
        utilization = _require_ratio(
            "utilization_ratio",
            self.utilization_ratio,
        )

        expected_integer_capacity = _integer_capacity(capacity)
        if self.integer_capacity != expected_integer_capacity:
            raise PopulationAllocationError(
                "integer_capacity must equal floor(resident_capacity)"
            )
        if self.residents > self.integer_capacity:
            raise PopulationAllocationError(
                "residents cannot exceed integer_capacity"
            )

        expected_utilization = (
            self.residents / self.integer_capacity
            if self.integer_capacity > 0
            else 0.0
        )
        if not math.isclose(
            utilization,
            expected_utilization,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise PopulationAllocationError(
                "utilization_ratio must match residents / integer_capacity"
            )
        object.__setattr__(self, "resident_capacity", capacity)
        object.__setattr__(self, "utilization_ratio", utilization)


@dataclass(frozen=True, slots=True)
class PopulationAllocationDiagnostics:
    """Resolved population target and explicit capacity shortfall diagnostics."""

    status: PopulationAllocationStatus
    target_total_population: int
    baseline_population: int
    generated_population_target: int
    allocatable_generated_capacity: int
    allocated_generated_population: int
    unmet_generated_population: int
    baseline_excess_population: int

    def __post_init__(self) -> None:
        if not isinstance(self.status, PopulationAllocationStatus):
            raise PopulationAllocationError(
                "status must be a PopulationAllocationStatus"
            )
        for field_name in (
            "target_total_population",
            "baseline_population",
            "generated_population_target",
            "allocatable_generated_capacity",
            "allocated_generated_population",
            "unmet_generated_population",
            "baseline_excess_population",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))

        expected_generated_target = max(
            0,
            self.target_total_population - self.baseline_population,
        )
        expected_baseline_excess = max(
            0,
            self.baseline_population - self.target_total_population,
        )
        if self.generated_population_target != expected_generated_target:
            raise PopulationAllocationError(
                "generated_population_target must equal max(target - baseline, 0)"
            )
        if self.baseline_excess_population != expected_baseline_excess:
            raise PopulationAllocationError(
                "baseline_excess_population must equal max(baseline - target, 0)"
            )
        if (
            self.allocated_generated_population
            > self.allocatable_generated_capacity
        ):
            raise PopulationAllocationError(
                "allocated population cannot exceed allocatable capacity"
            )
        if (
            self.allocated_generated_population
            > self.generated_population_target
        ):
            raise PopulationAllocationError(
                "allocated population cannot exceed generated target"
            )
        if self.unmet_generated_population != (
            self.generated_population_target
            - self.allocated_generated_population
        ):
            raise PopulationAllocationError(
                "unmet population must equal target minus allocated population"
            )

        expected_status = _allocation_status(
            generated_target=self.generated_population_target,
            allocated=self.allocated_generated_population,
            baseline_excess=self.baseline_excess_population,
        )
        if self.status is not expected_status:
            raise PopulationAllocationError(
                "allocation status is inconsistent with target diagnostics"
            )


@dataclass(frozen=True, slots=True)
class PopulationAllocationResult:
    """Canonical S09-T03 integer allocation with scenario provenance."""

    scenario_version: str
    scenario_fingerprint: str
    allocations: tuple[BuildingPopulationAllocation, ...]
    diagnostics: PopulationAllocationDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_version, str) or not self.scenario_version:
            raise PopulationAllocationError(
                "scenario_version must be a non-empty string"
            )
        if (
            not isinstance(self.scenario_fingerprint, str)
            or len(self.scenario_fingerprint) != 64
            or any(ch not in "0123456789abcdef" for ch in self.scenario_fingerprint)
        ):
            raise PopulationAllocationError(
                "scenario_fingerprint must be a lowercase SHA-256 hex digest"
            )
        if not isinstance(self.allocations, tuple):
            raise PopulationAllocationError(
                "allocations must be an immutable tuple"
            )
        if any(
            not isinstance(item, BuildingPopulationAllocation)
            for item in self.allocations
        ):
            raise PopulationAllocationError(
                "allocations must contain BuildingPopulationAllocation values"
            )
        ids = tuple(item.building_id for item in self.allocations)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise PopulationAllocationError(
                "allocation building ids must be sorted and unique"
            )
        if not isinstance(self.diagnostics, PopulationAllocationDiagnostics):
            raise PopulationAllocationError(
                "diagnostics must be PopulationAllocationDiagnostics"
            )
        if sum(item.integer_capacity for item in self.allocations) != (
            self.diagnostics.allocatable_generated_capacity
        ):
            raise PopulationAllocationError(
                "allocation capacities must sum to diagnostic capacity"
            )
        if sum(item.residents for item in self.allocations) != (
            self.diagnostics.allocated_generated_population
        ):
            raise PopulationAllocationError(
                "allocated residents must sum to diagnostic population"
            )


class PopulationAllocator:
    """Allocate generated residents deterministically within building capacities."""

    def __init__(
        self,
        *,
        max_buildings: int = DEFAULT_MAX_POPULATION_ALLOCATION_BUILDINGS,
    ) -> None:
        _require_positive_int("max_buildings", max_buildings)
        self.max_buildings = max_buildings

    def allocate(
        self,
        capacities: ResidentialCapacityResult,
        *,
        scenario: DemographicScenario,
        baseline_population: int | None = None,
    ) -> PopulationAllocationResult:
        self._validate_inputs(
            capacities=capacities,
            scenario=scenario,
            baseline_population=baseline_population,
        )
        resolved_baseline = 0 if baseline_population is None else baseline_population
        target_total = _resolve_target_total(
            scenario=scenario,
            baseline_population=resolved_baseline,
            baseline_was_supplied=baseline_population is not None,
        )
        generated_target = max(0, target_total - resolved_baseline)
        baseline_excess = max(0, resolved_baseline - target_total)

        ordered = tuple(
            sorted(capacities.buildings, key=lambda item: item.building_id)
        )
        integer_capacities = tuple(
            _integer_capacity(item.resident_capacity)
            for item in ordered
        )
        total_capacity = sum(integer_capacities)
        allocation_total = min(generated_target, total_capacity)
        residents = _apportion_population(
            buildings=ordered,
            integer_capacities=integer_capacities,
            allocation_total=allocation_total,
        )

        allocations = tuple(
            BuildingPopulationAllocation(
                building_id=building.building_id,
                resident_capacity=building.resident_capacity,
                integer_capacity=integer_capacity,
                residents=resident_count,
                utilization_ratio=(
                    resident_count / integer_capacity
                    if integer_capacity > 0
                    else 0.0
                ),
            )
            for building, integer_capacity, resident_count in zip(
                ordered,
                integer_capacities,
                residents,
                strict=True,
            )
        )
        diagnostics = PopulationAllocationDiagnostics(
            status=_allocation_status(
                generated_target=generated_target,
                allocated=allocation_total,
                baseline_excess=baseline_excess,
            ),
            target_total_population=target_total,
            baseline_population=resolved_baseline,
            generated_population_target=generated_target,
            allocatable_generated_capacity=total_capacity,
            allocated_generated_population=allocation_total,
            unmet_generated_population=generated_target - allocation_total,
            baseline_excess_population=baseline_excess,
        )
        return PopulationAllocationResult(
            scenario_version=scenario.version,
            scenario_fingerprint=scenario.fingerprint,
            allocations=allocations,
            diagnostics=diagnostics,
        )

    def _validate_inputs(
        self,
        *,
        capacities: ResidentialCapacityResult,
        scenario: DemographicScenario,
        baseline_population: int | None,
    ) -> None:
        if not isinstance(capacities, ResidentialCapacityResult):
            raise PopulationAllocationError(
                "capacities must be a ResidentialCapacityResult"
            )
        if not isinstance(scenario, DemographicScenario):
            raise PopulationAllocationError(
                "scenario must be a DemographicScenario"
            )
        if capacities.scenario_version != scenario.version:
            raise PopulationAllocationError(
                "capacity scenario version must match allocation scenario"
            )
        if capacities.scenario_fingerprint != scenario.fingerprint:
            raise PopulationAllocationError(
                "capacity scenario fingerprint must match allocation scenario"
            )
        if len(capacities.buildings) > self.max_buildings:
            raise PopulationAllocationError(
                "population allocation building limit exceeded: "
                f"{len(capacities.buildings)} > {self.max_buildings}"
            )
        if baseline_population is not None:
            _require_non_negative_int(
                "baseline_population",
                baseline_population,
            )
        if (
            scenario.population_target.kind
            is PopulationTargetKind.GROWTH_RATE
            and baseline_population is None
        ):
            raise PopulationAllocationError(
                "growth-rate target requires baseline_population"
            )


def _resolve_target_total(
    *,
    scenario: DemographicScenario,
    baseline_population: int,
    baseline_was_supplied: bool,
) -> int:
    target = scenario.population_target
    if target.kind is PopulationTargetKind.TOTAL_POPULATION:
        total = target.total_population
        if total is None:
            raise PopulationAllocationError(
                "total-population target is missing its value"
            )
        return total

    if not baseline_was_supplied:
        raise PopulationAllocationError(
            "growth-rate target requires baseline_population"
        )
    growth_rate = target.growth_rate
    if growth_rate is None:
        raise PopulationAllocationError(
            "growth-rate target is missing its value"
        )
    projected = (
        Decimal(baseline_population)
        * (Decimal(1) + Decimal(str(growth_rate)))
    )
    rounded = projected.to_integral_value(rounding=ROUND_HALF_UP)
    return max(0, int(rounded))


def _apportion_population(
    *,
    buildings: tuple[ResidentialBuildingCapacity, ...],
    integer_capacities: tuple[int, ...],
    allocation_total: int,
) -> tuple[int, ...]:
    _require_non_negative_int("allocation_total", allocation_total)
    if len(buildings) != len(integer_capacities):
        raise PopulationAllocationError(
            "building and integer-capacity counts must match"
        )
    total_capacity = sum(integer_capacities)
    if allocation_total > total_capacity:
        raise PopulationAllocationError(
            "allocation_total cannot exceed total integer capacity"
        )
    if allocation_total == 0 or total_capacity == 0:
        return tuple(0 for _ in buildings)

    base_allocations: list[int] = []
    remainders: list[tuple[int, str, int]] = []
    for index, (building, capacity) in enumerate(
        zip(buildings, integer_capacities, strict=True)
    ):
        numerator = allocation_total * capacity
        base, remainder = divmod(numerator, total_capacity)
        base_allocations.append(base)
        remainders.append((remainder, building.building_id, index))

    remaining = allocation_total - sum(base_allocations)
    ordered_remainders = sorted(
        remainders,
        key=lambda item: (-item[0], item[1]),
    )
    for _, _, index in ordered_remainders[:remaining]:
        if base_allocations[index] >= integer_capacities[index]:
            raise PopulationAllocationError(
                "apportionment attempted to exceed building capacity"
            )
        base_allocations[index] += 1

    if sum(base_allocations) != allocation_total:
        raise PopulationAllocationError(
            "apportionment did not preserve allocation total"
        )
    return tuple(base_allocations)


def _allocation_status(
    *,
    generated_target: int,
    allocated: int,
    baseline_excess: int,
) -> PopulationAllocationStatus:
    if baseline_excess > 0:
        return PopulationAllocationStatus.BASELINE_EXCEEDS_TARGET
    if allocated == generated_target:
        return PopulationAllocationStatus.TARGET_MET
    return PopulationAllocationStatus.CAPACITY_EXHAUSTED


def _integer_capacity(value: float) -> int:
    capacity = _require_non_negative_finite("resident_capacity", value)
    return max(0, math.floor(capacity + _CAPACITY_INTEGER_EPSILON))


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PopulationAllocationError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PopulationAllocationError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PopulationAllocationError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise PopulationAllocationError(
            f"{field_name} must be a finite non-negative number"
        )
    return number


def _require_ratio(field_name: str, value: float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number > 1.0:
        raise PopulationAllocationError(
            f"{field_name} must be inside 0..1"
        )
    return number


_CAPACITY_INTEGER_EPSILON = 1e-9
