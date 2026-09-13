from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry, prep
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


class GeometryExclusionError(ConstraintContractError):
    """Raised when geometry exclusion inputs violate the spatial contract."""


class GeometryExclusionCandidateLimitError(GeometryExclusionError):
    """Raised instead of evaluating an unbounded number of spatial candidates."""


class GeometryExclusionReason(StrEnum):
    OUTSIDE_BOUNDARY = "OUTSIDE_BOUNDARY"
    WATER = "WATER"
    PROTECTED = "PROTECTED"


@dataclass(frozen=True, slots=True)
class GeometryExclusionSubject:
    """One candidate geometry expressed in the project working CRS."""

    geometry: BaseGeometry
    working_srid: int

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        _require_geometry("subject", self.geometry)


@dataclass(frozen=True, slots=True)
class GeometryExclusionHit:
    """Deterministic explanation of the first blocking spatial relationship."""

    reason: GeometryExclusionReason
    exclusion_index: int | None = None


class GeometryExclusionIndex:
    """Reusable prepared/indexed geometry state for repeated exclusion checks."""

    def __init__(
        self,
        *,
        boundary: BaseGeometry,
        working_srid: int,
        water: tuple[BaseGeometry, ...] = (),
        protected: tuple[BaseGeometry, ...] = (),
        max_candidates: int = 10_000,
    ) -> None:
        require_working_crs(working_srid)
        _require_boundary(boundary)
        water_geometries = _require_geometry_tuple("water", water)
        protected_geometries = _require_geometry_tuple("protected", protected)
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int):
            raise GeometryExclusionError("max_candidates must be an integer")
        if max_candidates <= 0:
            raise GeometryExclusionError("max_candidates must be positive")

        self.working_srid = working_srid
        self.max_candidates = max_candidates
        self._boundary = boundary
        self._prepared_boundary: PreparedGeometry = prep(boundary)
        self._geometries = (*water_geometries, *protected_geometries)
        self._metadata = (
            *(
                (GeometryExclusionReason.WATER, index)
                for index, _geometry in enumerate(water_geometries)
            ),
            *(
                (GeometryExclusionReason.PROTECTED, index)
                for index, _geometry in enumerate(protected_geometries)
            ),
        )
        self._tree = STRtree(self._geometries) if self._geometries else None
        self._water_count = len(water_geometries)
        self._protected_count = len(protected_geometries)

    @property
    def exclusion_count(self) -> int:
        return len(self._geometries)

    @property
    def water_count(self) -> int:
        return self._water_count

    @property
    def protected_count(self) -> int:
        return self._protected_count

    def check(self, subject: GeometryExclusionSubject) -> GeometryExclusionHit | None:
        """Return the first deterministic exclusion hit, or ``None`` when allowed."""

        if not isinstance(subject, GeometryExclusionSubject):
            raise GeometryExclusionError("subject must be a GeometryExclusionSubject")
        if subject.working_srid != self.working_srid:
            raise GeometryExclusionError(
                "subject working_srid must match geometry exclusion index working_srid"
            )
        if not self._prepared_boundary.covers(subject.geometry):
            return GeometryExclusionHit(reason=GeometryExclusionReason.OUTSIDE_BOUNDARY)
        if self._tree is None:
            return None

        candidate_indexes = self._tree.query(subject.geometry)
        if len(candidate_indexes) > self.max_candidates:
            raise GeometryExclusionCandidateLimitError(
                "geometry exclusion candidate limit exceeded: "
                f"{len(candidate_indexes)} > {self.max_candidates}"
            )

        prepared_subject = prep(subject.geometry)
        ordered_indexes = sorted(
            (int(index) for index in candidate_indexes),
            key=lambda index: _hit_order(self._metadata[index]),
        )
        for index in ordered_indexes:
            if prepared_subject.intersects(self._geometries[index]):
                reason, exclusion_index = self._metadata[index]
                return GeometryExclusionHit(
                    reason=reason,
                    exclusion_index=exclusion_index,
                )
        return None


class GeometryExclusionConstraint:
    """HARD rule requiring a candidate to stay inside boundary and outside exclusions."""

    severity = ConstraintSeverity.HARD

    def __init__(
        self,
        *,
        scope: ConstraintScope,
        index: GeometryExclusionIndex,
        code: str = "geometry.exclusion",
    ) -> None:
        if not isinstance(index, GeometryExclusionIndex):
            raise GeometryExclusionError("index must be a GeometryExclusionIndex")
        validate_constraint_metadata(code, self.severity, scope)
        self.code = code
        self.scope = scope
        self.index = index

    def evaluate(
        self,
        *,
        subject: GeometryExclusionSubject,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ConstraintResult:
        if snapshot.settings.working_srid != self.index.working_srid:
            raise GeometryExclusionError(
                "snapshot working_srid must match geometry exclusion index working_srid"
            )
        if context.working_srid != self.index.working_srid:
            raise GeometryExclusionError(
                "run context working_srid must match geometry exclusion index working_srid"
            )

        hit = self.index.check(subject)
        if hit is None:
            return ConstraintResult(
                code=self.code,
                severity=self.severity,
                scope=self.scope,
                passed=True,
                message="geometry is inside the project boundary and outside exclusions",
            )
        return ConstraintResult(
            code=self.code,
            severity=self.severity,
            scope=self.scope,
            passed=False,
            message=_failure_message(hit),
        )


def _require_boundary(boundary: BaseGeometry) -> None:
    _require_geometry("boundary", boundary)
    if boundary.geom_type not in {"Polygon", "MultiPolygon"}:
        raise GeometryExclusionError("boundary must be Polygon or MultiPolygon")


def _require_geometry(field_name: str, geometry: BaseGeometry) -> None:
    if not isinstance(geometry, BaseGeometry):
        raise GeometryExclusionError(f"{field_name} must be a Shapely geometry")
    if geometry.is_empty:
        raise GeometryExclusionError(f"{field_name} geometry must not be empty")
    if not geometry.is_valid:
        raise GeometryExclusionError(f"{field_name} geometry must be valid")


def _require_geometry_tuple(
    field_name: str,
    geometries: tuple[BaseGeometry, ...],
) -> tuple[BaseGeometry, ...]:
    if not isinstance(geometries, tuple):
        raise GeometryExclusionError(f"{field_name} geometries must be an immutable tuple")
    for index, geometry in enumerate(geometries):
        _require_geometry(f"{field_name}[{index}]", geometry)
    return geometries


def _hit_order(metadata: tuple[GeometryExclusionReason, int]) -> tuple[int, int]:
    reason, exclusion_index = metadata
    priority = {
        GeometryExclusionReason.WATER: 0,
        GeometryExclusionReason.PROTECTED: 1,
    }[reason]
    return priority, exclusion_index


def _failure_message(hit: GeometryExclusionHit) -> str:
    if hit.reason is GeometryExclusionReason.OUTSIDE_BOUNDARY:
        return "geometry is not fully covered by the project boundary"
    if hit.reason is GeometryExclusionReason.WATER:
        return f"geometry intersects water exclusion #{hit.exclusion_index}"
    return f"geometry intersects protected exclusion #{hit.exclusion_index}"
