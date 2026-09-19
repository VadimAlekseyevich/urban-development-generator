from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum


class DemographicScenarioError(ValueError):
    """Raised when demographic scenario configuration violates the core contract."""


class PopulationTargetKind(StrEnum):
    """How the scenario expresses its population objective."""

    TOTAL_POPULATION = "total_population"
    GROWTH_RATE = "growth_rate"


@dataclass(frozen=True, slots=True)
class PopulationTarget:
    """Absolute target population or fractional growth relative to a baseline."""

    kind: PopulationTargetKind
    value: int | float

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PopulationTargetKind):
            raise DemographicScenarioError(
                "population target kind must be a PopulationTargetKind value"
            )

        if self.kind is PopulationTargetKind.TOTAL_POPULATION:
            if (
                isinstance(self.value, bool)
                or not isinstance(self.value, int)
                or self.value < 0
            ):
                raise DemographicScenarioError(
                    "total population target must be a non-negative integer"
                )
            return

        value = _require_finite_number("growth_rate", self.value)
        if value <= -1.0:
            raise DemographicScenarioError(
                "growth_rate must be greater than -1.0"
            )
        object.__setattr__(self, "value", value)

    @property
    def total_population(self) -> int | None:
        if self.kind is PopulationTargetKind.TOTAL_POPULATION:
            return int(self.value)
        return None

    @property
    def growth_rate(self) -> float | None:
        if self.kind is PopulationTargetKind.GROWTH_RATE:
            return float(self.value)
        return None


@dataclass(frozen=True, slots=True)
class AgeGroupShare:
    """One exhaustive, non-overlapping age interval and its population share."""

    code: str
    min_age: int
    max_age: int | None
    share: float

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or _AGE_GROUP_CODE_RE.fullmatch(self.code) is None:
            raise DemographicScenarioError(f"invalid age group code: {self.code!r}")
        _require_non_negative_int("min_age", self.min_age)
        if self.max_age is not None:
            _require_non_negative_int("max_age", self.max_age)
            if self.max_age < self.min_age:
                raise DemographicScenarioError("max_age must be >= min_age")

        share = _require_finite_number("age group share", self.share)
        if share < 0.0 or share > 1.0:
            raise DemographicScenarioError("age group share must be inside 0..1")
        object.__setattr__(self, "share", share)


