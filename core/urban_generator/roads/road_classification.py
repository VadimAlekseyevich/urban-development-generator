from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from core.urban_generator.roads.rule_based_growth import RoadGrowthIntent

DEFAULT_COLLECTOR_MIN_LENGTH_M = 500.0
DEFAULT_ARTERIAL_MIN_LENGTH_M = 2_000.0
DEFAULT_MAX_CLASSIFICATION_SUBJECTS = 1_000_000


class RoadClassificationError(ValueError):
    """Raised when road-classification inputs or policy violate the core contract."""


class GeneratedRoadClass(StrEnum):
    """Canonical classes assigned only to generated roads in S06-T12."""

    LOCAL = "local"
    COLLECTOR = "collector"
    ARTERIAL = "arterial"


class RoadClassificationOrigin(StrEnum):
    """Stable source kind used by the classification rules."""

    EXISTING = "EXISTING"
    BASELINE_GENERATED = "BASELINE_GENERATED"
    GROWTH_GENERATED = "GROWTH_GENERATED"


class RoadClassificationReason(StrEnum):
    """Highest-priority rule that determined a classified road's final class."""

    EXISTING_PRESERVED = "EXISTING_PRESERVED"
    LOCAL_DEFAULT = "LOCAL_DEFAULT"
    COLLECTOR_LENGTH = "COLLECTOR_LENGTH"
    COLLECTOR_INTENT_FLOOR = "COLLECTOR_INTENT_FLOOR"
    BASELINE_FLOOR = "BASELINE_FLOOR"
    ARTERIAL_LENGTH = "ARTERIAL_LENGTH"


_CLASS_RANK = {
    GeneratedRoadClass.LOCAL: 0,
    GeneratedRoadClass.COLLECTOR: 1,
    GeneratedRoadClass.ARTERIAL: 2,
}


@dataclass(frozen=True, slots=True)
class RoadClassificationPolicy:
    """Configurable v1 rules for generated arterial/collector/local classification."""

    collector_min_length_m: float = DEFAULT_COLLECTOR_MIN_LENGTH_M
    arterial_min_length_m: float = DEFAULT_ARTERIAL_MIN_LENGTH_M
    baseline_min_class: GeneratedRoadClass = GeneratedRoadClass.COLLECTOR
    collector_intent_min_class: GeneratedRoadClass = GeneratedRoadClass.COLLECTOR
    max_subjects: int = DEFAULT_MAX_CLASSIFICATION_SUBJECTS

    def __post_init__(self) -> None:
        collector = _require_positive_finite(
            "collector_min_length_m", self.collector_min_length_m
        )
        arterial = _require_positive_finite(
            "arterial_min_length_m", self.arterial_min_length_m
        )
        if arterial <= collector:
            raise RoadClassificationError(
                "arterial_min_length_m must be greater than collector_min_length_m"
            )
        object.__setattr__(self, "collector_min_length_m", collector)
        object.__setattr__(self, "arterial_min_length_m", arterial)
        if not isinstance(self.baseline_min_class, GeneratedRoadClass):
            raise RoadClassificationError(
                "baseline_min_class must be a GeneratedRoadClass value"
            )
        if not isinstance(self.collector_intent_min_class, GeneratedRoadClass):
            raise RoadClassificationError(
                "collector_intent_min_class must be a GeneratedRoadClass value"
            )
        _require_positive_int("max_subjects", self.max_subjects)


@dataclass(frozen=True, slots=True)
class RoadClassificationSubject:
    """One existing or generated road presented to the classification strategy."""

    road_id: str
    origin: RoadClassificationOrigin
    length_m: float | None = None
    existing_class: str | None = None
    growth_intent: RoadGrowthIntent | None = None

    def __post_init__(self) -> None:
        _require_non_empty_string("road_id", self.road_id)
        if not isinstance(self.origin, RoadClassificationOrigin):
            raise RoadClassificationError(
                "origin must be a RoadClassificationOrigin value"
            )

        if self.origin is RoadClassificationOrigin.EXISTING:
            if self.existing_class is None:
                raise RoadClassificationError(
                    "existing road requires existing_class to preserve"
                )
            _require_non_empty_string("existing_class", self.existing_class)
            if self.growth_intent is not None:
                raise RoadClassificationError(
                    "existing road must not carry generated growth_intent"
                )
            if self.length_m is not None:
                object.__setattr__(
                    self,
                    "length_m",
                    _require_positive_finite("length_m", self.length_m),
                )
            return

        if self.existing_class is not None:
            raise RoadClassificationError(
                "generated road must not carry existing_class"
            )
        if self.length_m is None:
            raise RoadClassificationError("generated road requires length_m")
        object.__setattr__(
            self,
            "length_m",
            _require_positive_finite("length_m", self.length_m),
        )
        if self.origin is RoadClassificationOrigin.BASELINE_GENERATED:
            if self.growth_intent is not None:
                raise RoadClassificationError(
                    "baseline-generated road must not carry growth_intent"
                )
            return
        if not isinstance(self.growth_intent, RoadGrowthIntent):
            raise RoadClassificationError(
                "growth-generated road requires RoadGrowthIntent"
            )


