from __future__ import annotations

import math
from dataclasses import dataclass

from core.urban_generator.demography.age_allocation import (
    AgeGroupAllocationResult,
    AgeGroupPopulation,
)
from core.urban_generator.demography.allocation import PopulationAllocationResult
from core.urban_generator.demography.employment import EmploymentEstimateResult
from core.urban_generator.zoning import ZoneClass

DEFAULT_MAX_DEMOGRAPHIC_AGGREGATION_BUILDINGS = 100_000


class DemographicAggregationError(ValueError):
    """Raised when S09-T06 aggregation inputs violate the consistency contract."""


@dataclass(frozen=True, slots=True)
class BuildingAggregationRef:
    """Explicit spatial ownership for one demographic building row."""

    building_id: str
    block_id: str
    zone_id: str
    zone_class: ZoneClass

    def __post_init__(self) -> None:
        _require_id("building_id", self.building_id)
        _require_id("block_id", self.block_id)
        _require_id("zone_id", self.zone_id)
        if not isinstance(self.zone_class, ZoneClass):
            raise DemographicAggregationError(
                "zone_class must be a ZoneClass value"
            )


@dataclass(frozen=True, slots=True)
class BlockDemographicAggregate:
    """Generated demographic totals for one block and its unique zone."""

    block_id: str
    zone_id: str
    zone_class: ZoneClass
    building_count: int
    population: int
    age_groups: tuple[AgeGroupPopulation, ...]
    jobs_estimate: float

    def __post_init__(self) -> None:
        _require_id("block_id", self.block_id)
        _require_id("zone_id", self.zone_id)
        if not isinstance(self.zone_class, ZoneClass):
            raise DemographicAggregationError(
                "zone_class must be a ZoneClass value"
            )
        _require_positive_int("building_count", self.building_count)
        _require_non_negative_int("population", self.population)
        _validate_age_groups(
            self.age_groups,
            expected_population=self.population,
        )
        jobs = _require_non_negative_finite(
            "jobs_estimate",
            self.jobs_estimate,
        )
        object.__setattr__(self, "jobs_estimate", jobs)


@dataclass(frozen=True, slots=True)
class ZoneDemographicAggregate:
    """Generated demographic totals for one functional zone."""

    zone_id: str
    zone_class: ZoneClass
    block_count: int
    building_count: int
    population: int
    age_groups: tuple[AgeGroupPopulation, ...]
    jobs_estimate: float

    def __post_init__(self) -> None:
        _require_id("zone_id", self.zone_id)
        if not isinstance(self.zone_class, ZoneClass):
            raise DemographicAggregationError(
                "zone_class must be a ZoneClass value"
            )
        _require_positive_int("block_count", self.block_count)
        _require_positive_int("building_count", self.building_count)
        _require_non_negative_int("population", self.population)
        _validate_age_groups(
            self.age_groups,
            expected_population=self.population,
        )
        jobs = _require_non_negative_finite(
            "jobs_estimate",
            self.jobs_estimate,
        )
        object.__setattr__(self, "jobs_estimate", jobs)


@dataclass(frozen=True, slots=True)
class DemographicAggregationTotals:
    """Cross-level totals used to prove aggregation consistency."""

    building_count: int
    block_count: int
    zone_count: int
    population: int
    age_groups: tuple[AgeGroupPopulation, ...]
    jobs_estimate: float

    def __post_init__(self) -> None:
        _require_non_negative_int("building_count", self.building_count)
        _require_non_negative_int("block_count", self.block_count)
        _require_non_negative_int("zone_count", self.zone_count)
        _require_non_negative_int("population", self.population)
        _validate_age_groups(
            self.age_groups,
            expected_population=self.population,
        )
        jobs = _require_non_negative_finite(
            "jobs_estimate",
            self.jobs_estimate,
        )
        object.__setattr__(self, "jobs_estimate", jobs)


