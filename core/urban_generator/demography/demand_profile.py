from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from core.urban_generator.demography.aggregation import (
    DemographicAggregationResult,
)
from core.urban_generator.demography.config import DemographicScenario
from core.urban_generator.zoning import ZoneClass

DEFAULT_MAX_DEMOGRAPHIC_DEMAND_BLOCKS = 100_000


class DemographicDemandProfileError(ValueError):
    """Raised when S09-T09 demand-profile inputs violate the typed contract."""


class DemographicDemandCategory(StrEnum):
    """Generic demographic signal kinds consumed by infrastructure demand models."""

    POPULATION = "population"
    AGE_GROUP = "age_group"
    WORKFORCE = "workforce"
    JOBS = "jobs"


class DemographicDemandUnit(StrEnum):
    """Unit carried by one demographic demand signal."""

    PEOPLE = "people"
    JOBS = "jobs"


_CATEGORY_ORDER = {
    DemographicDemandCategory.POPULATION: 0,
    DemographicDemandCategory.AGE_GROUP: 1,
    DemographicDemandCategory.WORKFORCE: 2,
    DemographicDemandCategory.JOBS: 3,
}


@dataclass(frozen=True, slots=True)
class DemographicDemandSignal:
    """One non-negative typed value available to S10 demand models."""

    category: DemographicDemandCategory
    value: float
    unit: DemographicDemandUnit
    demographic_group: str | None = None
    min_age: int | None = None
    max_age: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.category, DemographicDemandCategory):
            raise DemographicDemandProfileError(
                "category must be a DemographicDemandCategory"
            )
        if not isinstance(self.unit, DemographicDemandUnit):
            raise DemographicDemandProfileError(
                "unit must be a DemographicDemandUnit"
            )
        value = _require_non_negative_finite("value", self.value)
        object.__setattr__(self, "value", value)

        if self.category is DemographicDemandCategory.AGE_GROUP:
            _require_id("demographic_group", self.demographic_group)
            if (
                isinstance(self.min_age, bool)
                or not isinstance(self.min_age, int)
                or self.min_age < 0
            ):
                raise DemographicDemandProfileError(
                    "age-group min_age must be a non-negative integer"
                )
            if self.max_age is not None:
                if (
                    isinstance(self.max_age, bool)
                    or not isinstance(self.max_age, int)
                    or self.max_age < self.min_age
                ):
                    raise DemographicDemandProfileError(
                        "age-group max_age must be >= min_age"
                    )
            if self.unit is not DemographicDemandUnit.PEOPLE:
                raise DemographicDemandProfileError(
                    "age-group signal unit must be people"
                )
            return

        if (
            self.demographic_group is not None
            or self.min_age is not None
            or self.max_age is not None
        ):
            raise DemographicDemandProfileError(
                "only age-group signals may carry demographic group metadata"
            )
        expected_unit = (
            DemographicDemandUnit.JOBS
            if self.category is DemographicDemandCategory.JOBS
            else DemographicDemandUnit.PEOPLE
        )
        if self.unit is not expected_unit:
            raise DemographicDemandProfileError(
                f"{self.category.value} signal has invalid unit {self.unit.value}"
            )

    @property
    def key(self) -> tuple[DemographicDemandCategory, str | None]:
        return self.category, self.demographic_group


@dataclass(frozen=True, slots=True)
class BlockDemographicDemand:
    """Canonical demand signals for one block."""

    block_id: str
    zone_id: str
    zone_class: ZoneClass
    signals: tuple[DemographicDemandSignal, ...]

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        _require_id("zone_id", self.zone_id)
        if not isinstance(self.zone_class, ZoneClass):
            raise DemographicDemandProfileError(
                "zone_class must be a ZoneClass"
            )
        canonical = _canonical_signals(self.signals)
        _validate_required_signal_categories(canonical)
        object.__setattr__(self, "signals", canonical)

    def signal(
        self,
        category: DemographicDemandCategory,
        *,
        demographic_group: str | None = None,
    ) -> DemographicDemandSignal:
        return _find_signal(
            self.signals,
            category=category,
            demographic_group=demographic_group,
        )


@dataclass(frozen=True, slots=True)
class DemographicDemandTotals:
    """Project/run totals for the same signal schema as every block."""

    block_count: int
    signals: tuple[DemographicDemandSignal, ...]

    def __post_init__(self) -> None:
        _require_non_negative_int("block_count", self.block_count)
        canonical = _canonical_signals(self.signals)
        _validate_required_signal_categories(canonical)
        object.__setattr__(self, "signals", canonical)

    def signal(
        self,
        category: DemographicDemandCategory,
        *,
        demographic_group: str | None = None,
    ) -> DemographicDemandSignal:
        return _find_signal(
            self.signals,
            category=category,
            demographic_group=demographic_group,
        )


