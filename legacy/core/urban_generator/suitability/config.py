from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum


class SuitabilityConfigError(ValueError):
    """Raised when suitability configuration violates the core contract."""


class SuitabilityNormalization(StrEnum):
    """How one factor's raw finite values are mapped to the canonical 0..1 score range."""

    IDENTITY = "IDENTITY"
    MIN_MAX = "MIN_MAX"
    INVERTED_MIN_MAX = "INVERTED_MIN_MAX"


@dataclass(frozen=True, slots=True)
class SuitabilityFactorConfig:
    """Versioned configuration for one suitability factor."""

    code: str
    weight: float
    normalization: SuitabilityNormalization
    raw_min: float | None = None
    raw_max: float | None = None

    def __post_init__(self) -> None:
        _validate_factor_code(self.code)
        weight = _require_finite_number("weight", self.weight)
        if weight < 0.0:
            raise SuitabilityConfigError("factor weight must be non-negative")
        object.__setattr__(self, "weight", weight)

        if not isinstance(self.normalization, SuitabilityNormalization):
            raise SuitabilityConfigError(
                "factor normalization must be a SuitabilityNormalization value"
            )

        if self.normalization is SuitabilityNormalization.IDENTITY:
            if self.raw_min is not None or self.raw_max is not None:
                raise SuitabilityConfigError(
                    "IDENTITY normalization must not define raw_min/raw_max"
                )
            return

        if self.raw_min is None or self.raw_max is None:
            raise SuitabilityConfigError(
                f"{self.normalization.value} normalization requires raw_min and raw_max"
            )
        raw_min = _require_finite_number("raw_min", self.raw_min)
        raw_max = _require_finite_number("raw_max", self.raw_max)
        if raw_max <= raw_min:
            raise SuitabilityConfigError("raw_max must be greater than raw_min")
        object.__setattr__(self, "raw_min", raw_min)
        object.__setattr__(self, "raw_max", raw_max)

    def normalize(self, raw_value: float) -> float:
        """Normalize one finite raw value to 0..1 with deterministic clipping."""

        value = _require_finite_number("raw_value", raw_value)
        if self.normalization is SuitabilityNormalization.IDENTITY:
            if value < 0.0 or value > 1.0:
                raise SuitabilityConfigError(
                    "IDENTITY normalization requires raw values inside 0..1"
                )
            return value

        assert self.raw_min is not None
        assert self.raw_max is not None
        scaled = (value - self.raw_min) / (self.raw_max - self.raw_min)
        clipped = min(1.0, max(0.0, scaled))
        if self.normalization is SuitabilityNormalization.INVERTED_MIN_MAX:
            return 1.0 - clipped
        return clipped


@dataclass(frozen=True, slots=True)
class SuitabilityThresholds:
    """Score thresholds used to interpret the final normalized suitability score."""

    minimum_score: float
    preferred_score: float | None = None

    def __post_init__(self) -> None:
        minimum = _require_unit_interval("minimum_score", self.minimum_score)
        object.__setattr__(self, "minimum_score", minimum)

        if self.preferred_score is None:
            return
        preferred = _require_unit_interval("preferred_score", self.preferred_score)
        if preferred < minimum:
            raise SuitabilityConfigError(
                "preferred_score must be greater than or equal to minimum_score"
            )
        object.__setattr__(self, "preferred_score", preferred)


@dataclass(frozen=True, slots=True)
class SuitabilityConfig:
    """Immutable, explicitly versioned suitability configuration."""

    version: str
    factors: tuple[SuitabilityFactorConfig, ...]
    thresholds: SuitabilityThresholds

    def __post_init__(self) -> None:
        _validate_version(self.version)
        if not isinstance(self.factors, tuple):
            raise SuitabilityConfigError("suitability factors must be an immutable tuple")
        if not self.factors:
            raise SuitabilityConfigError("suitability config must contain at least one factor")
        if any(not isinstance(item, SuitabilityFactorConfig) for item in self.factors):
            raise SuitabilityConfigError(
                "suitability factors must contain only SuitabilityFactorConfig values"
            )
        if not isinstance(self.thresholds, SuitabilityThresholds):
            raise SuitabilityConfigError("thresholds must be SuitabilityThresholds")

        codes = [factor.code for factor in self.factors]
        if len(codes) != len(set(codes)):
            raise SuitabilityConfigError("suitability factor codes must be unique")
        if self.total_weight <= 0.0:
            raise SuitabilityConfigError(
                "suitability config must contain at least one factor with positive weight"
            )

    @property
    def total_weight(self) -> float:
        return sum(factor.weight for factor in self.factors)

    @property
    def normalized_weights(self) -> tuple[tuple[str, float], ...]:
        """Return deterministic configured-order weights summing to 1.0."""

        total = self.total_weight
        return tuple((factor.code, factor.weight / total) for factor in self.factors)

    def factor(self, code: str) -> SuitabilityFactorConfig:
        _validate_factor_code(code)
        for factor in self.factors:
            if factor.code == code:
                return factor
        raise SuitabilityConfigError(f"unknown suitability factor: {code!r}")

    @property
    def fingerprint(self) -> str:
        """Stable content fingerprint for provenance/cache keys, independent of object identity."""

        payload = {
            "version": self.version,
            "factors": [
                {
                    "code": factor.code,
                    "weight": factor.weight,
                    "normalization": factor.normalization.value,
                    "raw_min": factor.raw_min,
                    "raw_max": factor.raw_max,
                }
                for factor in self.factors
            ],
            "thresholds": {
                "minimum_score": self.thresholds.minimum_score,
                "preferred_score": self.thresholds.preferred_score,
            },
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _validate_factor_code(code: str) -> None:
    if not isinstance(code, str) or _FACTOR_CODE_RE.fullmatch(code) is None:
        raise SuitabilityConfigError(f"invalid suitability factor code: {code!r}")


def _validate_version(version: str) -> None:
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise SuitabilityConfigError(f"invalid suitability config version: {version!r}")


def _require_finite_number(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SuitabilityConfigError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise SuitabilityConfigError(f"{field_name} must be a finite number")
    return number


def _require_unit_interval(field_name: str, value: float) -> float:
    number = _require_finite_number(field_name, value)
    if number < 0.0 or number > 1.0:
        raise SuitabilityConfigError(f"{field_name} must be inside 0..1")
    return number


_FACTOR_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
