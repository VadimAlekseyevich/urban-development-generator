from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from shapely import normalize
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

from core.urban_generator.domain import (
    ConstraintEngine,
    ConstraintScope,
    RunContext,
    TerritorySnapshot,
    ValidationReport,
)
from core.urban_generator.domain.crs import require_working_crs

DEFAULT_MAX_ENVELOPE_PARTS = 128
DEFAULT_MAX_ENVELOPE_CONSTRAINT_EVALUATIONS = 32

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")
_EVALUATION_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class BuildingEnvelopeError(ValueError):
    """Raised when S08-T02 buildable-envelope inputs violate the core contract."""


class BuildingEnvelopeSourceKind(StrEnum):
    """Explicit upstream object used to derive a building envelope."""

    PARCEL = "parcel"
    BLOCK = "block"


class BuildingEnvelopeStatus(StrEnum):
    """Deterministic outcome of one envelope derivation."""

    READY = "READY"
    EMPTY_AFTER_MASK = "EMPTY_AFTER_MASK"
    EMPTY_AFTER_SETBACK = "EMPTY_AFTER_SETBACK"
    BELOW_MINIMUM_AREA = "BELOW_MINIMUM_AREA"
    HARD_CONSTRAINT_FAILED = "HARD_CONSTRAINT_FAILED"


@dataclass(frozen=True, slots=True)
class BuildingEnvelopeSource:
    """One parcel/block geometry expressed in the project working CRS."""

    source_id: str
    source_kind: BuildingEnvelopeSourceKind
    geometry: BaseGeometry
    working_srid: int

    def __post_init__(self) -> None:
        _require_id("source_id", self.source_id)
        if not isinstance(self.source_kind, BuildingEnvelopeSourceKind):
            raise BuildingEnvelopeError(
                "source_kind must be a BuildingEnvelopeSourceKind value"
            )
        require_working_crs(self.working_srid)
        _require_polygonal_geometry("source geometry", self.geometry)


@dataclass(frozen=True, slots=True)
class BuildingDevelopableMask:
    """Polygonal developable mask already expressed in the same metric CRS."""

    geometry: BaseGeometry
    working_srid: int

    def __post_init__(self) -> None:
        require_working_crs(self.working_srid)
        _require_polygonal_geometry("developable mask", self.geometry)


@dataclass(frozen=True, slots=True)
class BuildingEnvelopePolicy:
    """Local envelope policy before shared constraint-engine validation."""

    boundary_setback_m: float = 0.0
    minimum_area_m2: float = 1.0

    def __post_init__(self) -> None:
        setback = _require_non_negative_finite(
            "boundary_setback_m",
            self.boundary_setback_m,
        )
        minimum = _require_positive_finite(
            "minimum_area_m2",
            self.minimum_area_m2,
        )
        object.__setattr__(self, "boundary_setback_m", setback)
        object.__setattr__(self, "minimum_area_m2", minimum)


EnvelopeSubjectFactory = Callable[[BaseGeometry, int], object]