@dataclass(frozen=True, slots=True)
class DemographicAggregationResult:
    """Canonical block/zone totals with scenario and employment provenance."""

    scenario_version: str
    scenario_fingerprint: str
    employment_config_version: str
    employment_config_fingerprint: str
    blocks: tuple[BlockDemographicAggregate, ...]
    zones: tuple[ZoneDemographicAggregate, ...]
    totals: DemographicAggregationTotals

    def __post_init__(self) -> None:
        _require_non_empty_string("scenario_version", self.scenario_version)
        _require_sha256("scenario_fingerprint", self.scenario_fingerprint)
        _require_non_empty_string(
            "employment_config_version",
            self.employment_config_version,
        )
        _require_sha256(
            "employment_config_fingerprint",
            self.employment_config_fingerprint,
        )
        if not isinstance(self.blocks, tuple):
            raise DemographicAggregationError(
                "blocks must be an immutable tuple"
            )
        if any(
            not isinstance(item, BlockDemographicAggregate)
            for item in self.blocks
        ):
            raise DemographicAggregationError(
                "blocks must contain BlockDemographicAggregate values"
            )
        if not isinstance(self.zones, tuple):
            raise DemographicAggregationError(
                "zones must be an immutable tuple"
            )
        if any(
            not isinstance(item, ZoneDemographicAggregate)
            for item in self.zones
        ):
            raise DemographicAggregationError(
                "zones must contain ZoneDemographicAggregate values"
            )
        if not isinstance(self.totals, DemographicAggregationTotals):
            raise DemographicAggregationError(
                "totals must be DemographicAggregationTotals"
            )

        block_ids = tuple(item.block_id for item in self.blocks)
        if block_ids != tuple(sorted(block_ids)) or len(block_ids) != len(
            set(block_ids)
        ):
            raise DemographicAggregationError(
                "block aggregates must be sorted and unique"
            )
        zone_ids = tuple(item.zone_id for item in self.zones)
        if zone_ids != tuple(sorted(zone_ids)) or len(zone_ids) != len(
            set(zone_ids)
        ):
            raise DemographicAggregationError(
                "zone aggregates must be sorted and unique"
            )

        if self.totals.block_count != len(self.blocks):
            raise DemographicAggregationError(
                "totals block_count must match block aggregates"
            )
        if self.totals.zone_count != len(self.zones):
            raise DemographicAggregationError(
                "totals zone_count must match zone aggregates"
            )
        _validate_level_totals(
            blocks=self.blocks,
            zones=self.zones,
            totals=self.totals,
        )