@dataclass(frozen=True, slots=True)
class DemographicDemandProfile:
    """S09-T09 typed block signals with exact scenario provenance."""

    scenario_version: str
    scenario_fingerprint: str
    blocks: tuple[BlockDemographicDemand, ...]
    totals: DemographicDemandTotals

    def __post_init__(self) -> None:
        _require_id("scenario_version", self.scenario_version)
        _require_sha256("scenario_fingerprint", self.scenario_fingerprint)
        if not isinstance(self.blocks, tuple):
            raise DemographicDemandProfileError(
                "blocks must be an immutable tuple"
            )
        if any(not isinstance(item, BlockDemographicDemand) for item in self.blocks):
            raise DemographicDemandProfileError(
                "blocks must contain BlockDemographicDemand values"
            )
        ids = tuple(item.block_id for item in self.blocks)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise DemographicDemandProfileError(
                "demand-profile blocks must be sorted and unique"
            )
        if not isinstance(self.totals, DemographicDemandTotals):
            raise DemographicDemandProfileError(
                "totals must be DemographicDemandTotals"
            )
        if self.totals.block_count != len(self.blocks):
            raise DemographicDemandProfileError(
                "totals block_count must match demand-profile blocks"
            )
        _validate_profile_totals(blocks=self.blocks, totals=self.totals)


class DemographicDemandProfileBuilder:
    """Convert baseline or calibrated S09 aggregation into reusable S10 signals."""

    def __init__(
        self,
        *,
        max_blocks: int = DEFAULT_MAX_DEMOGRAPHIC_DEMAND_BLOCKS,
    ) -> None:
        _require_positive_int("max_blocks", max_blocks)
        self.max_blocks = max_blocks

    def build(
        self,
        aggregation: DemographicAggregationResult,
        *,
        scenario: DemographicScenario,
    ) -> DemographicDemandProfile:
        self._validate_inputs(aggregation=aggregation, scenario=scenario)

        blocks = tuple(
            BlockDemographicDemand(
                block_id=block.block_id,
                zone_id=block.zone_id,
                zone_class=block.zone_class,
                signals=_signals(
                    population=block.population,
                    age_groups=block.age_groups,
                    workforce=(
                        block.population * scenario.working_population_ratio
                    ),
                    jobs=block.jobs_estimate,
                ),
            )
            for block in aggregation.blocks
        )
        totals = DemographicDemandTotals(
            block_count=len(blocks),
            signals=_signals(
                population=aggregation.totals.population,
                age_groups=aggregation.totals.age_groups,
                workforce=(
                    aggregation.totals.population
                    * scenario.working_population_ratio
                ),
                jobs=aggregation.totals.jobs_estimate,
            ),
        )
        return DemographicDemandProfile(
            scenario_version=scenario.version,
            scenario_fingerprint=scenario.fingerprint,
            blocks=blocks,
            totals=totals,
        )

    def _validate_inputs(
        self,
        *,
        aggregation: DemographicAggregationResult,
        scenario: DemographicScenario,
    ) -> None:
        if not isinstance(aggregation, DemographicAggregationResult):
            raise DemographicDemandProfileError(
                "aggregation must be a DemographicAggregationResult"
            )
        if not isinstance(scenario, DemographicScenario):
            raise DemographicDemandProfileError(
                "scenario must be a DemographicScenario"
            )
        if len(aggregation.blocks) > self.max_blocks:
            raise DemographicDemandProfileError(
                "demographic demand block limit exceeded: "
                f"{len(aggregation.blocks)} > {self.max_blocks}"
            )
        if aggregation.scenario_version != scenario.version:
            raise DemographicDemandProfileError(
                "aggregation scenario version must match demand scenario"
            )
        if aggregation.scenario_fingerprint != scenario.fingerprint:
            raise DemographicDemandProfileError(
                "aggregation scenario fingerprint must match demand scenario"
            )

        expected_groups = tuple(
            (group.code, group.min_age, group.max_age)
            for group in scenario.age_groups
        )
        aggregate_groups = tuple(
            (group.code, group.min_age, group.max_age)
            for group in aggregation.totals.age_groups
        )
        if aggregate_groups != expected_groups:
            raise DemographicDemandProfileError(
                "aggregation age-group metadata must match demand scenario"
            )


