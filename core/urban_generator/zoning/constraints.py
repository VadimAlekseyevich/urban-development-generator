from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from core.urban_generator.domain.constraints import (
    ConstraintEngine,
    ConstraintScope,
    ValidationReport,
)
from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.domain.run_context import RunContext
from core.urban_generator.domain.territory import TerritorySnapshot
from core.urban_generator.zoning.assignment import ZoneAssignmentResult
from core.urban_generator.zoning.config import ZoneClass
from core.urban_generator.zoning.partition import ZoningPartitionResult

ZONE_CONSTRAINT_STAGE = "zoning"


class ZoneConstraintEvaluationError(ValueError):
    """Raised when zoning constraint evaluation violates the stage contract."""


@dataclass(frozen=True, slots=True)
class ZoneConstraintSubject:
    """One generated zone candidate passed to the shared ConstraintEngine."""

    cell_index: int
    seed_index: int
    zone_class: ZoneClass
    geometry: BaseGeometry
    area_m2: float
    suitability_score: float
    working_srid: int

    def __post_init__(self) -> None:
        _require_non_negative_int("cell_index", self.cell_index)
        _require_non_negative_int("seed_index", self.seed_index)
        if not isinstance(self.zone_class, ZoneClass):
            raise ZoneConstraintEvaluationError("zone_class must be a ZoneClass value")
        if not isinstance(self.geometry, BaseGeometry):
            raise ZoneConstraintEvaluationError("geometry must be a Shapely geometry")
        if self.geometry.is_empty or not self.geometry.is_valid:
            raise ZoneConstraintEvaluationError("geometry must be non-empty and valid")
        if self.geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ZoneConstraintEvaluationError("geometry must be polygonal")
        area_m2 = _require_positive_finite("area_m2", self.area_m2)
        if not math.isclose(
            area_m2,
            float(self.geometry.area),
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ZoneConstraintEvaluationError("area_m2 must match geometry area")
        score = _require_finite("suitability_score", self.suitability_score)
        if score < 0.0 or score > 1.0:
            raise ZoneConstraintEvaluationError("suitability_score must stay inside 0..1")
        require_working_crs(self.working_srid)


@dataclass(frozen=True, slots=True)
class EvaluatedZoneConstraints:
    """Constraint report returned by the shared engine for one generated zone subject."""

    subject: ZoneConstraintSubject
    report: ValidationReport

    def __post_init__(self) -> None:
        if not isinstance(self.subject, ZoneConstraintSubject):
            raise ZoneConstraintEvaluationError("subject must be a ZoneConstraintSubject")
        if not isinstance(self.report, ValidationReport):
            raise ZoneConstraintEvaluationError("report must be a ValidationReport")

    @property
    def is_valid(self) -> bool:
        return self.report.is_valid


@dataclass(frozen=True, slots=True)
class ZoneConstraintEvaluationResult:
    """Deterministic per-zone reports and aggregate shared-engine validation state."""

    evaluations: tuple[EvaluatedZoneConstraints, ...]
    stage: str
    evaluator_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.evaluations, tuple) or not self.evaluations:
            raise ZoneConstraintEvaluationError(
                "evaluations must be a non-empty immutable tuple"
            )
        if any(not isinstance(item, EvaluatedZoneConstraints) for item in self.evaluations):
            raise ZoneConstraintEvaluationError(
                "evaluations must contain only EvaluatedZoneConstraints values"
            )
        indexes = tuple(item.subject.cell_index for item in self.evaluations)
        if indexes != tuple(range(len(self.evaluations))):
            raise ZoneConstraintEvaluationError(
                "evaluations must contain every zone once in cell-index order"
            )
        if self.stage != ZONE_CONSTRAINT_STAGE:
            raise ZoneConstraintEvaluationError(
                f"stage must be {ZONE_CONSTRAINT_STAGE!r}"
            )
        if not isinstance(self.evaluator_version, str) or not self.evaluator_version:
            raise ZoneConstraintEvaluationError(
                "evaluator_version must be a non-empty string"
            )

    @property
    def report(self) -> ValidationReport:
        return ValidationReport(
            results=tuple(
                result
                for evaluation in self.evaluations
                for result in evaluation.report.results
            )
        )

    @property
    def valid_zone_count(self) -> int:
        return sum(evaluation.is_valid for evaluation in self.evaluations)

    @property
    def invalid_zone_count(self) -> int:
        return len(self.evaluations) - self.valid_zone_count