class DemographicAggregator:
    """Join T03/T04/T05 by building id and aggregate exact block/zone totals."""

    def __init__(
        self,
        *,
        max_buildings: int = DEFAULT_MAX_DEMOGRAPHIC_AGGREGATION_BUILDINGS,
    ) -> None:
        _require_positive_int("max_buildings", max_buildings)
        self.max_buildings = max_buildings

    def aggregate(
        self,
        refs: tuple[BuildingAggregationRef, ...],
        *,
        population: PopulationAllocationResult,
        age_groups: AgeGroupAllocationResult,
        employment: EmploymentEstimateResult,
    ) -> DemographicAggregationResult:
        self._validate_inputs(
            refs=refs,
            population=population,
            age_groups=age_groups,
            employment=employment,
        )

        population_by_id = {
            item.building_id: item for item in population.allocations
        }
        age_by_id = {
            item.building_id: item for item in age_groups.buildings
        }
        jobs_by_id = {
            item.building_id: item for item in employment.buildings
        }
        ordered_refs = tuple(sorted(refs, key=lambda item: item.building_id))

        block_members: dict[str, list[str]] = {}
        block_meta: dict[str, tuple[str, ZoneClass]] = {}
        zone_members: dict[str, list[str]] = {}
        zone_classes: dict[str, ZoneClass] = {}

        for ref in ordered_refs:
            block_members.setdefault(ref.block_id, []).append(ref.building_id)
            existing_block = block_meta.get(ref.block_id)
            block_value = (ref.zone_id, ref.zone_class)
            if existing_block is not None and existing_block != block_value:
                raise DemographicAggregationError(
                    "all buildings in a block must reference the same zone"
                )
            block_meta[ref.block_id] = block_value

            zone_members.setdefault(ref.zone_id, []).append(ref.building_id)
            existing_zone_class = zone_classes.get(ref.zone_id)
            if (
                existing_zone_class is not None
                and existing_zone_class is not ref.zone_class
            ):
                raise DemographicAggregationError(
                    "zone_id must map to exactly one zone_class"
                )
            zone_classes[ref.zone_id] = ref.zone_class

        blocks = tuple(
            self._build_block(
                block_id=block_id,
                zone_id=block_meta[block_id][0],
                zone_class=block_meta[block_id][1],
                building_ids=tuple(block_members[block_id]),
                population_by_id=population_by_id,
                age_by_id=age_by_id,
                jobs_by_id=jobs_by_id,
                age_template=age_groups.totals,
            )
            for block_id in sorted(block_members)
        )
        zones = tuple(
            self._build_zone(
                zone_id=zone_id,
                zone_class=zone_classes[zone_id],
                building_ids=tuple(zone_members[zone_id]),
                block_ids=tuple(
                    sorted(
                        block.block_id
                        for block in blocks
                        if block.zone_id == zone_id
                    )
                ),
                population_by_id=population_by_id,
                age_by_id=age_by_id,
                jobs_by_id=jobs_by_id,
                age_template=age_groups.totals,
            )
            for zone_id in sorted(zone_members)
        )
        totals = DemographicAggregationTotals(
            building_count=len(ordered_refs),
            block_count=len(blocks),
            zone_count=len(zones),
            population=population.diagnostics.allocated_generated_population,
            age_groups=tuple(
                AgeGroupPopulation(
                    code=item.code,
                    min_age=item.min_age,
                    max_age=item.max_age,
                    residents=item.residents,
                )
                for item in age_groups.totals
            ),
            jobs_estimate=employment.summary.total_jobs_estimate,
        )
        return DemographicAggregationResult(
            scenario_version=population.scenario_version,
            scenario_fingerprint=population.scenario_fingerprint,
            employment_config_version=employment.config_version,
            employment_config_fingerprint=employment.config_fingerprint,
            blocks=blocks,
            zones=zones,
            totals=totals,
        )

    @staticmethod
    def _build_block(
        *,
        block_id: str,
        zone_id: str,
        zone_class: ZoneClass,
        building_ids: tuple[str, ...],
        population_by_id: dict[str, object],
        age_by_id: dict[str, object],
        jobs_by_id: dict[str, object],
        age_template: tuple[object, ...],
    ) -> BlockDemographicAggregate:
        population, cohorts, jobs = _aggregate_members(
            building_ids=building_ids,
            population_by_id=population_by_id,
            age_by_id=age_by_id,
            jobs_by_id=jobs_by_id,
            age_template=age_template,
        )
        return BlockDemographicAggregate(
            block_id=block_id,
            zone_id=zone_id,
            zone_class=zone_class,
            building_count=len(building_ids),
            population=population,
            age_groups=cohorts,
            jobs_estimate=jobs,
        )

    @staticmethod
    def _build_zone(
        *,
        zone_id: str,
        zone_class: ZoneClass,
        building_ids: tuple[str, ...],
        block_ids: tuple[str, ...],
        population_by_id: dict[str, object],
        age_by_id: dict[str, object],
        jobs_by_id: dict[str, object],
        age_template: tuple[object, ...],
    ) -> ZoneDemographicAggregate:
        population, cohorts, jobs = _aggregate_members(
            building_ids=building_ids,
            population_by_id=population_by_id,
            age_by_id=age_by_id,
            jobs_by_id=jobs_by_id,
            age_template=age_template,
        )
        return ZoneDemographicAggregate(
            zone_id=zone_id,
            zone_class=zone_class,
            block_count=len(block_ids),
            building_count=len(building_ids),
            population=population,
            age_groups=cohorts,
            jobs_estimate=jobs,
        )

    def _validate_inputs(
        self,
        *,
        refs: tuple[BuildingAggregationRef, ...],
        population: PopulationAllocationResult,
        age_groups: AgeGroupAllocationResult,
        employment: EmploymentEstimateResult,
    ) -> None:
        if not isinstance(refs, tuple):
            raise DemographicAggregationError(
                "refs must be an immutable tuple"
            )
        if len(refs) > self.max_buildings:
            raise DemographicAggregationError(
                "demographic aggregation building limit exceeded: "
                f"{len(refs)} > {self.max_buildings}"
            )
        if any(not isinstance(item, BuildingAggregationRef) for item in refs):
            raise DemographicAggregationError(
                "refs must contain BuildingAggregationRef values"
            )
        if not isinstance(population, PopulationAllocationResult):
            raise DemographicAggregationError(
                "population must be PopulationAllocationResult"
            )
        if not isinstance(age_groups, AgeGroupAllocationResult):
            raise DemographicAggregationError(
                "age_groups must be AgeGroupAllocationResult"
            )
        if not isinstance(employment, EmploymentEstimateResult):
            raise DemographicAggregationError(
                "employment must be EmploymentEstimateResult"
            )

        provenance = (
            population.scenario_version,
            population.scenario_fingerprint,
        )
        if (
            age_groups.scenario_version,
            age_groups.scenario_fingerprint,
        ) != provenance:
            raise DemographicAggregationError(
                "age-group scenario provenance must match population"
            )
        if (
            employment.scenario_version,
            employment.scenario_fingerprint,
        ) != provenance:
            raise DemographicAggregationError(
                "employment scenario provenance must match population"
            )

        ref_ids = tuple(item.building_id for item in refs)
        if len(ref_ids) != len(set(ref_ids)):
            raise DemographicAggregationError(
                "building aggregation refs must be unique"
            )
        population_ids = tuple(
            item.building_id for item in population.allocations
        )
        age_ids = tuple(item.building_id for item in age_groups.buildings)
        employment_ids = tuple(
            item.building_id for item in employment.buildings
        )
        expected = set(population_ids)
        if set(ref_ids) != expected:
            raise DemographicAggregationError(
                "refs and population must contain identical building ids"
            )
        if set(age_ids) != expected:
            raise DemographicAggregationError(
                "age groups and population must contain identical building ids"
            )
        if set(employment_ids) != expected:
            raise DemographicAggregationError(
                "employment and population must contain identical building ids"
            )

        population_by_id = {
            item.building_id: item for item in population.allocations
        }
        for item in age_groups.buildings:
            if item.total_residents != population_by_id[item.building_id].residents:
                raise DemographicAggregationError(
                    "building age totals must match population allocations"
                )
        allocated_population = (
            population.diagnostics.allocated_generated_population
        )
        if age_groups.total_population != allocated_population:
            raise DemographicAggregationError(
                "age total population must match allocated population"
            )
        if employment.summary.generated_population != allocated_population:
            raise DemographicAggregationError(
                "employment population must match allocated population"
            )


