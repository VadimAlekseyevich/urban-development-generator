from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from core.urban_generator.domain.constraints import (
    ConstraintContractError,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    validate_constraint_metadata,
)
from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.domain.run_context import RunContext
from core.urban_generator.domain.territory import TerritorySnapshot


class DistanceSetbackError(ConstraintContractError):
    """Raised when distance/setback inputs violate the spatial contract."""


class DistanceSetbackCandidateLimitError(DistanceSetbackError):
    """Raised instead of evaluating an unbounded number of nearby features."""


class DistanceSetbackKind(StrEnum):
    ROAD = "ROAD"
    BUILDING = "BUILDING"
    FEATURE = "FEATURE"


@dataclass(frozen=True, slots=True)
class DistanceSetbackBand:
    """One immutable feature group with a minimum metric clearance."""

    kind: DistanceSetbackKind
    minimum_distance_m: float
    geometries: tuple[BaseGeometry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DistanceSetbackKind):
            raise DistanceSetbackError("setback kind must be a DistanceSetbackKind value")
        distance = _require_distance("minimum_distance_m", self.minimum_distance_m)
        object.__setattr__(self, "minimum_distance_m", distance)
        _require_geometry_tuple("geometries", self.geometries)


@dataclass(frozen=True, slots=True)
class DistanceSetbackSubject:
    """One candidate geometry expressed in the project working CRS."""

    geometry: BaseGeometry
    working_srid: int

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        _require_geometry("subject", self.geometry)


@dataclass(frozen=True, slots=True)
class DistanceSetbackHit:
    """Deterministic explanation of the first configured setback violation."""

    kind: DistanceSetbackKind
    band_index: int
    feature_index: int
    minimum_distance_m: float
    actual_distance_m: float


class DistanceSetbackIndex:
    """Reusable STRtree-backed state for repeated metric setback checks."""

    def __init__(
        self,
        *,
        bands: tuple[DistanceSetbackBand, ...],
        working_srid: int,
        max_candidates: int = 10_000,
    ) -> None:
        require_working_crs(working_srid)
        if not isinstance(bands, tuple):
            raise DistanceSetbackError("setback bands must be an immutable tuple")
        if any(not isinstance(band, DistanceSetbackBand) for band in bands):
            raise DistanceSetbackError(
                "setback bands must contain only DistanceSetbackBand values"
            )
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int):
            raise DistanceSetbackError("max_candidates must be an integer")
        if max_candidates <= 0:
            raise DistanceSetbackError("max_candidates must be positive")

        self.working_srid = working_srid
        self.max_candidates = max_candidates
        self.bands = bands
        self._trees = tuple(
            STRtree(band.geometries)
            if band.minimum_distance_m > 0.0 and band.geometries
            else None
            for band in bands
        )

    @property
    def feature_count(self) -> int:
        return sum(len(band.geometries) for band in self.bands)

    @property
    def active_band_count(self) -> int:
        return sum(tree is not None for tree in self._trees)

    def check(self, subject: DistanceSetbackSubject) -> DistanceSetbackHit | None:
        """Return the first deterministic setback violation, or ``None`` when allowed."""

        if not isinstance(subject, DistanceSetbackSubject):
            raise DistanceSetbackError("subject must be a DistanceSetbackSubject")
        if subject.working_srid != self.working_srid:
            raise DistanceSetbackError(
                "subject working_srid must match distance setback index working_srid"
            )

        examined_candidates = 0
        min_x, min_y, max_x, max_y = subject.geometry.bounds
        for band_index, (band, tree) in enumerate(zip(self.bands, self._trees, strict=True)):
            if tree is None:
                continue
            distance = band.minimum_distance_m
            search_envelope = box(
                min_x - distance,
                min_y - distance,
                max_x + distance,
                max_y + distance,
            )
            candidate_indexes = tree.query(search_envelope)
            examined_candidates += len(candidate_indexes)
            if examined_candidates > self.max_candidates:
                raise DistanceSetbackCandidateLimitError(
                    "distance setback candidate limit exceeded: "
                    f"{examined_candidates} > {self.max_candidates}"
                )

            for raw_index in sorted(int(index) for index in candidate_indexes):
                actual_distance_m = float(subject.geometry.distance(band.geometries[raw_index]))
                if actual_distance_m < band.minimum_distance_m:
                    return DistanceSetbackHit(
                        kind=band.kind,
                        band_index=band_index,
                        feature_index=raw_index,
                        minimum_distance_m=band.minimum_distance_m,
                        actual_distance_m=actual_distance_m,
                    )
        return None


class DistanceSetbackConstraint:
    """HARD rule requiring a candidate to keep configured metric clearances."""

    severity = ConstraintSeverity.HARD

    def __init__(
        self,
        *,
        scope: ConstraintScope,
        index: DistanceSetbackIndex,
        code: str = "distance.setback",
    ) -> None:
        if not isinstance(index, DistanceSetbackIndex):
            raise DistanceSetbackError("index must be a DistanceSetbackIndex")
        validate_constraint_metadata(code, self.severity, scope)
        self.code = code
        self.scope = scope
        self.index = index

    def evaluate(
        self,
        *,
        subject: DistanceSetbackSubject,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ConstraintResult:
        if snapshot.settings.working_srid != self.index.working_srid:
            raise DistanceSetbackError(
                "snapshot working_srid must match distance setback index working_srid"
            )
        if context.working_srid != self.index.working_srid:
            raise DistanceSetbackError(
                "run context working_srid must match distance setback index working_srid"
            )

        hit = self.index.check(subject)
        if hit is None:
            return ConstraintResult(
                code=self.code,
                severity=self.severity,
                scope=self.scope,
                passed=True,
                message="geometry satisfies all configured distance setbacks",
            )
        return ConstraintResult(
            code=self.code,
            severity=self.severity,
            scope=self.scope,
            passed=False,
            message=_failure_message(hit),
        )


def _require_distance(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DistanceSetbackError(f"{field_name} must be a finite non-negative number")
    distance = float(value)
    if not math.isfinite(distance) or distance < 0.0:
        raise DistanceSetbackError(f"{field_name} must be a finite non-negative number")
    return distance


def _require_geometry(field_name: str, geometry: BaseGeometry) -> None:
    if not isinstance(geometry, BaseGeometry):
        raise DistanceSetbackError(f"{field_name} must be a Shapely geometry")
    if geometry.is_empty:
        raise DistanceSetbackError(f"{field_name} geometry must not be empty")
    if not geometry.is_valid:
        raise DistanceSetbackError(f"{field_name} geometry must be valid")


def _require_geometry_tuple(
    field_name: str,
    geometries: tuple[BaseGeometry, ...],
) -> tuple[BaseGeometry, ...]:
    if not isinstance(geometries, tuple):
        raise DistanceSetbackError(f"{field_name} must be an immutable tuple")
    for index, geometry in enumerate(geometries):
        _require_geometry(f"{field_name}[{index}]", geometry)
    return geometries


def _failure_message(hit: DistanceSetbackHit) -> str:
    return (
        f"geometry is {hit.actual_distance_m:.3f} m from "
        f"{hit.kind.value.lower()} feature #{hit.feature_index} in setback band "
        f"#{hit.band_index}; requires at least {hit.minimum_distance_m:.3f} m"
    )
