from __future__ import annotations

import math
from dataclasses import dataclass

from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingAreaMetrics,
    BuildingUse,
)
from core.urban_generator.demography.config import DemographicScenario

DEFAULT_MAX_RESIDENTIAL_CAPACITY_SUBJECTS = 100_000


class ResidentialCapacityError(ValueError):
    """Raised when S09-T02 residential capacity inputs violate the contract."""


@dataclass(frozen=True, slots=True)
class ResidentialCapacitySubject:
    """Authoritative S08 building metrics and assigned use for one building."""

    metrics: BuildingAreaMetrics
    attributes: AssignedBuildingAttributes

    def __post_init__(self) -> None:
        if not isinstance(self.metrics, BuildingAreaMetrics):
            raise ResidentialCapacityError(
                "metrics must be BuildingAreaMetrics"
            )
        if not isinstance(self.attributes, AssignedBuildingAttributes):
            raise ResidentialCapacityError(
                "attributes must be AssignedBuildingAttributes"
            )
        if self.metrics.building_id != self.attributes.building_id:
            raise ResidentialCapacityError(
                "building metric and attribute ids must match"
            )
        if self.metrics.floors != self.attributes.floors:
            raise ResidentialCapacityError(
                "building metric and attribute floors must match"
            )

    @property
    def building_id(self) -> str:
        return self.metrics.building_id


@dataclass(frozen=True, slots=True)
class ResidentialBuildingCapacity:
    """Fractional residential capacity derived without integer allocation."""

    building_id: str
    use: BuildingUse
    total_gfa_m2: float
    residential_gfa_m2: float
    occupied_residential_area_m2: float
    resident_capacity: float
    household_capacity: float

    def __post_init__(self) -> None:
        if not isinstance(self.building_id, str) or not self.building_id:
            raise ResidentialCapacityError("building_id must be a non-empty string")
        if not isinstance(self.use, BuildingUse):
            raise ResidentialCapacityError("use must be a BuildingUse value")

        total_gfa = _require_non_negative_finite("total_gfa_m2", self.total_gfa_m2)
        residential_gfa = _require_non_negative_finite(
            "residential_gfa_m2",
            self.residential_gfa_m2,
        )
        occupied_area = _require_non_negative_finite(
            "occupied_residential_area_m2",
            self.occupied_residential_area_m2,
        )
        resident_capacity = _require_non_negative_finite(
            "resident_capacity",
            self.resident_capacity,
        )
        household_capacity = _require_non_negative_finite(
            "household_capacity",
            self.household_capacity,
        )

        if residential_gfa > total_gfa + _AREA_EPSILON_M2:
            raise ResidentialCapacityError(
                "residential_gfa_m2 cannot exceed total_gfa_m2"
            )
        if occupied_area > residential_gfa + _AREA_EPSILON_M2:
            raise ResidentialCapacityError(
                "occupied residential area cannot exceed residential GFA"
            )

        object.__setattr__(self, "total_gfa_m2", total_gfa)
        object.__setattr__(self, "residential_gfa_m2", residential_gfa)
        object.__setattr__(
            self,
            "occupied_residential_area_m2",
            occupied_area,
        )
        object.__setattr__(self, "resident_capacity", resident_capacity)
        object.__setattr__(self, "household_capacity", household_capacity)


@dataclass(frozen=True, slots=True)
class ResidentialCapacityResult:
    """Canonical S09-T02 capacities plus demographic scenario provenance."""

    scenario_version: str
    scenario_fingerprint: str
    buildings: tuple[ResidentialBuildingCapacity, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_version, str) or not self.scenario_version:
            raise ResidentialCapacityError(
                "scenario_version must be a non-empty string"
            )
        if (
            not isinstance(self.scenario_fingerprint, str)
            or len(self.scenario_fingerprint) != 64
            or any(ch not in "0123456789abcdef" for ch in self.scenario_fingerprint)
        ):
            raise ResidentialCapacityError(
                "scenario_fingerprint must be a lowercase SHA-256 hex digest"
            )
        if not isinstance(self.buildings, tuple):
            raise ResidentialCapacityError(
                "buildings must be an immutable tuple"
            )
        if any(
            not isinstance(item, ResidentialBuildingCapacity)
            for item in self.buildings
        ):
            raise ResidentialCapacityError(
                "buildings must contain ResidentialBuildingCapacity values"
            )
        ids = tuple(item.building_id for item in self.buildings)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ResidentialCapacityError(
                "building capacity ids must be sorted and unique"
            )


