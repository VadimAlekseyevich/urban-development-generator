from __future__ import annotations

import math
from dataclasses import dataclass

from core.urban_generator.demography.age_allocation import AgeGroupPopulation
from core.urban_generator.demography.aggregation import DemographicAggregationResult
from core.urban_generator.zoning import ZoneClass

DEFAULT_MAX_DEMOGRAPHY_METRIC_BLOCKS = 100_000


class DemographyMetricsError(ValueError):
    """Raised when S09-T10 demographic metric inputs violate the contract."""


@dataclass(frozen=True, slots=True)
class DemographyBlockArea:
    """Authoritative metric block area used to derive population density."""

    block_id: str
    area_m2: float

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        area = _require_positive_finite("area_m2", self.area_m2)
        object.__setattr__(self, "area_m2", area)


@dataclass(frozen=True, slots=True)
class DemographicAgeMetric:
    code: str
    min_age: int
    max_age: int | None
    residents: int
    share: float

    def __post_init__(self) -> None:
        _require_id("code", self.code)
        _require_non_negative_int("min_age", self.min_age)
        if self.max_age is not None:
            _require_non_negative_int("max_age", self.max_age)
            if self.max_age < self.min_age:
                raise DemographyMetricsError("max_age must be >= min_age")
        _require_non_negative_int("residents", self.residents)
        share = _require_ratio("share", self.share)
        object.__setattr__(self, "share", share)


@dataclass(frozen=True, slots=True)
class BlockDemographyMetrics:
    block_id: str
    zone_id: str
    zone_class: ZoneClass
    area_m2: float
    population: int
    population_density_per_km2: float
    jobs_estimate: float
    age_groups: tuple[DemographicAgeMetric, ...]

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        _require_id("zone_id", self.zone_id)
        if not isinstance(self.zone_class, ZoneClass):
            raise DemographyMetricsError("zone_class must be a ZoneClass")
        area = _require_positive_finite("area_m2", self.area_m2)
        _require_non_negative_int("population", self.population)
        density = _require_non_negative_finite(
            "population_density_per_km2",
            self.population_density_per_km2,
        )
        jobs = _require_non_negative_finite("jobs_estimate", self.jobs_estimate)
        _validate_age_metrics(self.age_groups, expected_population=self.population)
        object.__setattr__(self, "area_m2", area)
        object.__setattr__(self, "population_density_per_km2", density)
        object.__setattr__(self, "jobs_estimate", jobs)


@dataclass(frozen=True, slots=True)
class DemographyMetricsTotals:
    block_count: int
    area_m2: float
    population: int
    population_density_per_km2: float
    jobs_estimate: float
    age_groups: tuple[DemographicAgeMetric, ...]

    def __post_init__(self) -> None:
        _require_non_negative_int("block_count", self.block_count)
        area = _require_non_negative_finite("area_m2", self.area_m2)
        _require_non_negative_int("population", self.population)
        density = _require_non_negative_finite(
            "population_density_per_km2",
            self.population_density_per_km2,
        )
        jobs = _require_non_negative_finite("jobs_estimate", self.jobs_estimate)
        _validate_age_metrics(self.age_groups, expected_population=self.population)
        object.__setattr__(self, "area_m2", area)
        object.__setattr__(self, "population_density_per_km2", density)
        object.__setattr__(self, "jobs_estimate", jobs)