def _signals(
    *,
    population: int,
    age_groups: tuple[object, ...],
    workforce: float,
    jobs: float,
) -> tuple[DemographicDemandSignal, ...]:
    signals: list[DemographicDemandSignal] = [
        DemographicDemandSignal(
            category=DemographicDemandCategory.POPULATION,
            value=float(population),
            unit=DemographicDemandUnit.PEOPLE,
        )
    ]
    for group in age_groups:
        signals.append(
            DemographicDemandSignal(
                category=DemographicDemandCategory.AGE_GROUP,
                value=float(group.residents),
                unit=DemographicDemandUnit.PEOPLE,
                demographic_group=group.code,
                min_age=group.min_age,
                max_age=group.max_age,
            )
        )
    signals.extend(
        (
            DemographicDemandSignal(
                category=DemographicDemandCategory.WORKFORCE,
                value=workforce,
                unit=DemographicDemandUnit.PEOPLE,
            ),
            DemographicDemandSignal(
                category=DemographicDemandCategory.JOBS,
                value=jobs,
                unit=DemographicDemandUnit.JOBS,
            ),
        )
    )
    return tuple(signals)


def _canonical_signals(
    signals: tuple[DemographicDemandSignal, ...],
) -> tuple[DemographicDemandSignal, ...]:
    if not isinstance(signals, tuple):
        raise DemographicDemandProfileError(
            "signals must be an immutable tuple"
        )
    if any(not isinstance(item, DemographicDemandSignal) for item in signals):
        raise DemographicDemandProfileError(
            "signals must contain DemographicDemandSignal values"
        )
    keys = tuple(item.key for item in signals)
    if len(keys) != len(set(keys)):
        raise DemographicDemandProfileError(
            "demand signal category/group keys must be unique"
        )
    return tuple(sorted(signals, key=_signal_sort_key))


def _signal_sort_key(
    signal: DemographicDemandSignal,
) -> tuple[int, int, str]:
    min_age = signal.min_age if signal.min_age is not None else -1
    group = signal.demographic_group or ""
    return _CATEGORY_ORDER[signal.category], min_age, group


def _validate_required_signal_categories(
    signals: tuple[DemographicDemandSignal, ...],
) -> None:
    for category in (
        DemographicDemandCategory.POPULATION,
        DemographicDemandCategory.WORKFORCE,
        DemographicDemandCategory.JOBS,
    ):
        matches = tuple(item for item in signals if item.category is category)
        if len(matches) != 1:
            raise DemographicDemandProfileError(
                f"demand profile requires exactly one {category.value} signal"
            )
    age_groups = tuple(
        item
        for item in signals
        if item.category is DemographicDemandCategory.AGE_GROUP
    )
    if not age_groups:
        raise DemographicDemandProfileError(
            "demand profile requires at least one age-group signal"
        )


def _find_signal(
    signals: tuple[DemographicDemandSignal, ...],
    *,
    category: DemographicDemandCategory,
    demographic_group: str | None,
) -> DemographicDemandSignal:
    if not isinstance(category, DemographicDemandCategory):
        raise DemographicDemandProfileError(
            "signal lookup category must be a DemographicDemandCategory"
        )
    for signal in signals:
        if (
            signal.category is category
            and signal.demographic_group == demographic_group
        ):
            return signal
    raise DemographicDemandProfileError(
        "demand signal not found for "
        f"{category.value}/{demographic_group!r}"
    )


def _validate_profile_totals(
    *,
    blocks: tuple[BlockDemographicDemand, ...],
    totals: DemographicDemandTotals,
) -> None:
    total_keys = tuple(signal.key for signal in totals.signals)
    for block in blocks:
        if tuple(signal.key for signal in block.signals) != total_keys:
            raise DemographicDemandProfileError(
                "every block must expose the same demand signal schema as totals"
            )

    for total_signal in totals.signals:
        block_total = math.fsum(
            block.signal(
                total_signal.category,
                demographic_group=total_signal.demographic_group,
            ).value
            for block in blocks
        )
        if not math.isclose(
            block_total,
            total_signal.value,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise DemographicDemandProfileError(
                "block demand signals must sum to project totals"
            )


def _require_id(field_name: str, value: str | None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DemographicDemandProfileError(
            f"{field_name} must be a non-empty string"
        )
    if "\n" in value or "\r" in value:
        raise DemographicDemandProfileError(
            f"{field_name} must not contain line breaks"
        )


def _require_sha256(field_name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise DemographicDemandProfileError(
            f"{field_name} must be a lowercase SHA-256 hex digest"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DemographicDemandProfileError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DemographicDemandProfileError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(
    field_name: str,
    value: int | float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DemographicDemandProfileError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise DemographicDemandProfileError(
            f"{field_name} must be a finite non-negative number"
        )
    return number