class ResidentialCapacityCalculator:
    """Deterministically derive fractional residential capacity from S08 GFA."""

    def __init__(
        self,
        *,
        max_subjects: int = DEFAULT_MAX_RESIDENTIAL_CAPACITY_SUBJECTS,
    ) -> None:
        _require_positive_int("max_subjects", max_subjects)
        self.max_subjects = max_subjects

    def calculate(
        self,
        subjects: tuple[ResidentialCapacitySubject, ...],
        *,
        scenario: DemographicScenario,
    ) -> ResidentialCapacityResult:
        self._validate_inputs(subjects=subjects, scenario=scenario)

        buildings = tuple(
            self._calculate_subject(subject, scenario=scenario)
            for subject in sorted(subjects, key=lambda item: item.building_id)
        )
        return ResidentialCapacityResult(
            scenario_version=scenario.version,
            scenario_fingerprint=scenario.fingerprint,
            buildings=buildings,
        )

    def _calculate_subject(
        self,
        subject: ResidentialCapacitySubject,
        *,
        scenario: DemographicScenario,
    ) -> ResidentialBuildingCapacity:
        total_gfa = subject.metrics.gfa_m2
        residential_share = _residential_share(
            subject.attributes.use,
            mixed_residential_share=scenario.residential_gfa_share,
        )
        residential_gfa = total_gfa * residential_share
        occupied_area = residential_gfa * scenario.occupancy_ratio
        resident_capacity = (
            occupied_area / scenario.residential_area_per_person_m2
        )
        household_capacity = resident_capacity / scenario.average_household_size

        return ResidentialBuildingCapacity(
            building_id=subject.building_id,
            use=subject.attributes.use,
            total_gfa_m2=total_gfa,
            residential_gfa_m2=residential_gfa,
            occupied_residential_area_m2=occupied_area,
            resident_capacity=resident_capacity,
            household_capacity=household_capacity,
        )

    def _validate_inputs(
        self,
        *,
        subjects: tuple[ResidentialCapacitySubject, ...],
        scenario: DemographicScenario,
    ) -> None:
        if not isinstance(subjects, tuple):
            raise ResidentialCapacityError(
                "subjects must be an immutable tuple"
            )
        if len(subjects) > self.max_subjects:
            raise ResidentialCapacityError(
                "residential capacity subject limit exceeded: "
                f"{len(subjects)} > {self.max_subjects}"
            )
        if any(
            not isinstance(item, ResidentialCapacitySubject)
            for item in subjects
        ):
            raise ResidentialCapacityError(
                "subjects must contain ResidentialCapacitySubject values"
            )
        ids = tuple(item.building_id for item in subjects)
        if len(ids) != len(set(ids)):
            raise ResidentialCapacityError("building ids must be unique")
        if not isinstance(scenario, DemographicScenario):
            raise ResidentialCapacityError(
                "scenario must be a DemographicScenario"
            )


def _residential_share(
    use: BuildingUse,
    *,
    mixed_residential_share: float,
) -> float:
    if use is BuildingUse.RESIDENTIAL:
        return 1.0
    if use is BuildingUse.MIXED:
        return mixed_residential_share
    if use in (BuildingUse.PUBLIC, BuildingUse.COMMERCIAL):
        return 0.0
    raise ResidentialCapacityError(f"unsupported building use: {use!r}")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ResidentialCapacityError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResidentialCapacityError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ResidentialCapacityError(
            f"{field_name} must be a finite non-negative number"
        )
    return number


_AREA_EPSILON_M2 = 1e-9