@dataclass(frozen=True, slots=True)
class DemographyMetricsResult:
    scenario_version: str
    scenario_fingerprint: str
    employment_config_version: str
    employment_config_fingerprint: str
    blocks: tuple[BlockDemographyMetrics, ...]
    totals: DemographyMetricsTotals

    def __post_init__(self) -> None:
        _require_id("scenario_version", self.scenario_version)
        _require_sha256("scenario_fingerprint", self.scenario_fingerprint)
        _require_id("employment_config_version", self.employment_config_version)
        _require_sha256(
            "employment_config_fingerprint",
            self.employment_config_fingerprint,
        )
        if not isinstance(self.blocks, tuple):
            raise DemographyMetricsError("blocks must be an immutable tuple")
        if any(not isinstance(item, BlockDemographyMetrics) for item in self.blocks):
            raise DemographyMetricsError(
                "blocks must contain BlockDemographyMetrics values"
            )
        ids = tuple(item.block_id for item in self.blocks)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise DemographyMetricsError("metric blocks must be sorted and unique")
        if not isinstance(self.totals, DemographyMetricsTotals):
            raise DemographyMetricsError("totals must be DemographyMetricsTotals")
        if self.totals.block_count != len(self.blocks):
            raise DemographyMetricsError(
                "totals block_count must match metric blocks"
            )
        if sum(item.population for item in self.blocks) != self.totals.population:
            raise DemographyMetricsError(
                "block population must sum to metric total population"
            )
        if not math.isclose(
            math.fsum(item.jobs_estimate for item in self.blocks),
            self.totals.jobs_estimate,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise DemographyMetricsError(
                "block jobs must sum to metric total jobs"
            )


class DemographyMetricsBuilder:
    """Derive stable UI/API metrics from authoritative block aggregation and area."""

    def __init__(
        self,
        *,
        max_blocks: int = DEFAULT_MAX_DEMOGRAPHY_METRIC_BLOCKS,
    ) -> None:
        _require_positive_int("max_blocks", max_blocks)
        self.max_blocks = max_blocks

    def build(
        self,
        aggregation: DemographicAggregationResult,
        *,
        block_areas: tuple[DemographyBlockArea, ...],
    ) -> DemographyMetricsResult:
        if not isinstance(aggregation, DemographicAggregationResult):
            raise DemographyMetricsError(
                "aggregation must be a DemographicAggregationResult"
            )
        if not isinstance(block_areas, tuple):
            raise DemographyMetricsError(
                "block_areas must be an immutable tuple"
            )
        if len(aggregation.blocks) > self.max_blocks:
            raise DemographyMetricsError(
                "demography metric block limit exceeded: "
                f"{len(aggregation.blocks)} > {self.max_blocks}"
            )
        if any(not isinstance(item, DemographyBlockArea) for item in block_areas):
            raise DemographyMetricsError(
                "block_areas must contain DemographyBlockArea values"
            )
        area_ids = tuple(item.block_id for item in block_areas)
        if len(area_ids) != len(set(area_ids)):
            raise DemographyMetricsError("block area ids must be unique")
        aggregate_ids = tuple(item.block_id for item in aggregation.blocks)
        if set(area_ids) != set(aggregate_ids):
            raise DemographyMetricsError(
                "block areas must cover exactly the aggregated blocks"
            )

        area_by_id = {item.block_id: item.area_m2 for item in block_areas}
        blocks = tuple(
            BlockDemographyMetrics(
                block_id=block.block_id,
                zone_id=block.zone_id,
                zone_class=block.zone_class,
                area_m2=area_by_id[block.block_id],
                population=block.population,
                population_density_per_km2=_density(
                    population=block.population,
                    area_m2=area_by_id[block.block_id],
                ),
                jobs_estimate=block.jobs_estimate,
                age_groups=_age_metrics(
                    block.age_groups,
                    total_population=block.population,
                ),
            )
            for block in aggregation.blocks
        )
        total_area = math.fsum(item.area_m2 for item in blocks)
        totals = DemographyMetricsTotals(
            block_count=len(blocks),
            area_m2=total_area,
            population=aggregation.totals.population,
            population_density_per_km2=(
                _density(
                    population=aggregation.totals.population,
                    area_m2=total_area,
                )
                if total_area > 0.0
                else 0.0
            ),
            jobs_estimate=aggregation.totals.jobs_estimate,
            age_groups=_age_metrics(
                aggregation.totals.age_groups,
                total_population=aggregation.totals.population,
            ),
        )
        return DemographyMetricsResult(
            scenario_version=aggregation.scenario_version,
            scenario_fingerprint=aggregation.scenario_fingerprint,
            employment_config_version=aggregation.employment_config_version,
            employment_config_fingerprint=(
                aggregation.employment_config_fingerprint
            ),
            blocks=blocks,
            totals=totals,
        )


def _age_metrics(
    groups: tuple[AgeGroupPopulation, ...],
    *,
    total_population: int,
) -> tuple[DemographicAgeMetric, ...]:
    return tuple(
        DemographicAgeMetric(
            code=group.code,
            min_age=group.min_age,
            max_age=group.max_age,
            residents=group.residents,
            share=(
                group.residents / total_population
                if total_population > 0
                else 0.0
            ),
        )
        for group in groups
    )


def _density(*, population: int, area_m2: float) -> float:
    return population / area_m2 * 1_000_000.0


def _validate_age_metrics(
    groups: tuple[DemographicAgeMetric, ...],
    *,
    expected_population: int,
) -> None:
    if not isinstance(groups, tuple) or not groups:
        raise DemographyMetricsError(
            "age_groups must be a non-empty immutable tuple"
        )
    if any(not isinstance(item, DemographicAgeMetric) for item in groups):
        raise DemographyMetricsError(
            "age_groups must contain DemographicAgeMetric values"
        )
    codes = tuple(item.code for item in groups)
    if len(codes) != len(set(codes)):
        raise DemographyMetricsError("age group codes must be unique")
    if sum(item.residents for item in groups) != expected_population:
        raise DemographyMetricsError(
            "age-group residents must sum to population"
        )
    expected_share_sum = 1.0 if expected_population > 0 else 0.0
    if not math.isclose(
        math.fsum(item.share for item in groups),
        expected_share_sum,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise DemographyMetricsError(
            "age-group shares must sum to the expected population share"
        )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DemographyMetricsError(
            f"{field_name} must be a non-empty string"
        )
    if "\n" in value or "\r" in value:
        raise DemographyMetricsError(
            f"{field_name} must not contain line breaks"
        )


def _require_sha256(field_name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise DemographyMetricsError(
            f"{field_name} must be a lowercase SHA-256 hex digest"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DemographyMetricsError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DemographyMetricsError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(
    field_name: str,
    value: int | float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DemographyMetricsError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise DemographyMetricsError(
            f"{field_name} must be a finite non-negative number"
        )
    return number


def _require_positive_finite(field_name: str, value: int | float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number <= 0.0:
        raise DemographyMetricsError(
            f"{field_name} must be greater than zero"
        )
    return number


def _require_ratio(field_name: str, value: int | float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number > 1.0:
        raise DemographyMetricsError(
            f"{field_name} must be inside 0..1"
        )
    return number
