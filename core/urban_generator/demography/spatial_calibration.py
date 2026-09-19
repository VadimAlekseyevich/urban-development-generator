from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from core.urban_generator.demography.age_allocation import AgeGroupPopulation
from core.urban_generator.demography.aggregation import (
    BlockDemographicAggregate,
    DemographicAggregationResult,
)
from core.urban_generator.demography.population_raster import (
    PopulationRasterSample,
    PopulationRasterSamplingResult,
)

DEFAULT_MAX_SPATIAL_CALIBRATION_BLOCKS = 100_000


class SpatialCalibrationError(ValueError):
    """Raised when S09-T08 spatial calibration violates its invariants."""


@dataclass(frozen=True, slots=True)
class SpatialCalibrationPolicy:
    """Blend raster evidence into block population shares within each zone."""

    raster_weight: float = 1.0
    minimum_valid_fraction: float = 0.0
    max_blocks: int = DEFAULT_MAX_SPATIAL_CALIBRATION_BLOCKS

    def __post_init__(self) -> None:
        raster_weight = _require_ratio("raster_weight", self.raster_weight)
        minimum_valid_fraction = _require_ratio(
            "minimum_valid_fraction",
            self.minimum_valid_fraction,
        )
        _require_positive_int("max_blocks", self.max_blocks)
        object.__setattr__(self, "raster_weight", raster_weight)
        object.__setattr__(
            self,
            "minimum_valid_fraction",
            minimum_valid_fraction,
        )


@dataclass(frozen=True, slots=True)
class BlockSpatialCalibration:
    """Audit row describing one block before and after calibration."""

    block_id: str
    zone_id: str
    original_population: int
    calibrated_population: int
    sampled_population: float
    valid_fraction: float
    raster_evidence_used: bool

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        _require_id("zone_id", self.zone_id)
        _require_non_negative_int(
            "original_population",
            self.original_population,
        )
        _require_non_negative_int(
            "calibrated_population",
            self.calibrated_population,
        )
        sampled_population = _require_non_negative_finite(
            "sampled_population",
            self.sampled_population,
        )
        valid_fraction = _require_ratio(
            "valid_fraction",
            self.valid_fraction,
        )
        if not isinstance(self.raster_evidence_used, bool):
            raise SpatialCalibrationError(
                "raster_evidence_used must be a boolean"
            )
        object.__setattr__(self, "sampled_population", sampled_population)
        object.__setattr__(self, "valid_fraction", valid_fraction)