@dataclass(frozen=True, slots=True)
class EngineBuildingEnvelopeConstraintEvaluator:
    """Adapter that evaluates one homogeneous typed rule set through ConstraintEngine.

    Different existing building constraints intentionally use different subject types
    (geometry/setback/raster). One adapter therefore owns one engine plus a subject
    factory, allowing S08 to compose those rule sets without inventing an untyped
    universal constraint subject or duplicating rule logic.
    """

    evaluation_id: str
    engine: ConstraintEngine
    subject_factory: EnvelopeSubjectFactory

    def __post_init__(self) -> None:
        if (
            not isinstance(self.evaluation_id, str)
            or _EVALUATION_ID_RE.fullmatch(self.evaluation_id) is None
        ):
            raise BuildingEnvelopeError(
                f"invalid constraint evaluation id: {self.evaluation_id!r}"
            )
        if not isinstance(self.engine, ConstraintEngine):
            raise BuildingEnvelopeError("engine must implement ConstraintEngine")
        if not callable(self.subject_factory):
            raise BuildingEnvelopeError("subject_factory must be callable")

    def evaluate(
        self,
        *,
        geometry: BaseGeometry,
        working_srid: int,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ValidationReport:
        subject = self.subject_factory(geometry, working_srid)
        return self.engine.evaluate(
            subject=subject,
            stage="buildings",
            scope=ConstraintScope.BUILDING,
            snapshot=snapshot,
            context=context,
        )


@dataclass(frozen=True, slots=True)
class BuildingEnvelopeConstraintReport:
    evaluation_id: str
    report: ValidationReport

    def __post_init__(self) -> None:
        if (
            not isinstance(self.evaluation_id, str)
            or _EVALUATION_ID_RE.fullmatch(self.evaluation_id) is None
        ):
            raise BuildingEnvelopeError(
                f"invalid constraint evaluation id: {self.evaluation_id!r}"
            )
        if not isinstance(self.report, ValidationReport):
            raise BuildingEnvelopeError("report must be a ValidationReport")


@dataclass(frozen=True, slots=True)
class BuildingEnvelopeResult:
    """Candidate envelope plus explicit readiness/constraint diagnostics."""

    source_id: str
    source_kind: BuildingEnvelopeSourceKind
    working_srid: int
    status: BuildingEnvelopeStatus
    candidate_geometry: BaseGeometry | None
    constraint_reports: tuple[BuildingEnvelopeConstraintReport, ...]

    def __post_init__(self) -> None:
        _require_id("source_id", self.source_id)
        if not isinstance(self.source_kind, BuildingEnvelopeSourceKind):
            raise BuildingEnvelopeError(
                "source_kind must be a BuildingEnvelopeSourceKind value"
            )
        require_working_crs(self.working_srid)
        if not isinstance(self.status, BuildingEnvelopeStatus):
            raise BuildingEnvelopeError("status must be a BuildingEnvelopeStatus")
        if self.candidate_geometry is not None:
            _require_polygonal_geometry(
                "candidate geometry",
                self.candidate_geometry,
            )
        if not isinstance(self.constraint_reports, tuple):
            raise BuildingEnvelopeError(
                "constraint_reports must be an immutable tuple"
            )
        if any(
            not isinstance(item, BuildingEnvelopeConstraintReport)
            for item in self.constraint_reports
        ):
            raise BuildingEnvelopeError(
                "constraint_reports must contain BuildingEnvelopeConstraintReport values"
            )
        ids = tuple(item.evaluation_id for item in self.constraint_reports)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise BuildingEnvelopeError(
                "constraint reports must use sorted unique evaluation ids"
            )

        if self.status in {
            BuildingEnvelopeStatus.EMPTY_AFTER_MASK,
            BuildingEnvelopeStatus.EMPTY_AFTER_SETBACK,
        }:
            if self.candidate_geometry is not None:
                raise BuildingEnvelopeError(
                    "empty envelope status must not carry candidate geometry"
                )
        elif self.candidate_geometry is None:
            raise BuildingEnvelopeError(
                "non-empty envelope status requires candidate geometry"
            )

        has_hard_failure = any(
            report.report.hard_failures for report in self.constraint_reports
        )
        if self.status is BuildingEnvelopeStatus.HARD_CONSTRAINT_FAILED:
            if not has_hard_failure:
                raise BuildingEnvelopeError(
                    "HARD_CONSTRAINT_FAILED requires a hard constraint failure"
                )
        elif has_hard_failure:
            raise BuildingEnvelopeError(
                "hard constraint failure requires HARD_CONSTRAINT_FAILED status"
            )

    @property
    def is_buildable(self) -> bool:
        return self.status is BuildingEnvelopeStatus.READY

    @property
    def buildable_geometry(self) -> BaseGeometry | None:
        return self.candidate_geometry if self.is_buildable else None

    @property
    def area_m2(self) -> float:
        if self.candidate_geometry is None:
            return 0.0
        return float(self.candidate_geometry.area)


class BuildableEnvelopeBuilder:
    """Derive a bounded polygonal building envelope then validate shared constraints."""

    def __init__(
        self,
        *,
        working_srid: int,
        policy: BuildingEnvelopePolicy | None = None,
        max_output_parts: int = DEFAULT_MAX_ENVELOPE_PARTS,
        max_constraint_evaluations: int = DEFAULT_MAX_ENVELOPE_CONSTRAINT_EVALUATIONS,
    ) -> None:
        self.working_crs = require_working_crs(working_srid)
        self.policy = policy if policy is not None else BuildingEnvelopePolicy()
        if not isinstance(self.policy, BuildingEnvelopePolicy):
            raise BuildingEnvelopeError("policy must be a BuildingEnvelopePolicy")
        _require_positive_int("max_output_parts", max_output_parts)
        _require_positive_int(
            "max_constraint_evaluations",
            max_constraint_evaluations,
        )
        self.max_output_parts = max_output_parts
        self.max_constraint_evaluations = max_constraint_evaluations

    def build(
        self,
        source: BuildingEnvelopeSource,
        *,
        developable_mask: BuildingDevelopableMask,
        snapshot: TerritorySnapshot,
        context: RunContext,
        constraint_evaluators: tuple[
            EngineBuildingEnvelopeConstraintEvaluator, ...
        ] = (),
    ) -> BuildingEnvelopeResult:
        self._validate_inputs(
            source=source,
            developable_mask=developable_mask,
            snapshot=snapshot,
            context=context,
            constraint_evaluators=constraint_evaluators,
        )

        masked = _polygonal_geometry(
            source.geometry.intersection(developable_mask.geometry)
        )
        if masked is None:
            return self._empty_result(
                source,
                BuildingEnvelopeStatus.EMPTY_AFTER_MASK,
            )

        if self.policy.boundary_setback_m > 0.0:
            inset = _polygonal_geometry(
                masked.buffer(
                    -self.policy.boundary_setback_m,
                    join_style="mitre",
                )
            )
            if inset is None:
                return self._empty_result(
                    source,
                    BuildingEnvelopeStatus.EMPTY_AFTER_SETBACK,
                )
            candidate = inset
        else:
            candidate = masked

        part_count = _polygon_part_count(candidate)
        if part_count > self.max_output_parts:
            raise BuildingEnvelopeError(
                "buildable envelope output part limit exceeded: "
                f"{part_count} > {self.max_output_parts}"
            )

        if float(candidate.area) < self.policy.minimum_area_m2:
            return BuildingEnvelopeResult(
                source_id=source.source_id,
                source_kind=source.source_kind,
                working_srid=self.working_crs.srid,
                status=BuildingEnvelopeStatus.BELOW_MINIMUM_AREA,
                candidate_geometry=candidate,
                constraint_reports=(),
            )

        reports: list[BuildingEnvelopeConstraintReport] = []
        hard_failed = False
        for evaluator in sorted(
            constraint_evaluators,
            key=lambda item: item.evaluation_id,
        ):
            report = evaluator.evaluate(
                geometry=candidate,
                working_srid=self.working_crs.srid,
                snapshot=snapshot,
                context=context,
            )
            reports.append(
                BuildingEnvelopeConstraintReport(
                    evaluation_id=evaluator.evaluation_id,
                    report=report,
                )
            )
            hard_failed = hard_failed or bool(report.hard_failures)

        return BuildingEnvelopeResult(
            source_id=source.source_id,
            source_kind=source.source_kind,
            working_srid=self.working_crs.srid,
            status=(
                BuildingEnvelopeStatus.HARD_CONSTRAINT_FAILED
                if hard_failed
                else BuildingEnvelopeStatus.READY
            ),
            candidate_geometry=candidate,
            constraint_reports=tuple(reports),
        )

    def _validate_inputs(
        self,
        *,
        source: BuildingEnvelopeSource,
        developable_mask: BuildingDevelopableMask,
        snapshot: TerritorySnapshot,
        context: RunContext,
        constraint_evaluators: tuple[
            EngineBuildingEnvelopeConstraintEvaluator, ...
        ],
    ) -> None:
        if not isinstance(source, BuildingEnvelopeSource):
            raise BuildingEnvelopeError(
                "source must be a BuildingEnvelopeSource"
            )
        if source.working_srid != self.working_crs.srid:
            raise BuildingEnvelopeError(
                "source working_srid must match builder working CRS"
            )
        if not isinstance(developable_mask, BuildingDevelopableMask):
            raise BuildingEnvelopeError(
                "developable_mask must be a BuildingDevelopableMask"
            )
        if developable_mask.working_srid != self.working_crs.srid:
            raise BuildingEnvelopeError(
                "developable mask working_srid must match builder working CRS"
            )
        if not isinstance(snapshot, TerritorySnapshot):
            raise BuildingEnvelopeError("snapshot must be a TerritorySnapshot")
        if snapshot.settings.working_srid != self.working_crs.srid:
            raise BuildingEnvelopeError(
                "snapshot working_srid must match builder working CRS"
            )
        if not isinstance(context, RunContext):
            raise BuildingEnvelopeError("context must be a RunContext")
        if context.working_srid != self.working_crs.srid:
            raise BuildingEnvelopeError(
                "context working_srid must match builder working CRS"
            )
        if not isinstance(constraint_evaluators, tuple):
            raise BuildingEnvelopeError(
                "constraint_evaluators must be an immutable tuple"
            )
        if len(constraint_evaluators) > self.max_constraint_evaluations:
            raise BuildingEnvelopeError(
                "buildable envelope constraint evaluation limit exceeded: "
                f"{len(constraint_evaluators)} > "
                f"{self.max_constraint_evaluations}"
            )
        if any(
            not isinstance(item, EngineBuildingEnvelopeConstraintEvaluator)
            for item in constraint_evaluators
        ):
            raise BuildingEnvelopeError(
                "constraint_evaluators must contain "
                "EngineBuildingEnvelopeConstraintEvaluator values"
            )
        ids = tuple(item.evaluation_id for item in constraint_evaluators)
        if len(ids) != len(set(ids)):
            raise BuildingEnvelopeError(
                "constraint evaluator ids must be unique"
            )

    def _empty_result(
        self,
        source: BuildingEnvelopeSource,
        status: BuildingEnvelopeStatus,
    ) -> BuildingEnvelopeResult:
        return BuildingEnvelopeResult(
            source_id=source.source_id,
            source_kind=source.source_kind,
            working_srid=self.working_crs.srid,
            status=status,
            candidate_geometry=None,
            constraint_reports=(),
        )


def _polygonal_geometry(geometry: BaseGeometry) -> BaseGeometry | None:
    if geometry.is_empty:
        return None
    repaired = geometry if geometry.is_valid else make_valid(geometry)
    parts = _polygon_parts(repaired)
    if not parts:
        return None
    combined = unary_union(parts)
    if combined.is_empty:
        return None
    if not combined.is_valid:
        combined = make_valid(combined)
    parts = _polygon_parts(combined)
    if not parts:
        return None
    normalized = normalize(unary_union(parts))
    if isinstance(normalized, (Polygon, MultiPolygon)):
        return normalized
    raise BuildingEnvelopeError(
        "buildable envelope normalization produced non-polygonal geometry"
    )


def _polygon_parts(geometry: BaseGeometry) -> tuple[Polygon, ...]:
    if isinstance(geometry, Polygon):
        return (geometry,) if _positive_polygon(geometry) else ()
    if isinstance(geometry, MultiPolygon):
        return tuple(
            polygon
            for polygon in geometry.geoms
            if _positive_polygon(polygon)
        )
    if isinstance(geometry, GeometryCollection):
        parts: list[Polygon] = []
        for item in geometry.geoms:
            parts.extend(_polygon_parts(item))
        return tuple(parts)
    return ()


def _polygon_part_count(geometry: BaseGeometry) -> int:
    if isinstance(geometry, Polygon):
        return 1
    if isinstance(geometry, MultiPolygon):
        return len(geometry.geoms)
    raise BuildingEnvelopeError(
        "candidate geometry must be Polygon or MultiPolygon"
    )


def _positive_polygon(geometry: Polygon) -> bool:
    return (
        not geometry.is_empty
        and geometry.is_valid
        and not geometry.has_z
        and math.isfinite(float(geometry.area))
        and float(geometry.area) > 0.0
    )


def _require_polygonal_geometry(
    field_name: str,
    geometry: BaseGeometry,
) -> None:
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise BuildingEnvelopeError(
            f"{field_name} must be Polygon or MultiPolygon"
        )
    if geometry.is_empty or not geometry.is_valid or geometry.has_z:
        raise BuildingEnvelopeError(
            f"{field_name} must be non-empty, valid and 2D"
        )
    area = float(geometry.area)
    if not math.isfinite(area) or area <= 0.0:
        raise BuildingEnvelopeError(
            f"{field_name} must have positive finite area"
        )


def _require_id(field_name: str, value: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise BuildingEnvelopeError(f"invalid {field_name}: {value!r}")


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BuildingEnvelopeError(
            f"{field_name} must be a positive integer"
        )


def _require_non_negative_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BuildingEnvelopeError(
            f"{field_name} must be a finite non-negative number"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise BuildingEnvelopeError(
            f"{field_name} must be a finite non-negative number"
        )
    return number


def _require_positive_finite(field_name: str, value: float) -> float:
    number = _require_non_negative_finite(field_name, value)
    if number <= 0.0:
        raise BuildingEnvelopeError(f"{field_name} must be greater than zero")
    return number
