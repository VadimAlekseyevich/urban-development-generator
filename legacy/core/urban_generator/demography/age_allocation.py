from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from core.urban_generator.demography.allocation import PopulationAllocationResult
from core.urban_generator.demography.config import AgeGroupShare, DemographicScenario

DEFAULT_MAX_AGE_ALLOCATION_BUILDINGS = 100_000
DEFAULT_MAX_AGE_GROUPS = 64


class AgeGroupAllocationError(ValueError):
    """Raised when S09-T04 age-group allocation violates the contract."""


@dataclass(frozen=True, slots=True)
class AgeGroupPopulation:
    """Integer residents assigned to one configured age group."""

    code: str
    min_age: int
    max_age: int | None
    residents: int

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code:
            raise AgeGroupAllocationError("age group code must be a non-empty string")
        _require_non_negative_int("min_age", self.min_age)
        if self.max_age is not None:
            _require_non_negative_int("max_age", self.max_age)
            if self.max_age < self.min_age:
                raise AgeGroupAllocationError("max_age must be >= min_age")
        _require_non_negative_int("residents", self.residents)


@dataclass(frozen=True, slots=True)
class BuildingAgeGroupAllocation:
    """One building's residents partitioned into configured age groups."""

    building_id: str
    total_residents: int
    age_groups: tuple[AgeGroupPopulation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.building_id, str) or not self.building_id:
            raise AgeGroupAllocationError(
                "building_id must be a non-empty string"
            )
        _require_non_negative_int("total_residents", self.total_residents)
        if not isinstance(self.age_groups, tuple):
            raise AgeGroupAllocationError(
                "age_groups must be an immutable tuple"
            )
        if any(not isinstance(item, AgeGroupPopulation) for item in self.age_groups):
            raise AgeGroupAllocationError(
                "age_groups must contain AgeGroupPopulation values"
            )
        codes = tuple(item.code for item in self.age_groups)
        if len(codes) != len(set(codes)):
            raise AgeGroupAllocationError(
                "building age group codes must be unique"
            )
        if sum(item.residents for item in self.age_groups) != self.total_residents:
            raise AgeGroupAllocationError(
                "building age group residents must sum to total_residents"
            )


@dataclass(frozen=True, slots=True)
class AgeGroupAllocationTotal:
    """Actual cohort total compared with the scenario target share."""

    code: str
    min_age: int
    max_age: int | None
    target_share: float
    residents: int
    achieved_share: float

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code:
            raise AgeGroupAllocationError("age group code must be a non-empty string")
        _require_non_negative_int("min_age", self.min_age)
        if self.max_age is not None:
            _require_non_negative_int("max_age", self.max_age)
            if self.max_age < self.min_age:
                raise AgeGroupAllocationError("max_age must be >= min_age")
        target_share = _require_ratio("target_share", self.target_share)
        _require_non_negative_int("residents", self.residents)
        achieved_share = _require_ratio(
            "achieved_share",
            self.achieved_share,
        )
        object.__setattr__(self, "target_share", target_share)
        object.__setattr__(self, "achieved_share", achieved_share)


