from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.zoning import ZoneClass


class InfrastructureTypeError(ValueError):
    """Raised when an S10 infrastructure type violates the core contract."""


class InfrastructureCategory(StrEnum):
    """Minimum v1 infrastructure categories from the development plan."""

    EDUCATION = "education"
    HEALTHCARE = "healthcare"
    RETAIL = "retail"
    RECREATION = "recreation"


class InfrastructureCandidateSource(StrEnum):
    """Spatial entities that may host a generated facility candidate."""

    BLOCK = "block"
    PARCEL = "parcel"
    BUILDING = "building"


@dataclass(frozen=True, slots=True)
class InfrastructureDemandModel:
    """Map one S09 demographic signal into infrastructure capacity demand."""

    signal: DemographicDemandCategory
    demand_rate: float
    demographic_group: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.signal, DemographicDemandCategory):
            raise InfrastructureTypeError(
                "signal must be a DemographicDemandCategory"
            )
        rate = _require_positive_finite("demand_rate", self.demand_rate)
        if self.signal is DemographicDemandCategory.AGE_GROUP:
            _require_code("demographic_group", self.demographic_group)
        elif self.demographic_group is not None:
            raise InfrastructureTypeError(
                "demographic_group is only valid for age_group demand"
            )
        object.__setattr__(self, "demand_rate", rate)


@dataclass(frozen=True, slots=True)
class InfrastructureCandidatePolicy:
    """Canonical sources considered by the later bounded candidate generator."""

    sources: tuple[InfrastructureCandidateSource, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.sources, tuple):
            raise InfrastructureTypeError(
                "candidate sources must be an immutable tuple"
            )
        if not self.sources:
            raise InfrastructureTypeError(
                "candidate sources must not be empty"
            )
        if any(
            not isinstance(item, InfrastructureCandidateSource)
            for item in self.sources
        ):
            raise InfrastructureTypeError(
                "candidate sources must contain InfrastructureCandidateSource values"
            )
        canonical = tuple(sorted(set(self.sources), key=lambda item: item.value))
        object.__setattr__(self, "sources", canonical)


@dataclass(frozen=True, slots=True)
class InfrastructureType:
    """Immutable, versioned S10 facility type and feasibility assumptions."""

    version: str
    code: str
    category: InfrastructureCategory
    demand_model: InfrastructureDemandModel
    capacity: float
    max_network_distance_m: float
    allowed_zones: tuple[ZoneClass, ...]
    minimum_site_area_m2: float
    target_site_area_m2: float
    candidate_policy: InfrastructureCandidatePolicy

    def __post_init__(self) -> None:
        _validate_version(self.version)
        _require_code("code", self.code)
        if not isinstance(self.category, InfrastructureCategory):
            raise InfrastructureTypeError(
                "category must be an InfrastructureCategory"
            )
        if not isinstance(self.demand_model, InfrastructureDemandModel):
            raise InfrastructureTypeError(
                "demand_model must be InfrastructureDemandModel"
            )
        capacity = _require_positive_finite("capacity", self.capacity)
        max_distance = _require_positive_finite(
            "max_network_distance_m",
            self.max_network_distance_m,
        )
        minimum_area = _require_positive_finite(
            "minimum_site_area_m2",
            self.minimum_site_area_m2,
        )
        target_area = _require_positive_finite(
            "target_site_area_m2",
            self.target_site_area_m2,
        )
        if target_area < minimum_area:
            raise InfrastructureTypeError(
                "target_site_area_m2 must be >= minimum_site_area_m2"
            )

        if not isinstance(self.allowed_zones, tuple):
            raise InfrastructureTypeError(
                "allowed_zones must be an immutable tuple"
            )
        if not self.allowed_zones:
            raise InfrastructureTypeError(
                "allowed_zones must not be empty"
            )
        if any(not isinstance(item, ZoneClass) for item in self.allowed_zones):
            raise InfrastructureTypeError(
                "allowed_zones must contain ZoneClass values"
            )
        canonical_zones = tuple(
            sorted(set(self.allowed_zones), key=lambda item: item.value)
        )
        if not isinstance(
            self.candidate_policy,
            InfrastructureCandidatePolicy,
        ):
            raise InfrastructureTypeError(
                "candidate_policy must be InfrastructureCandidatePolicy"
            )

        object.__setattr__(self, "capacity", capacity)
        object.__setattr__(self, "max_network_distance_m", max_distance)
        object.__setattr__(self, "allowed_zones", canonical_zones)
        object.__setattr__(self, "minimum_site_area_m2", minimum_area)
        object.__setattr__(self, "target_site_area_m2", target_area)

    @property
    def fingerprint(self) -> str:
        """Stable canonical content fingerprint for provenance and cache keys."""

        payload = {
            "version": self.version,
            "code": self.code,
            "category": self.category.value,
            "demand_model": {
                "signal": self.demand_model.signal.value,
                "demand_rate": self.demand_model.demand_rate,
                "demographic_group": self.demand_model.demographic_group,
            },
            "capacity": self.capacity,
            "max_network_distance_m": self.max_network_distance_m,
            "allowed_zones": [item.value for item in self.allowed_zones],
            "minimum_site_area_m2": self.minimum_site_area_m2,
            "target_site_area_m2": self.target_site_area_m2,
            "candidate_policy": {
                "sources": [
                    item.value for item in self.candidate_policy.sources
                ],
            },
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _validate_version(value: str) -> None:
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        raise InfrastructureTypeError(
            f"invalid infrastructure type version: {value!r}"
        )


def _require_code(field_name: str, value: str | None) -> None:
    if not isinstance(value, str) or _CODE_RE.fullmatch(value) is None:
        raise InfrastructureTypeError(
            f"invalid {field_name}: {value!r}"
        )


def _require_positive_finite(
    field_name: str,
    value: int | float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InfrastructureTypeError(
            f"{field_name} must be a finite positive number"
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise InfrastructureTypeError(
            f"{field_name} must be a finite positive number"
        )
    return number


_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,63}$")
