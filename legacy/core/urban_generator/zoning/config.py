from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum


class ZoningConfigError(ValueError):
    """Raised when functional zoning configuration violates the core contract."""


class ZoneClass(StrEnum):
    """Canonical v1 functional zone classes."""

    RESIDENTIAL = "residential"
    MIXED = "mixed"
    PUBLIC = "public"
    RECREATION = "recreation"


class ZoneAdjacencyPolicy(StrEnum):
    """How a pair of different functional zone classes should relate spatially."""

    PREFERRED = "PREFERRED"
    ALLOWED = "ALLOWED"
    DISCOURAGED = "DISCOURAGED"
    FORBIDDEN = "FORBIDDEN"


_ZONE_ORDER = {zone_class: index for index, zone_class in enumerate(ZoneClass)}
_SHARE_SUM_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class ZoneClassConfig:
    """Target allocation and metric minimum-area rule for one zone class."""

    zone_class: ZoneClass
    target_share: float
    minimum_area_m2: float

    def __post_init__(self) -> None:
        if not isinstance(self.zone_class, ZoneClass):
            raise ZoningConfigError("zone_class must be a ZoneClass value")

        target_share = _require_finite_number("target_share", self.target_share)
        if target_share < 0.0 or target_share > 1.0:
            raise ZoningConfigError("target_share must be inside 0..1")
        object.__setattr__(self, "target_share", target_share)

        minimum_area_m2 = _require_finite_number(
            "minimum_area_m2",
            self.minimum_area_m2,
        )
        if minimum_area_m2 <= 0.0:
            raise ZoningConfigError("minimum_area_m2 must be greater than zero")
        object.__setattr__(self, "minimum_area_m2", minimum_area_m2)


@dataclass(frozen=True, slots=True)
class ZoneAdjacencyRule:
    """Symmetric adjacency policy for one unordered pair of different zone classes."""

    first: ZoneClass
    second: ZoneClass
    policy: ZoneAdjacencyPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.first, ZoneClass) or not isinstance(self.second, ZoneClass):
            raise ZoningConfigError("adjacency endpoints must be ZoneClass values")
        if self.first is self.second:
            raise ZoningConfigError("self adjacency is always allowed and must not be configured")
        if not isinstance(self.policy, ZoneAdjacencyPolicy):
            raise ZoningConfigError("adjacency policy must be a ZoneAdjacencyPolicy value")

        if _ZONE_ORDER[self.first] > _ZONE_ORDER[self.second]:
            first = self.second
            second = self.first
            object.__setattr__(self, "first", first)
            object.__setattr__(self, "second", second)

    @property
    def pair(self) -> tuple[ZoneClass, ZoneClass]:
        return self.first, self.second