@dataclass(frozen=True, slots=True)
class AgeGroupAllocationResult:
    """Canonical S09-T04 building/cohort matrix with exact integer margins."""

    scenario_version: str
    scenario_fingerprint: str
    buildings: tuple[BuildingAgeGroupAllocation, ...]
    totals: tuple[AgeGroupAllocationTotal, ...]
    total_population: int

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_version, str) or not self.scenario_version:
            raise AgeGroupAllocationError(
                "scenario_version must be a non-empty string"
            )
        if (
            not isinstance(self.scenario_fingerprint, str)
            or len(self.scenario_fingerprint) != 64
            or any(ch not in "0123456789abcdef" for ch in self.scenario_fingerprint)
        ):
            raise AgeGroupAllocationError(
                "scenario_fingerprint must be a lowercase SHA-256 hex digest"
            )
        _require_non_negative_int("total_population", self.total_population)
        if not isinstance(self.buildings, tuple):
            raise AgeGroupAllocationError(
                "buildings must be an immutable tuple"
            )
        if any(
            not isinstance(item, BuildingAgeGroupAllocation)
            for item in self.buildings
        ):
            raise AgeGroupAllocationError(
                "buildings must contain BuildingAgeGroupAllocation values"
            )
        ids = tuple(item.building_id for item in self.buildings)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise AgeGroupAllocationError(
                "building age allocations must be sorted and unique"
            )
        if not isinstance(self.totals, tuple) or not self.totals:
            raise AgeGroupAllocationError(
                "totals must be a non-empty immutable tuple"
            )
        if any(not isinstance(item, AgeGroupAllocationTotal) for item in self.totals):
            raise AgeGroupAllocationError(
                "totals must contain AgeGroupAllocationTotal values"
            )
        total_codes = tuple(item.code for item in self.totals)
        if len(total_codes) != len(set(total_codes)):
            raise AgeGroupAllocationError(
                "age group total codes must be unique"
            )

        if sum(item.total_residents for item in self.buildings) != self.total_population:
            raise AgeGroupAllocationError(
                "building resident totals must sum to total_population"
            )
        if sum(item.residents for item in self.totals) != self.total_population:
            raise AgeGroupAllocationError(
                "age group totals must sum to total_population"
            )

        expected_codes = tuple(item.code for item in self.totals)
        column_totals = [0 for _ in self.totals]
        for building in self.buildings:
            if tuple(item.code for item in building.age_groups) != expected_codes:
                raise AgeGroupAllocationError(
                    "every building must contain age groups in total order"
                )
            for index, group in enumerate(building.age_groups):
                column_totals[index] += group.residents
        if tuple(column_totals) != tuple(item.residents for item in self.totals):
            raise AgeGroupAllocationError(
                "building cohort columns must match age group totals"
            )

        for item in self.totals:
            expected_share = (
                item.residents / self.total_population
                if self.total_population > 0
                else 0.0
            )
            if not math.isclose(
                item.achieved_share,
                expected_share,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise AgeGroupAllocationError(
                    "achieved_share must match cohort residents / total population"
                )


class AgeGroupAllocator:
    """Allocate exact cohort totals while preserving every building row total."""

    def __init__(
        self,
        *,
        max_buildings: int = DEFAULT_MAX_AGE_ALLOCATION_BUILDINGS,
        max_age_groups: int = DEFAULT_MAX_AGE_GROUPS,
    ) -> None:
        _require_positive_int("max_buildings", max_buildings)
        _require_positive_int("max_age_groups", max_age_groups)
        self.max_buildings = max_buildings
        self.max_age_groups = max_age_groups

    def allocate(
        self,
        population: PopulationAllocationResult,
        *,
        scenario: DemographicScenario,
    ) -> AgeGroupAllocationResult:
        self._validate_inputs(population=population, scenario=scenario)

        ordered_buildings = tuple(
            sorted(population.allocations, key=lambda item: item.building_id)
        )
        total_population = sum(item.residents for item in ordered_buildings)
        group_totals = _apportion_group_totals(
            total_population=total_population,
            groups=scenario.age_groups,
        )

        remaining_group_totals = list(group_totals)
        remaining_population = total_population
        building_results: list[BuildingAgeGroupAllocation] = []

        for building in ordered_buildings:
            counts = _apportion_building_row(
                row_total=building.residents,
                remaining_group_totals=tuple(remaining_group_totals),
                remaining_population=remaining_population,
            )
            age_groups = tuple(
                AgeGroupPopulation(
                    code=group.code,
                    min_age=group.min_age,
                    max_age=group.max_age,
                    residents=count,
                )
                for group, count in zip(
                    scenario.age_groups,
                    counts,
                    strict=True,
                )
            )
            building_results.append(
                BuildingAgeGroupAllocation(
                    building_id=building.building_id,
                    total_residents=building.residents,
                    age_groups=age_groups,
                )
            )
            for index, count in enumerate(counts):
                remaining_group_totals[index] -= count
                if remaining_group_totals[index] < 0:
                    raise AgeGroupAllocationError(
                        "age allocation exceeded remaining cohort total"
                    )
            remaining_population -= building.residents
            if remaining_population < 0:
                raise AgeGroupAllocationError(
                    "age allocation exceeded remaining population"
                )

        if remaining_population != 0 or any(remaining_group_totals):
            raise AgeGroupAllocationError(
                "age allocation did not preserve exact matrix margins"
            )

        totals = tuple(
            AgeGroupAllocationTotal(
                code=group.code,
                min_age=group.min_age,
                max_age=group.max_age,
                target_share=group.share,
                residents=count,
                achieved_share=(
                    count / total_population
                    if total_population > 0
                    else 0.0
                ),
            )
            for group, count in zip(
                scenario.age_groups,
                group_totals,
                strict=True,
            )
        )
        return AgeGroupAllocationResult(
            scenario_version=scenario.version,
            scenario_fingerprint=scenario.fingerprint,
            buildings=tuple(building_results),
            totals=totals,
            total_population=total_population,
        )

    def _validate_inputs(
        self,
        *,
        population: PopulationAllocationResult,
        scenario: DemographicScenario,
    ) -> None:
        if not isinstance(population, PopulationAllocationResult):
            raise AgeGroupAllocationError(
                "population must be a PopulationAllocationResult"
            )
        if not isinstance(scenario, DemographicScenario):
            raise AgeGroupAllocationError(
                "scenario must be a DemographicScenario"
            )
        if population.scenario_version != scenario.version:
            raise AgeGroupAllocationError(
                "population scenario version must match age scenario"
            )
        if population.scenario_fingerprint != scenario.fingerprint:
            raise AgeGroupAllocationError(
                "population scenario fingerprint must match age scenario"
            )
        if len(population.allocations) > self.max_buildings:
            raise AgeGroupAllocationError(
                "age allocation building limit exceeded: "
                f"{len(population.allocations)} > {self.max_buildings}"
            )
        if len(scenario.age_groups) > self.max_age_groups:
            raise AgeGroupAllocationError(
                "age group limit exceeded: "
                f"{len(scenario.age_groups)} > {self.max_age_groups}"
            )


def _apportion_group_totals(
    *,
    total_population: int,
    groups: tuple[AgeGroupShare, ...],
) -> tuple[int, ...]:
    _require_non_negative_int("total_population", total_population)
    if not groups:
        raise AgeGroupAllocationError("groups must not be empty")
    if total_population == 0:
        return tuple(0 for _ in groups)

    weights = tuple(Decimal(str(group.share)) for group in groups)
    weight_sum = sum(weights, Decimal(0))
    if weight_sum <= 0:
        raise AgeGroupAllocationError(
            "age group share sum must be positive"
        )

    quotas = tuple(
        Decimal(total_population) * weight / weight_sum
        for weight in weights
    )
    base = [int(quota) for quota in quotas]
    remaining = total_population - sum(base)
    remainders = tuple(
        quota - Decimal(base[index])
        for index, quota in enumerate(quotas)
    )
    order = sorted(
        range(len(groups)),
        key=lambda index: (-remainders[index], index),
    )
    for index in order[:remaining]:
        base[index] += 1

    if sum(base) != total_population:
        raise AgeGroupAllocationError(
            "cohort apportionment did not preserve population total"
        )
    return tuple(base)


def _apportion_building_row(
    *,
    row_total: int,
    remaining_group_totals: tuple[int, ...],
    remaining_population: int,
) -> tuple[int, ...]:
    _require_non_negative_int("row_total", row_total)
    _require_non_negative_int("remaining_population", remaining_population)
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        for value in remaining_group_totals
    ):
        raise AgeGroupAllocationError(
            "remaining group totals must be non-negative integers"
        )
    if sum(remaining_group_totals) != remaining_population:
        raise AgeGroupAllocationError(
            "remaining group totals must sum to remaining_population"
        )
    if row_total > remaining_population:
        raise AgeGroupAllocationError(
            "building row cannot exceed remaining population"
        )
    if row_total == 0:
        return tuple(0 for _ in remaining_group_totals)
    if remaining_population == 0:
        raise AgeGroupAllocationError(
            "positive building row requires remaining population"
        )

    base: list[int] = []
    remainders: list[tuple[int, int]] = []
    for index, column_total in enumerate(remaining_group_totals):
        numerator = row_total * column_total
        quotient, remainder = divmod(numerator, remaining_population)
        base.append(quotient)
        remainders.append((remainder, index))

    remaining = row_total - sum(base)
    candidates = sorted(
        (
            (remainder, index)
            for remainder, index in remainders
            if base[index] < remaining_group_totals[index]
        ),
        key=lambda item: (-item[0], item[1]),
    )
    if remaining > len(candidates):
        raise AgeGroupAllocationError(
            "insufficient cohort remainder capacity for building row"
        )
    for _, index in candidates[:remaining]:
        base[index] += 1

    if sum(base) != row_total:
        raise AgeGroupAllocationError(
            "building cohort allocation did not preserve row total"
        )
    if any(
        count > remaining_group_totals[index]
        for index, count in enumerate(base)
    ):
        raise AgeGroupAllocationError(
            "building cohort allocation exceeded remaining column total"
        )
    return tuple(base)


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AgeGroupAllocationError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgeGroupAllocationError(
            f"{field_name} must be a non-negative integer"
        )


def _require_ratio(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgeGroupAllocationError(
            f"{field_name} must be a finite ratio"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise AgeGroupAllocationError(
            f"{field_name} must be inside 0..1"
        )
    return number
