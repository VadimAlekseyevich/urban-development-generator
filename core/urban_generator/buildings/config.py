from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum

from core.urban_generator.zoning import ZoneClass


class BuildingConfigError(ValueError):
    """Raised when building archetype configuration violates the core contract."""


class BuildingArchetype(StrEnum):
    """Canonical v1 building archetype codes.

    The enum names the data-driven archetype family only. Geometry generation belongs to
    the later S08 footprint-strategy work items.
    """

    DETACHED = "detached"
    POINT = "point"
    BAR = "bar"
    PERIMETER = "perimeter"
    COURTYARD = "courtyard"
    PUBLIC = "public"
    COMMERCIAL = "commercial"


class BuildingFootprintStrategy(StrEnum):
    """Geometry strategy token consumed by later S08 placement stages."""

    RECTANGULAR_POINT = "rectangular_point"
    BAR = "bar"
    PERIMETER = "perimeter"
    COURTYARD = "courtyard"


class BuildingPlacementScope(StrEnum):
    """Whether an archetype is placed from parcel or block-level candidates."""

    PARCEL = "parcel"
    BLOCK = "block"
    PARCEL_OR_BLOCK = "parcel_or_block"


_ARCHETYPE_ORDER = {
    archetype: index for index, archetype in enumerate(BuildingArchetype)
}
_ZONE_ORDER = {zone_class: index for index, zone_class in enumerate(ZoneClass)}
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class BuildingArchetypeConfig:
    """One data-driven archetype profile without placement/constraint execution."""

    archetype: BuildingArchetype
    footprint_strategy: BuildingFootprintStrategy
    placement_scope: BuildingPlacementScope
    allowed_zones: tuple[ZoneClass, ...]
    selection_weight: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.archetype, BuildingArchetype):
            raise BuildingConfigError("archetype must be a BuildingArchetype value")
        if not isinstance(self.footprint_strategy, BuildingFootprintStrategy):
            raise BuildingConfigError(
                "footprint_strategy must be a BuildingFootprintStrategy value"
            )
        if not isinstance(self.placement_scope, BuildingPlacementScope):
            raise BuildingConfigError(
                "placement_scope must be a BuildingPlacementScope value"
            )
        if not isinstance(self.allowed_zones, tuple):
            raise BuildingConfigError("allowed_zones must be an immutable tuple")
        if not self.allowed_zones:
            raise BuildingConfigError("allowed_zones must contain at least one zone class")
        if any(not isinstance(zone, ZoneClass) for zone in self.allowed_zones):
            raise BuildingConfigError("allowed_zones must contain only ZoneClass values")

        canonical_zones = tuple(
            sorted(self.allowed_zones, key=lambda zone: _ZONE_ORDER[zone])
        )
        if len(canonical_zones) != len(set(canonical_zones)):
            raise BuildingConfigError("allowed_zones must be unique")
        object.__setattr__(self, "allowed_zones", canonical_zones)

        selection_weight = _require_finite_number(
            "selection_weight",
            self.selection_weight,
        )
        if selection_weight <= 0.0:
            raise BuildingConfigError("selection_weight must be greater than zero")
        object.__setattr__(self, "selection_weight", selection_weight)

    def allows_zone(self, zone_class: ZoneClass) -> bool:
        """Return whether the profile may be selected for the given functional zone."""

        _require_zone_class(zone_class)
        return zone_class in self.allowed_zones


@dataclass(frozen=True, slots=True)
class BuildingConfig:
    """Immutable, versioned archetype catalog for the S08 building stage."""

    version: str
    archetypes: tuple[BuildingArchetypeConfig, ...]

    def __post_init__(self) -> None:
        _validate_version(self.version)
        if not isinstance(self.archetypes, tuple):
            raise BuildingConfigError("archetypes must be an immutable tuple")
        if not self.archetypes:
            raise BuildingConfigError("building config must contain archetype profiles")
        if any(
            not isinstance(item, BuildingArchetypeConfig)
            for item in self.archetypes
        ):
            raise BuildingConfigError(
                "archetypes must contain only BuildingArchetypeConfig values"
            )

        configured = tuple(item.archetype for item in self.archetypes)
        if len(configured) != len(set(configured)):
            raise BuildingConfigError("building archetypes must be unique")

        canonical = tuple(
            sorted(
                self.archetypes,
                key=lambda item: _ARCHETYPE_ORDER[item.archetype],
            )
        )
        object.__setattr__(self, "archetypes", canonical)

    def archetype(
        self,
        archetype: BuildingArchetype,
    ) -> BuildingArchetypeConfig:
        """Return one configured archetype profile."""

        _require_archetype(archetype)
        for profile in self.archetypes:
            if profile.archetype is archetype:
                return profile
        raise BuildingConfigError(f"unconfigured building archetype: {archetype.value}")

    def eligible_archetypes(
        self,
        zone_class: ZoneClass,
    ) -> tuple[BuildingArchetypeConfig, ...]:
        """Return canonical profiles allowed in one zone.

        The method deliberately does not normalize or sample weights. Candidate creation and
        seeded selection belong to later S08 work items.
        """

        _require_zone_class(zone_class)
        return tuple(
            profile
            for profile in self.archetypes
            if profile.allows_zone(zone_class)
        )

    @property
    def fingerprint(self) -> str:
        """Stable canonical content fingerprint for provenance/checkpoint keys."""

        payload = {
            "version": self.version,
            "archetypes": [
                {
                    "archetype": profile.archetype.value,
                    "footprint_strategy": profile.footprint_strategy.value,
                    "placement_scope": profile.placement_scope.value,
                    "allowed_zones": [
                        zone.value for zone in profile.allowed_zones
                    ],
                    "selection_weight": profile.selection_weight,
                }
                for profile in self.archetypes
            ],
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _validate_version(version: str) -> None:
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise BuildingConfigError(f"invalid building config version: {version!r}")


def _require_finite_number(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuildingConfigError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise BuildingConfigError(f"{field_name} must be a finite number")
    return number


def _require_archetype(value: BuildingArchetype) -> None:
    if not isinstance(value, BuildingArchetype):
        raise BuildingConfigError(
            "archetype lookup requires a BuildingArchetype value"
        )


def _require_zone_class(value: ZoneClass) -> None:
    if not isinstance(value, ZoneClass):
        raise BuildingConfigError("zone lookup requires a ZoneClass value")