@dataclass(frozen=True, slots=True)
class DemographicScenario:
    """Immutable, versioned demographic assumptions for the S09 pipeline."""

    version: str
    population_target: PopulationTarget
    occupancy_ratio: float
    residential_area_per_person_m2: float
    average_household_size: float
    residential_gfa_share: float
    age_groups: tuple[AgeGroupShare, ...]
    working_population_ratio: float

    def __post_init__(self) -> None:
        _validate_version(self.version)
        if not isinstance(self.population_target, PopulationTarget):
            raise DemographicScenarioError(
                "population_target must be a PopulationTarget"
            )

        occupancy = _require_ratio("occupancy_ratio", self.occupancy_ratio)
        area_per_person = _require_positive_finite(
            "residential_area_per_person_m2",
            self.residential_area_per_person_m2,
        )
        household_size = _require_positive_finite(
            "average_household_size",
            self.average_household_size,
        )
        residential_gfa_share = _require_ratio(
            "residential_gfa_share",
            self.residential_gfa_share,
        )
        working_ratio = _require_ratio(
            "working_population_ratio",
            self.working_population_ratio,
        )

        if not isinstance(self.age_groups, tuple):
            raise DemographicScenarioError("age_groups must be an immutable tuple")
        if not self.age_groups:
            raise DemographicScenarioError("age_groups must not be empty")
        if any(not isinstance(item, AgeGroupShare) for item in self.age_groups):
            raise DemographicScenarioError(
                "age_groups must contain only AgeGroupShare values"
            )

        canonical_groups = tuple(
            sorted(
                self.age_groups,
                key=lambda item: (
                    item.min_age,
                    math.inf if item.max_age is None else item.max_age,
                    item.code,
                ),
            )
        )
        codes = tuple(item.code for item in canonical_groups)
        if len(codes) != len(set(codes)):
            raise DemographicScenarioError("age group codes must be unique")

        _validate_age_partition(canonical_groups)
        total_share = math.fsum(item.share for item in canonical_groups)
        if not math.isclose(
            total_share,
            1.0,
            rel_tol=0.0,
            abs_tol=_SHARE_SUM_TOLERANCE,
        ):
            raise DemographicScenarioError(
                "age group shares must sum to 1.0 "
                f"within {_SHARE_SUM_TOLERANCE:g}; got {total_share:.17g}"
            )

        object.__setattr__(self, "occupancy_ratio", occupancy)
        object.__setattr__(
            self,
            "residential_area_per_person_m2",
            area_per_person,
        )
        object.__setattr__(self, "average_household_size", household_size)
        object.__setattr__(self, "residential_gfa_share", residential_gfa_share)
        object.__setattr__(self, "age_groups", canonical_groups)
        object.__setattr__(self, "working_population_ratio", working_ratio)

    @property
    def vacancy_ratio(self) -> float:
        """Share of residential capacity not expected to be occupied."""

        return 1.0 - self.occupancy_ratio

    def age_group(self, code: str) -> AgeGroupShare:
        if not isinstance(code, str):
            raise DemographicScenarioError("age group lookup code must be a string")
        for group in self.age_groups:
            if group.code == code:
                return group
        raise DemographicScenarioError(f"unknown age group code: {code!r}")

    @property
    def fingerprint(self) -> str:
        """Stable canonical content fingerprint for provenance/cache keys."""

        payload = {
            "version": self.version,
            "population_target": {
                "kind": self.population_target.kind.value,
                "value": self.population_target.value,
            },
            "occupancy_ratio": self.occupancy_ratio,
            "residential_area_per_person_m2": self.residential_area_per_person_m2,
            "average_household_size": self.average_household_size,
            "residential_gfa_share": self.residential_gfa_share,
            "age_groups": [
                {
                    "code": group.code,
                    "min_age": group.min_age,
                    "max_age": group.max_age,
                    "share": group.share,
                }
                for group in self.age_groups
            ],
            "working_population_ratio": self.working_population_ratio,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _validate_age_partition(groups: tuple[AgeGroupShare, ...]) -> None:
    if groups[0].min_age != 0:
        raise DemographicScenarioError("age group partition must start at age 0")

    previous_max: int | None = None
    for index, group in enumerate(groups):
        if index > 0:
            if previous_max is None:
                raise DemographicScenarioError(
                    "open-ended age group must be the final age group"
                )
            if group.min_age != previous_max + 1:
                raise DemographicScenarioError(
                    "age groups must form a contiguous non-overlapping partition"
                )
        previous_max = group.max_age

    if groups[-1].max_age is not None:
        raise DemographicScenarioError(
            "final age group must be open-ended with max_age=None"
        )


def _validate_version(version: str) -> None:
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise DemographicScenarioError(
            f"invalid demographic scenario version: {version!r}"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DemographicScenarioError(
            f"{field_name} must be a non-negative integer"
        )


def _require_finite_number(field_name: str, value: int | float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DemographicScenarioError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise DemographicScenarioError(f"{field_name} must be a finite number")
    return number


def _require_positive_finite(field_name: str, value: int | float) -> float:
    number = _require_finite_number(field_name, value)
    if number <= 0.0:
        raise DemographicScenarioError(f"{field_name} must be greater than zero")
    return number


def _require_ratio(field_name: str, value: int | float) -> float:
    number = _require_finite_number(field_name, value)
    if number < 0.0 or number > 1.0:
        raise DemographicScenarioError(f"{field_name} must be inside 0..1")
    return number


_SHARE_SUM_TOLERANCE = 1e-9
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_AGE_GROUP_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
