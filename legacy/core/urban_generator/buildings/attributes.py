from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum

from core.urban_generator.buildings.config import BuildingArchetype
from core.urban_generator.domain import RunContext
from core.urban_generator.zoning import ZoneClass

DEFAULT_MAX_ATTRIBUTE_SUBJECTS = 100_000
BUILDING_ATTRIBUTE_ASSIGNMENT_VERSION = "building-attributes-v1"

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ZONE_ORDER = {value: index for index, value in enumerate(ZoneClass)}
_ARCHETYPE_ORDER = {
    value: index for index, value in enumerate(BuildingArchetype)
}


class BuildingAttributeAssignmentError(ValueError):
    """Raised when S08-T10 building attribute inputs violate the contract."""


class BuildingUse(StrEnum):
    """Canonical v1 generated-building use classes."""

    RESIDENTIAL = "residential"
    MIXED = "mixed"
    PUBLIC = "public"
    COMMERCIAL = "commercial"


@dataclass(frozen=True, slots=True)
class BuildingAttributeRule:
    """One exact zone/archetype rule for use and floor assignment."""

    zone_class: ZoneClass
    archetype: BuildingArchetype
    use: BuildingUse
    min_floors: int
    max_floors: int

    def __post_init__(self) -> None:
        if not isinstance(self.zone_class, ZoneClass):
            raise BuildingAttributeAssignmentError(
                "zone_class must be a ZoneClass value"
            )
        if not isinstance(self.archetype, BuildingArchetype):
            raise BuildingAttributeAssignmentError(
                "archetype must be a BuildingArchetype value"
            )
        if not isinstance(self.use, BuildingUse):
            raise BuildingAttributeAssignmentError(
                "use must be a BuildingUse value"
            )
        _require_positive_int("min_floors", self.min_floors)
        _require_positive_int("max_floors", self.max_floors)
        if self.max_floors < self.min_floors:
            raise BuildingAttributeAssignmentError(
                "max_floors must be >= min_floors"
            )

    @property
    def key(self) -> tuple[ZoneClass, BuildingArchetype]:
        return self.zone_class, self.archetype