@dataclass(frozen=True, slots=True)
class SpatialCalibrationDiagnostics:
    """Deterministic summary of how strongly raster evidence changed blocks."""

    block_count: int
    zone_count: int
    evidence_block_count: int
    fallback_zone_count: int
    moved_population: int
    maximum_block_shift: int

    def __post_init__(self) -> None:
        for field_name in (
            "block_count",
            "zone_count",
            "evidence_block_count",
            "fallback_zone_count",
            "moved_population",
            "maximum_block_shift",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if self.evidence_block_count > self.block_count:
            raise SpatialCalibrationError(
                "evidence_block_count cannot exceed block_count"
            )
        if self.fallback_zone_count > self.zone_count:
            raise SpatialCalibrationError(
                "fallback_zone_count cannot exceed zone_count"
            )


@dataclass(frozen=True, slots=True)
class SpatialCalibrationResult:
    """Calibrated block distribution with exact upstream totals preserved."""

    aggregation: DemographicAggregationResult
    block_calibrations: tuple[BlockSpatialCalibration, ...]
    diagnostics: SpatialCalibrationDiagnostics

    def __post_init__(self) -> None:
        if not isinstance(self.aggregation, DemographicAggregationResult):
            raise SpatialCalibrationError(
                "aggregation must be a DemographicAggregationResult"
            )
        if not isinstance(self.block_calibrations, tuple):
            raise SpatialCalibrationError(
                "block_calibrations must be an immutable tuple"
            )
        if any(
            not isinstance(item, BlockSpatialCalibration)
            for item in self.block_calibrations
        ):
            raise SpatialCalibrationError(
                "block_calibrations must contain BlockSpatialCalibration values"
            )
        ids = tuple(item.block_id for item in self.block_calibrations)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise SpatialCalibrationError(
                "block calibrations must be sorted and unique"
            )
        if not isinstance(self.diagnostics, SpatialCalibrationDiagnostics):
            raise SpatialCalibrationError(
                "diagnostics must be SpatialCalibrationDiagnostics"
            )
        if self.diagnostics.block_count != len(self.block_calibrations):
            raise SpatialCalibrationError(
                "diagnostic block_count must match calibration rows"
            )


class SpatialDemographicCalibrator:
    """Redistribute block population within zones using optional raster evidence."""

    def __init__(
        self,
        *,
        policy: SpatialCalibrationPolicy | None = None,
    ) -> None:
        self.policy = policy if policy is not None else SpatialCalibrationPolicy()
        if not isinstance(self.policy, SpatialCalibrationPolicy):
            raise SpatialCalibrationError(
                "policy must be a SpatialCalibrationPolicy"
            )

    def calibrate(
        self,
        aggregation: DemographicAggregationResult,
        *,
        raster: PopulationRasterSamplingResult,
    ) -> SpatialCalibrationResult:
        self._validate_inputs(aggregation=aggregation, raster=raster)

        samples_by_id = {
            sample.subject_id: sample
            for sample in raster.samples
        }
        blocks_by_zone: dict[str, list[BlockDemographicAggregate]] = {}
        for block in aggregation.blocks:
            blocks_by_zone.setdefault(block.zone_id, []).append(block)

        calibrated_blocks: list[BlockDemographicAggregate] = []
        audit_rows: list[BlockSpatialCalibration] = []
        fallback_zone_count = 0

        for zone in aggregation.zones:
            zone_blocks = tuple(
                sorted(
                    blocks_by_zone.get(zone.zone_id, ()),
                    key=lambda item: item.block_id,
                )
            )
            populations, used_evidence = self._calibrate_zone_population(
                zone_population=zone.population,
                blocks=zone_blocks,
                samples_by_id=samples_by_id,
            )
            if not used_evidence:
                fallback_zone_count += 1

            age_rows = _allocate_age_matrix(
                row_totals=populations,
                column_totals=tuple(
                    item.residents for item in zone.age_groups
                ),
            )
            for index, block in enumerate(zone_blocks):
                calibrated_population = populations[index]
                calibrated_age_groups = tuple(
                    AgeGroupPopulation(
                        code=group.code,
                        min_age=group.min_age,
                        max_age=group.max_age,
                        residents=age_rows[index][group_index],
                    )
                    for group_index, group in enumerate(zone.age_groups)
                )
                calibrated_blocks.append(
                    BlockDemographicAggregate(
                        block_id=block.block_id,
                        zone_id=block.zone_id,
                        zone_class=block.zone_class,
                        building_count=block.building_count,
                        population=calibrated_population,
                        age_groups=calibrated_age_groups,
                        jobs_estimate=block.jobs_estimate,
                    )
                )
                sample = samples_by_id[block.block_id]
                audit_rows.append(
                    BlockSpatialCalibration(
                        block_id=block.block_id,
                        zone_id=block.zone_id,
                        original_population=block.population,
                        calibrated_population=calibrated_population,
                        sampled_population=sample.sampled_population,
                        valid_fraction=sample.valid_fraction,
                        raster_evidence_used=(
                            used_evidence
                            and _sample_is_eligible(
                                sample,
                                minimum_valid_fraction=(
                                    self.policy.minimum_valid_fraction
                                ),
                            )
                            and sample.sampled_population > 0.0
                        ),
                    )
                )

        ordered_blocks = tuple(
            sorted(calibrated_blocks, key=lambda item: item.block_id)
        )
        calibrated_aggregation = DemographicAggregationResult(
            scenario_version=aggregation.scenario_version,
            scenario_fingerprint=aggregation.scenario_fingerprint,
            employment_config_version=aggregation.employment_config_version,
            employment_config_fingerprint=(
                aggregation.employment_config_fingerprint
            ),
            blocks=ordered_blocks,
            zones=aggregation.zones,
            totals=aggregation.totals,
        )
        ordered_audit = tuple(
            sorted(audit_rows, key=lambda item: item.block_id)
        )
        shifts = tuple(
            abs(item.calibrated_population - item.original_population)
            for item in ordered_audit
        )
        return SpatialCalibrationResult(
            aggregation=calibrated_aggregation,
            block_calibrations=ordered_audit,
            diagnostics=SpatialCalibrationDiagnostics(
                block_count=len(ordered_audit),
                zone_count=len(aggregation.zones),
                evidence_block_count=sum(
                    item.raster_evidence_used for item in ordered_audit
                ),
                fallback_zone_count=fallback_zone_count,
                moved_population=sum(shifts) // 2,
                maximum_block_shift=max(shifts, default=0),
            ),
        )

    def _calibrate_zone_population(
        self,
        *,
        zone_population: int,
        blocks: tuple[BlockDemographicAggregate, ...],
        samples_by_id: dict[str, PopulationRasterSample],
    ) -> tuple[tuple[int, ...], bool]:
        if not blocks:
            raise SpatialCalibrationError(
                "every zone must contain at least one block"
            )
        if zone_population == 0:
            return tuple(0 for _ in blocks), False

        original = tuple(block.population for block in blocks)
        if sum(original) != zone_population:
            raise SpatialCalibrationError(
                "block populations must sum to zone population"
            )

        eligible_population = tuple(
            (
                samples_by_id[block.block_id].sampled_population
                if _sample_is_eligible(
                    samples_by_id[block.block_id],
                    minimum_valid_fraction=self.policy.minimum_valid_fraction,
                )
                else 0.0
            )
            for block in blocks
        )
        raster_total = math.fsum(eligible_population)
        use_raster = (
            self.policy.raster_weight > 0.0
            and raster_total > 0.0
        )
        if not use_raster:
            return original, False

        base_total = Decimal(zone_population)
        raster_total_decimal = sum(
            (Decimal(str(value)) for value in eligible_population),
            Decimal(0),
        )
        raster_weight = Decimal(str(self.policy.raster_weight))
        base_weight = Decimal(1) - raster_weight
        weights = tuple(
            (
                base_weight * (Decimal(value) / base_total)
                + raster_weight
                * (
                    Decimal(str(evidence))
                    / raster_total_decimal
                )
            )
            for value, evidence in zip(
                original,
                eligible_population,
                strict=True,
            )
        )
        return _apportion_integer_total(
            total=zone_population,
            weights=weights,
        ), True

    def _validate_inputs(
        self,
        *,
        aggregation: DemographicAggregationResult,
        raster: PopulationRasterSamplingResult,
    ) -> None:
        if not isinstance(aggregation, DemographicAggregationResult):
            raise SpatialCalibrationError(
                "aggregation must be a DemographicAggregationResult"
            )
        if not isinstance(raster, PopulationRasterSamplingResult):
            raise SpatialCalibrationError(
                "raster must be a PopulationRasterSamplingResult"
            )
        if len(aggregation.blocks) > self.policy.max_blocks:
            raise SpatialCalibrationError(
                "spatial calibration block limit exceeded: "
                f"{len(aggregation.blocks)} > {self.policy.max_blocks}"
            )
        block_ids = tuple(block.block_id for block in aggregation.blocks)
        sample_ids = tuple(sample.subject_id for sample in raster.samples)
        if set(block_ids) != set(sample_ids):
            raise SpatialCalibrationError(
                "population raster samples must cover exactly the aggregated blocks"
            )


def _sample_is_eligible(
    sample: PopulationRasterSample,
    *,
    minimum_valid_fraction: float,
) -> bool:
    return (
        sample.has_valid_data
        and sample.valid_fraction >= minimum_valid_fraction
    )


def _apportion_integer_total(
    *,
    total: int,
    weights: tuple[Decimal, ...],
) -> tuple[int, ...]:
    _require_non_negative_int("total", total)
    if not weights:
        if total == 0:
            return ()
        raise SpatialCalibrationError(
            "positive total requires at least one weight"
        )
    if any(weight < 0 for weight in weights):
        raise SpatialCalibrationError(
            "calibration weights must be non-negative"
        )
    weight_sum = sum(weights, Decimal(0))
    if weight_sum <= 0:
        raise SpatialCalibrationError(
            "calibration weights must have a positive sum"
        )

    quotas = tuple(
        Decimal(total) * weight / weight_sum
        for weight in weights
    )
    base = [int(quota) for quota in quotas]
    remaining = total - sum(base)
    remainders = tuple(
        quota - Decimal(base[index])
        for index, quota in enumerate(quotas)
    )
    order = sorted(
        range(len(weights)),
        key=lambda index: (-remainders[index], index),
    )
    for index in order[:remaining]:
        base[index] += 1
    if sum(base) != total:
        raise SpatialCalibrationError(
            "population apportionment did not preserve zone total"
        )
    return tuple(base)


def _allocate_age_matrix(
    *,
    row_totals: tuple[int, ...],
    column_totals: tuple[int, ...],
) -> tuple[tuple[int, ...], ...]:
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        for value in (*row_totals, *column_totals)
    ):
        raise SpatialCalibrationError(
            "age-matrix margins must be non-negative integers"
        )
    if sum(row_totals) != sum(column_totals):
        raise SpatialCalibrationError(
            "age-matrix row and column totals must match"
        )

    remaining_columns = list(column_totals)
    remaining_population = sum(row_totals)
    rows: list[tuple[int, ...]] = []
    for row_total in row_totals:
        counts = _apportion_row(
            row_total=row_total,
            remaining_columns=tuple(remaining_columns),
            remaining_population=remaining_population,
        )
        rows.append(counts)
        for index, count in enumerate(counts):
            remaining_columns[index] -= count
        remaining_population -= row_total

    if remaining_population != 0 or any(remaining_columns):
        raise SpatialCalibrationError(
            "age-matrix allocation did not preserve exact margins"
        )
    return tuple(rows)