@dataclass(frozen=True, slots=True)
class ClassifiedRoad:
    """One stable classification result with existing-class preservation provenance."""

    road_id: str
    origin: RoadClassificationOrigin
    road_class: str
    generated_class: GeneratedRoadClass | None
    reason: RoadClassificationReason

    def __post_init__(self) -> None:
        _require_non_empty_string("road_id", self.road_id)
        if not isinstance(self.origin, RoadClassificationOrigin):
            raise RoadClassificationError(
                "origin must be a RoadClassificationOrigin value"
            )
        _require_non_empty_string("road_class", self.road_class)
        if not isinstance(self.reason, RoadClassificationReason):
            raise RoadClassificationError(
                "reason must be a RoadClassificationReason value"
            )
        if self.origin is RoadClassificationOrigin.EXISTING:
            if self.generated_class is not None:
                raise RoadClassificationError(
                    "existing road must not expose generated_class"
                )
            if self.reason is not RoadClassificationReason.EXISTING_PRESERVED:
                raise RoadClassificationError(
                    "existing road classification reason must be EXISTING_PRESERVED"
                )
            return
        if not isinstance(self.generated_class, GeneratedRoadClass):
            raise RoadClassificationError(
                "generated road requires canonical generated_class"
            )
        if self.road_class != self.generated_class.value:
            raise RoadClassificationError(
                "generated road_class must equal canonical generated_class value"
            )
        if self.reason is RoadClassificationReason.EXISTING_PRESERVED:
            raise RoadClassificationError(
                "generated road cannot use EXISTING_PRESERVED reason"
            )


@dataclass(frozen=True, slots=True)
class RoadClassificationDiagnostics:
    """Stable counts for one deterministic classification pass."""

    subject_count: int
    existing_preserved_count: int
    generated_count: int
    local_count: int
    collector_count: int
    arterial_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "subject_count",
            "existing_preserved_count",
            "generated_count",
            "local_count",
            "collector_count",
            "arterial_count",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        if self.existing_preserved_count + self.generated_count != self.subject_count:
            raise RoadClassificationError(
                "existing and generated counts must sum to subject_count"
            )
        if self.local_count + self.collector_count + self.arterial_count != self.generated_count:
            raise RoadClassificationError(
                "generated class counts must sum to generated_count"
            )


@dataclass(frozen=True, slots=True)
class RoadClassificationResult:
    """Immutable deterministic road-classification output."""

    roads: tuple[ClassifiedRoad, ...]
    diagnostics: RoadClassificationDiagnostics
    strategy_name: str
    strategy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.roads, tuple):
            raise RoadClassificationError("roads must be an immutable tuple")
        if any(not isinstance(road, ClassifiedRoad) for road in self.roads):
            raise RoadClassificationError(
                "roads must contain only ClassifiedRoad values"
            )
        road_ids = tuple(road.road_id for road in self.roads)
        if tuple(sorted(road_ids)) != road_ids:
            raise RoadClassificationError("classified roads must be sorted by road_id")
        if len(set(road_ids)) != len(road_ids):
            raise RoadClassificationError("classified road ids must be unique")
        if not isinstance(self.diagnostics, RoadClassificationDiagnostics):
            raise RoadClassificationError(
                "diagnostics must be RoadClassificationDiagnostics"
            )
        if self.diagnostics.subject_count != len(self.roads):
            raise RoadClassificationError(
                "diagnostics subject_count must match roads"
            )
        _require_non_empty_string("strategy_name", self.strategy_name)
        _require_non_empty_string("strategy_version", self.strategy_version)


@runtime_checkable
class RoadClassificationStrategy(Protocol):
    """Pluggable strategy port for generated road classification."""

    name: str
    version: str

    def classify(
        self,
        subjects: tuple[RoadClassificationSubject, ...],
    ) -> RoadClassificationResult:
        """Preserve existing classes and classify generated subjects."""

        ...