class ZoneConstraintEvaluator:
    """Route generated zoning candidates through the shared ConstraintEngine only."""

    version = "1"
    stage = ZONE_CONSTRAINT_STAGE

    def evaluate(
        self,
        *,
        partition: ZoningPartitionResult,
        assignment: ZoneAssignmentResult,
        snapshot: TerritorySnapshot,
        context: RunContext,
        engine: ConstraintEngine,
    ) -> ZoneConstraintEvaluationResult:
        if not isinstance(partition, ZoningPartitionResult):
            raise ZoneConstraintEvaluationError("partition must be a ZoningPartitionResult")
        if not isinstance(assignment, ZoneAssignmentResult):
            raise ZoneConstraintEvaluationError("assignment must be a ZoneAssignmentResult")
        if not isinstance(snapshot, TerritorySnapshot):
            raise ZoneConstraintEvaluationError("snapshot must be a TerritorySnapshot")
        if not isinstance(context, RunContext):
            raise ZoneConstraintEvaluationError("context must be a RunContext")
        if not callable(getattr(engine, "evaluate", None)):
            raise ZoneConstraintEvaluationError("engine must implement ConstraintEngine.evaluate")

        working_srid = partition.working_srid
        if snapshot.settings.working_srid != working_srid:
            raise ZoneConstraintEvaluationError(
                "snapshot working_srid must match zoning partition working_srid"
            )
        if context.working_srid != working_srid:
            raise ZoneConstraintEvaluationError(
                "run context working_srid must match zoning partition working_srid"
            )
        if len(assignment.assignments) != len(partition.cells):
            raise ZoneConstraintEvaluationError(
                "partition and assignment must contain the same number of zones"
            )

        evaluations: list[EvaluatedZoneConstraints] = []
        for cell_index, (cell, assigned) in enumerate(
            zip(partition.cells, assignment.assignments, strict=True)
        ):
            if assigned.cell_index != cell_index or assigned.seed_index != cell.seed_index:
                raise ZoneConstraintEvaluationError(
                    "assignment cell/seed references must match partition"
                )
            if not math.isclose(
                assigned.area_m2,
                cell.area_m2,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise ZoneConstraintEvaluationError(
                    "assignment area must match partition cell area"
                )
            subject = ZoneConstraintSubject(
                cell_index=cell_index,
                seed_index=cell.seed_index,
                zone_class=assigned.zone_class,
                geometry=cell.geometry,
                area_m2=cell.area_m2,
                suitability_score=assigned.suitability_score,
                working_srid=working_srid,
            )
            report = engine.evaluate(
                subject=subject,
                stage=self.stage,
                scope=ConstraintScope.ZONE,
                snapshot=snapshot,
                context=context,
            )
            if not isinstance(report, ValidationReport):
                raise ZoneConstraintEvaluationError(
                    "ConstraintEngine.evaluate must return ValidationReport"
                )
            evaluations.append(
                EvaluatedZoneConstraints(subject=subject, report=report)
            )

        return ZoneConstraintEvaluationResult(
            evaluations=tuple(evaluations),
            stage=self.stage,
            evaluator_version=self.version,
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ZoneConstraintEvaluationError(
            f"{field_name} must be a non-negative integer"
        )


def _require_finite(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ZoneConstraintEvaluationError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ZoneConstraintEvaluationError(f"{field_name} must be a finite number")
    return number


def _require_positive_finite(field_name: str, value: float) -> float:
    number = _require_finite(field_name, value)
    if number <= 0.0:
        raise ZoneConstraintEvaluationError(f"{field_name} must be greater than zero")
    return number