@dataclass(frozen=True, slots=True)
class BuildingAttributeConfig:
    """Versioned exact-match rules for S08-T10 use/floor assignment."""

    version: str
    rules: tuple[BuildingAttributeRule, ...]

    def __post_init__(self) -> None:
        _require_version(self.version)
        if not isinstance(self.rules, tuple):
            raise BuildingAttributeAssignmentError(
                "rules must be an immutable tuple"
            )
        if not self.rules:
            raise BuildingAttributeAssignmentError(
                "attribute config must contain at least one rule"
            )
        if any(not isinstance(item, BuildingAttributeRule) for item in self.rules):
            raise BuildingAttributeAssignmentError(
                "rules must contain BuildingAttributeRule values"
            )

        keys = tuple(item.key for item in self.rules)
        if len(keys) != len(set(keys)):
            raise BuildingAttributeAssignmentError(
                "zone/archetype attribute rule pairs must be unique"
            )

        canonical = tuple(
            sorted(
                self.rules,
                key=lambda item: (
                    _ZONE_ORDER[item.zone_class],
                    _ARCHETYPE_ORDER[item.archetype],
                ),
            )
        )
        object.__setattr__(self, "rules", canonical)

    def rule(
        self,
        zone_class: ZoneClass,
        archetype: BuildingArchetype,
    ) -> BuildingAttributeRule:
        if not isinstance(zone_class, ZoneClass):
            raise BuildingAttributeAssignmentError(
                "zone_class lookup requires a ZoneClass value"
            )
        if not isinstance(archetype, BuildingArchetype):
            raise BuildingAttributeAssignmentError(
                "archetype lookup requires a BuildingArchetype value"
            )
        key = (zone_class, archetype)
        for rule in self.rules:
            if rule.key == key:
                return rule
        raise BuildingAttributeAssignmentError(
            "no building attribute rule for "
            f"{zone_class.value}/{archetype.value}"
        )

    @property
    def fingerprint(self) -> str:
        payload = {
            "assignment_version": BUILDING_ATTRIBUTE_ASSIGNMENT_VERSION,
            "version": self.version,
            "rules": [
                {
                    "zone_class": rule.zone_class.value,
                    "archetype": rule.archetype.value,
                    "use": rule.use.value,
                    "min_floors": rule.min_floors,
                    "max_floors": rule.max_floors,
                }
                for rule in self.rules
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


@dataclass(frozen=True, slots=True)
class BuildingAttributeSubject:
    """Geometry-free accepted building identity passed from placement to T10."""

    building_id: str
    source_id: str
    zone_class: ZoneClass
    archetype: BuildingArchetype

    def __post_init__(self) -> None:
        _require_id("building_id", self.building_id)
        _require_id("source_id", self.source_id)
        if not isinstance(self.zone_class, ZoneClass):
            raise BuildingAttributeAssignmentError(
                "zone_class must be a ZoneClass value"
            )
        if not isinstance(self.archetype, BuildingArchetype):
            raise BuildingAttributeAssignmentError(
                "archetype must be a BuildingArchetype value"
            )


@dataclass(frozen=True, slots=True)
class AssignedBuildingAttributes:
    """Immutable T10 attributes with no geometry or derived GFA."""

    building_id: str
    source_id: str
    zone_class: ZoneClass
    archetype: BuildingArchetype
    use: BuildingUse
    floors: int
    config_version: str
    assignment_version: str = BUILDING_ATTRIBUTE_ASSIGNMENT_VERSION

    def __post_init__(self) -> None:
        _require_id("building_id", self.building_id)
        _require_id("source_id", self.source_id)
        if not isinstance(self.zone_class, ZoneClass):
            raise BuildingAttributeAssignmentError(
                "zone_class must be a ZoneClass value"
            )
        if not isinstance(self.archetype, BuildingArchetype):
            raise BuildingAttributeAssignmentError(
                "archetype must be a BuildingArchetype value"
            )
        if not isinstance(self.use, BuildingUse):
            raise BuildingAttributeAssignmentError(
                "use must be a BuildingUse value"
            )
        _require_positive_int("floors", self.floors)
        _require_version(self.config_version)
        if self.assignment_version != BUILDING_ATTRIBUTE_ASSIGNMENT_VERSION:
            raise BuildingAttributeAssignmentError(
                "unsupported building attribute assignment version"
            )


@dataclass(frozen=True, slots=True)
class BuildingAttributeAssignmentResult:
    """Canonical T10 assignments plus config provenance."""

    config_version: str
    config_fingerprint: str
    buildings: tuple[AssignedBuildingAttributes, ...]

    def __post_init__(self) -> None:
        _require_version(self.config_version)
        if (
            not isinstance(self.config_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.config_fingerprint) is None
        ):
            raise BuildingAttributeAssignmentError(
                "config_fingerprint must be a lowercase SHA-256 hex digest"
            )
        if not isinstance(self.buildings, tuple):
            raise BuildingAttributeAssignmentError(
                "buildings must be an immutable tuple"
            )
        if any(
            not isinstance(item, AssignedBuildingAttributes)
            for item in self.buildings
        ):
            raise BuildingAttributeAssignmentError(
                "buildings must contain AssignedBuildingAttributes values"
            )
        ids = tuple(item.building_id for item in self.buildings)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise BuildingAttributeAssignmentError(
                "assigned building ids must be sorted and unique"
            )
        if any(
            item.config_version != self.config_version
            for item in self.buildings
        ):
            raise BuildingAttributeAssignmentError(
                "assigned config versions must match result config_version"
            )


class BuildingAttributeAssigner:
    """Assign use/floors deterministically without mutating building geometry."""

    def __init__(
        self,
        *,
        max_subjects: int = DEFAULT_MAX_ATTRIBUTE_SUBJECTS,
    ) -> None:
        _require_positive_int("max_subjects", max_subjects)
        self.max_subjects = max_subjects

    def assign(
        self,
        subjects: tuple[BuildingAttributeSubject, ...],
        *,
        config: BuildingAttributeConfig,
        context: RunContext,
    ) -> BuildingAttributeAssignmentResult:
        self._validate_inputs(
            subjects=subjects,
            config=config,
            context=context,
        )
        fingerprint = config.fingerprint
        assignments: list[AssignedBuildingAttributes] = []

        for subject in sorted(subjects, key=lambda item: item.building_id):
            rule = config.rule(subject.zone_class, subject.archetype)
            floors = self._floors(
                subject=subject,
                rule=rule,
                context=context,
                config_fingerprint=fingerprint,
            )
            assignments.append(
                AssignedBuildingAttributes(
                    building_id=subject.building_id,
                    source_id=subject.source_id,
                    zone_class=subject.zone_class,
                    archetype=subject.archetype,
                    use=rule.use,
                    floors=floors,
                    config_version=config.version,
                )
            )

        return BuildingAttributeAssignmentResult(
            config_version=config.version,
            config_fingerprint=fingerprint,
            buildings=tuple(assignments),
        )

    def _validate_inputs(
        self,
        *,
        subjects: tuple[BuildingAttributeSubject, ...],
        config: BuildingAttributeConfig,
        context: RunContext,
    ) -> None:
        if not isinstance(subjects, tuple):
            raise BuildingAttributeAssignmentError(
                "subjects must be an immutable tuple"
            )
        if len(subjects) > self.max_subjects:
            raise BuildingAttributeAssignmentError(
                "attribute subject limit exceeded: "
                f"{len(subjects)} > {self.max_subjects}"
            )
        if any(
            not isinstance(item, BuildingAttributeSubject)
            for item in subjects
        ):
            raise BuildingAttributeAssignmentError(
                "subjects must contain BuildingAttributeSubject values"
            )
        ids = tuple(item.building_id for item in subjects)
        if len(ids) != len(set(ids)):
            raise BuildingAttributeAssignmentError(
                "building ids must be unique"
            )
        if not isinstance(config, BuildingAttributeConfig):
            raise BuildingAttributeAssignmentError(
                "config must be a BuildingAttributeConfig"
            )
        if not isinstance(context, RunContext):
            raise BuildingAttributeAssignmentError(
                "context must be a RunContext"
            )

    def _floors(
        self,
        *,
        subject: BuildingAttributeSubject,
        rule: BuildingAttributeRule,
        context: RunContext,
        config_fingerprint: str,
    ) -> int:
        if rule.min_floors == rule.max_floors:
            return rule.min_floors

        token = "|".join(
            (
                config_fingerprint,
                subject.building_id,
                subject.zone_class.value,
                subject.archetype.value,
            )
        ).encode("utf-8")
        digest = hashlib.blake2b(token, digest_size=16).hexdigest()
        rng = context.rng(f"buildings.attributes:{digest}")
        return int(rng.integers(rule.min_floors, rule.max_floors + 1))


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise BuildingAttributeAssignmentError(
            f"invalid {field_name}: {value!r}"
        )


def _require_version(value: str) -> None:
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        raise BuildingAttributeAssignmentError(
            f"invalid config version: {value!r}"
        )


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BuildingAttributeAssignmentError(
            f"{field_name} must be a positive integer"
        )