def _aggregate_members(
    *,
    building_ids: tuple[str, ...],
    population_by_id: dict[str, object],
    age_by_id: dict[str, object],
    jobs_by_id: dict[str, object],
    age_template: tuple[object, ...],
) -> tuple[int, tuple[AgeGroupPopulation, ...], float]:
    population = 0
    jobs_values: list[float] = []
    cohort_counts = [0 for _ in age_template]

    for building_id in building_ids:
        population_item = population_by_id[building_id]
        age_item = age_by_id[building_id]
        jobs_item = jobs_by_id[building_id]
        population += int(getattr(population_item, "residents"))
        jobs_values.append(float(getattr(jobs_item, "jobs_estimate")))
        building_groups = tuple(getattr(age_item, "age_groups"))
        if len(building_groups) != len(age_template):
            raise DemographicAggregationError(
                "building age groups must match age total count"
            )
        for index, group in enumerate(building_groups):
            template = age_template[index]
            if (
                getattr(group, "code") != getattr(template, "code")
                or getattr(group, "min_age") != getattr(template, "min_age")
                or getattr(group, "max_age") != getattr(template, "max_age")
            ):
                raise DemographicAggregationError(
                    "building age group metadata must match age totals"
                )
            cohort_counts[index] += int(getattr(group, "residents"))

    cohorts = tuple(
        AgeGroupPopulation(
            code=str(getattr(template, "code")),
            min_age=int(getattr(template, "min_age")),
            max_age=getattr(template, "max_age"),
            residents=cohort_counts[index],
        )
        for index, template in enumerate(age_template)
    )
    return population, cohorts, math.fsum(jobs_values)


def _validate_level_totals(
    *,
    blocks: tuple[BlockDemographicAggregate, ...],
    zones: tuple[ZoneDemographicAggregate, ...],
    totals: DemographicAggregationTotals,
) -> None:
    for label, items in (("block", blocks), ("zone", zones)):
        if sum(item.building_count for item in items) != totals.building_count:
            raise DemographicAggregationError(
                f"{label} building counts must preserve total building_count"
            )
        if sum(item.population for item in items) != totals.population:
            raise DemographicAggregationError(
                f"{label} population must preserve total population"
            )
        if not math.isclose(
            math.fsum(item.jobs_estimate for item in items),
            totals.jobs_estimate,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise DemographicAggregationError(
                f"{label} jobs must preserve total jobs_estimate"
            )
        for index, total_group in enumerate(totals.age_groups):
            if sum(item.age_groups[index].residents for item in items) != (
                total_group.residents
            ):
                raise DemographicAggregationError(
                    f"{label} age groups must preserve cohort totals"
                )


def _validate_age_groups(
    groups: tuple[AgeGroupPopulation, ...],
    *,
    expected_population: int,
) -> None:
    if not isinstance(groups, tuple) or not groups:
        raise DemographicAggregationError(
            "age_groups must be a non-empty immutable tuple"
        )
    if any(not isinstance(item, AgeGroupPopulation) for item in groups):
        raise DemographicAggregationError(
            "age_groups must contain AgeGroupPopulation values"
        )
    codes = tuple(item.code for item in groups)
    if len(codes) != len(set(codes)):
        raise DemographicAggregationError(
            "age group codes must be unique"
        )
    if sum(item.residents for item in groups) != expected_population:
        raise DemographicAggregationError(
            "age group residents must sum to population"
        )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DemographicAggregationError(
            f"{field_name} must be a non-empty string"
        )
    if "\n" in value or "\r" in value:
        raise DemographicAggregationError(
            f"{field_name} must not contain line breaks"
        )


def _require_non_empty_string(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise DemographicAggregationError(
            f"{field_name} must be a non-empty string"
        )


def _require_sha256(field_name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise DemographicAggregationError(
            f"{field_name} must be a lowercase SHA-256 hex digest"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DemographicAggregationError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DemographicAggregationError(
            f"{field_name} must be a non-negative integer"
        )


def _require_non_negative_finite(
    field_name: str,
    value: int | float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DemographicAggregationError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise DemographicAggregationError(
            f"{field_name} must be a finite non-negative number"
        )
    return number