class RuleBasedRoadClassifier:
    """Classify generated roads with length/floor rules while preserving existing classes."""

    name = "rule-based-road-classification"
    version = "1"

    def __init__(self, *, policy: RoadClassificationPolicy | None = None) -> None:
        if policy is None:
            policy = RoadClassificationPolicy()
        if not isinstance(policy, RoadClassificationPolicy):
            raise RoadClassificationError(
                "policy must be a RoadClassificationPolicy"
            )
        self._policy = policy

    @property
    def policy(self) -> RoadClassificationPolicy:
        return self._policy

    def classify(
        self,
        subjects: tuple[RoadClassificationSubject, ...],
    ) -> RoadClassificationResult:
        ordered = _validate_subjects(subjects, max_subjects=self._policy.max_subjects)
        roads = tuple(self._classify_one(subject) for subject in ordered)
        return RoadClassificationResult(
            roads=roads,
            diagnostics=_build_diagnostics(roads),
            strategy_name=self.name,
            strategy_version=self.version,
        )

    def _classify_one(self, subject: RoadClassificationSubject) -> ClassifiedRoad:
        if subject.origin is RoadClassificationOrigin.EXISTING:
            if subject.existing_class is None:
                raise RoadClassificationError(
                    "existing road requires existing_class to preserve"
                )
            return ClassifiedRoad(
                road_id=subject.road_id,
                origin=subject.origin,
                road_class=subject.existing_class,
                generated_class=None,
                reason=RoadClassificationReason.EXISTING_PRESERVED,
            )

        if subject.length_m is None:
            raise RoadClassificationError("generated road requires length_m")
        road_class, reason = self._generated_class(subject)
        return ClassifiedRoad(
            road_id=subject.road_id,
            origin=subject.origin,
            road_class=road_class.value,
            generated_class=road_class,
            reason=reason,
        )

    def _generated_class(
        self,
        subject: RoadClassificationSubject,
    ) -> tuple[GeneratedRoadClass, RoadClassificationReason]:
        if subject.length_m is None:
            raise RoadClassificationError("generated road requires length_m")
        if subject.length_m >= self._policy.arterial_min_length_m:
            return GeneratedRoadClass.ARTERIAL, RoadClassificationReason.ARTERIAL_LENGTH

        road_class = GeneratedRoadClass.LOCAL
        reason = RoadClassificationReason.LOCAL_DEFAULT
        if subject.length_m >= self._policy.collector_min_length_m:
            road_class = GeneratedRoadClass.COLLECTOR
            reason = RoadClassificationReason.COLLECTOR_LENGTH

        if subject.origin is RoadClassificationOrigin.BASELINE_GENERATED:
            if _CLASS_RANK[self._policy.baseline_min_class] > _CLASS_RANK[road_class]:
                road_class = self._policy.baseline_min_class
                reason = RoadClassificationReason.BASELINE_FLOOR
        elif subject.growth_intent is RoadGrowthIntent.COLLECTOR:
            if _CLASS_RANK[self._policy.collector_intent_min_class] > _CLASS_RANK[road_class]:
                road_class = self._policy.collector_intent_min_class
                reason = RoadClassificationReason.COLLECTOR_INTENT_FLOOR

        return road_class, reason


def _validate_subjects(
    subjects: tuple[RoadClassificationSubject, ...],
    *,
    max_subjects: int,
) -> tuple[RoadClassificationSubject, ...]:
    if not isinstance(subjects, tuple):
        raise RoadClassificationError("subjects must be an immutable tuple")
    if len(subjects) > max_subjects:
        raise RoadClassificationError(
            f"road classification subject limit exceeded: {len(subjects)} > {max_subjects}"
        )
    if any(not isinstance(subject, RoadClassificationSubject) for subject in subjects):
        raise RoadClassificationError(
            "subjects must contain only RoadClassificationSubject values"
        )
    road_ids = tuple(subject.road_id for subject in subjects)
    if len(set(road_ids)) != len(road_ids):
        raise RoadClassificationError("road_id values must be unique")
    return tuple(sorted(subjects, key=lambda subject: subject.road_id))


def _build_diagnostics(
    roads: tuple[ClassifiedRoad, ...],
) -> RoadClassificationDiagnostics:
    existing_count = sum(
        road.origin is RoadClassificationOrigin.EXISTING for road in roads
    )
    generated = tuple(road for road in roads if road.generated_class is not None)
    return RoadClassificationDiagnostics(
        subject_count=len(roads),
        existing_preserved_count=existing_count,
        generated_count=len(generated),
        local_count=sum(
            road.generated_class is GeneratedRoadClass.LOCAL for road in generated
        ),
        collector_count=sum(
            road.generated_class is GeneratedRoadClass.COLLECTOR for road in generated
        ),
        arterial_count=sum(
            road.generated_class is GeneratedRoadClass.ARTERIAL for road in generated
        ),
    )


def _require_non_empty_string(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RoadClassificationError(f"{field_name} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise RoadClassificationError(f"{field_name} must not contain line breaks")


def _require_positive_finite(field_name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RoadClassificationError(
            f"{field_name} must be a positive finite number"
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise RoadClassificationError(
            f"{field_name} must be a positive finite number"
        )
    return number


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RoadClassificationError(f"{field_name} must be a positive integer")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RoadClassificationError(f"{field_name} must be a non-negative integer")