@dataclass(frozen=True, slots=True)
class ZoningConfig:
    """Immutable, versioned functional zoning configuration."""

    version: str
    zones: tuple[ZoneClassConfig, ...]
    adjacency_rules: tuple[ZoneAdjacencyRule, ...] = ()

    def __post_init__(self) -> None:
        _validate_version(self.version)
        if not isinstance(self.zones, tuple):
            raise ZoningConfigError("zones must be an immutable tuple")
        if not self.zones:
            raise ZoningConfigError("zoning config must contain zone definitions")
        if any(not isinstance(item, ZoneClassConfig) for item in self.zones):
            raise ZoningConfigError("zones must contain only ZoneClassConfig values")
        if not isinstance(self.adjacency_rules, tuple):
            raise ZoningConfigError("adjacency_rules must be an immutable tuple")
        if any(not isinstance(item, ZoneAdjacencyRule) for item in self.adjacency_rules):
            raise ZoningConfigError(
                "adjacency_rules must contain only ZoneAdjacencyRule values"
            )

        configured_classes = tuple(zone.zone_class for zone in self.zones)
        if len(configured_classes) != len(set(configured_classes)):
            raise ZoningConfigError("zone classes must be unique")
        expected_classes = set(ZoneClass)
        actual_classes = set(configured_classes)
        if actual_classes != expected_classes:
            missing = sorted(item.value for item in expected_classes - actual_classes)
            extra = sorted(item.value for item in actual_classes - expected_classes)
            details: list[str] = []
            if missing:
                details.append("missing=" + ",".join(missing))
            if extra:
                details.append("extra=" + ",".join(extra))
            suffix = f" ({'; '.join(details)})" if details else ""
            raise ZoningConfigError(
                "zoning config must define every canonical zone class exactly once" + suffix
            )

        target_share_sum = math.fsum(zone.target_share for zone in self.zones)
        if not math.isclose(
            target_share_sum,
            1.0,
            rel_tol=0.0,
            abs_tol=_SHARE_SUM_TOLERANCE,
        ):
            raise ZoningConfigError(
                "zone target shares must sum to 1.0 "
                f"within {_SHARE_SUM_TOLERANCE:g}; got {target_share_sum:.17g}"
            )

        pairs = [rule.pair for rule in self.adjacency_rules]
        if len(pairs) != len(set(pairs)):
            raise ZoningConfigError("adjacency rule pairs must be unique")

        canonical_zones = tuple(
            sorted(self.zones, key=lambda item: _ZONE_ORDER[item.zone_class])
        )
        canonical_rules = tuple(
            sorted(
                self.adjacency_rules,
                key=lambda item: (_ZONE_ORDER[item.first], _ZONE_ORDER[item.second]),
            )
        )
        object.__setattr__(self, "zones", canonical_zones)
        object.__setattr__(self, "adjacency_rules", canonical_rules)

    def zone(self, zone_class: ZoneClass) -> ZoneClassConfig:
        _require_zone_class(zone_class)
        for zone in self.zones:
            if zone.zone_class is zone_class:
                return zone
        raise ZoningConfigError(f"unknown zone class: {zone_class!r}")

    def adjacency_policy(
        self,
        first: ZoneClass,
        second: ZoneClass,
    ) -> ZoneAdjacencyPolicy:
        """Return the symmetric pair policy; unspecified and self pairs are allowed."""

        _require_zone_class(first)
        _require_zone_class(second)
        if first is second:
            return ZoneAdjacencyPolicy.ALLOWED
        pair = _canonical_pair(first, second)
        for rule in self.adjacency_rules:
            if rule.pair == pair:
                return rule.policy
        return ZoneAdjacencyPolicy.ALLOWED

    def allows_adjacency(self, first: ZoneClass, second: ZoneClass) -> bool:
        """Return whether the configured relation permits the classes to touch."""

        return self.adjacency_policy(first, second) is not ZoneAdjacencyPolicy.FORBIDDEN

    @property
    def fingerprint(self) -> str:
        """Stable canonical content fingerprint for provenance and cache keys."""

        payload = {
            "version": self.version,
            "zones": [
                {
                    "zone_class": zone.zone_class.value,
                    "target_share": zone.target_share,
                    "minimum_area_m2": zone.minimum_area_m2,
                }
                for zone in self.zones
            ],
            "adjacency_rules": [
                {
                    "first": rule.first.value,
                    "second": rule.second.value,
                    "policy": rule.policy.value,
                }
                for rule in self.adjacency_rules
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


def _canonical_pair(
    first: ZoneClass,
    second: ZoneClass,
) -> tuple[ZoneClass, ZoneClass]:
    if _ZONE_ORDER[first] <= _ZONE_ORDER[second]:
        return first, second
    return second, first


def _require_zone_class(value: ZoneClass) -> None:
    if not isinstance(value, ZoneClass):
        raise ZoningConfigError("zone class lookup requires a ZoneClass value")


def _validate_version(version: str) -> None:
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise ZoningConfigError(f"invalid zoning config version: {version!r}")


def _require_finite_number(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ZoningConfigError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ZoningConfigError(f"{field_name} must be a finite number")
    return number


_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