def _apportion_row(
    *,
    row_total: int,
    remaining_columns: tuple[int, ...],
    remaining_population: int,
) -> tuple[int, ...]:
    if row_total == 0:
        return tuple(0 for _ in remaining_columns)
    if remaining_population <= 0:
        raise SpatialCalibrationError(
            "positive age row requires remaining population"
        )

    base: list[int] = []
    remainders: list[tuple[int, int]] = []
    for index, column_total in enumerate(remaining_columns):
        numerator = row_total * column_total
        quotient, remainder = divmod(numerator, remaining_population)
        base.append(quotient)
        remainders.append((remainder, index))

    remaining = row_total - sum(base)
    candidates = sorted(
        (
            (remainder, index)
            for remainder, index in remainders
            if base[index] < remaining_columns[index]
        ),
        key=lambda item: (-item[0], item[1]),
    )
    if remaining > len(candidates):
        raise SpatialCalibrationError(
            "insufficient age-column capacity for calibrated block"
        )
    for _, index in candidates[:remaining]:
        base[index] += 1

    if sum(base) != row_total:
        raise SpatialCalibrationError(
            "age row allocation did not preserve block population"
        )
    return tuple(base)


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SpatialCalibrationError(
            f"{field_name} must be a non-empty string"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SpatialCalibrationError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SpatialCalibrationError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(
    field_name: str,
    value: int | float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpatialCalibrationError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise SpatialCalibrationError(
            f"{field_name} must be a finite non-negative number"
        )
    return number


def _require_ratio(field_name: str, value: int | float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number > 1.0:
        raise SpatialCalibrationError(
            f"{field_name} must be inside 0..1"
        )
    return number
