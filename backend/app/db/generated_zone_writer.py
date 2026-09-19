from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import Table, delete, func, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.generated_entity import GeneratedZone
from backend.app.models.generation_run import GenerationRun
from core.urban_generator.domain import DataError
from core.urban_generator.domain.constraints import ConstraintResult
from core.urban_generator.zoning.assignment import ZoneAssignmentResult
from core.urban_generator.zoning.constraints import ZoneConstraintEvaluationResult
from core.urban_generator.zoning.identity import generated_zone_uuid
from core.urban_generator.zoning.partition import ZoningPartitionResult


class GeneratedZonePersistenceError(DataError):
    """Base error for persistence of generated functional zones."""


class GeneratedZoneImmutableError(GeneratedZonePersistenceError):
    """Raised before replacing zones owned by a successful run."""


@dataclass(frozen=True, slots=True)
class GeneratedZoneWriteResult:
    run_id: uuid.UUID
    working_srid: int
    deleted_rows: int
    inserted_rows: int
    insert_statements: int


class SqlAlchemyGeneratedZoneWriter:
    """Atomically replace one run's generated functional zones.

    The writer persists the final partition geometry plus its assigned functional class,
    metric area and shared-constraint diagnostics. Repeating the write for an unfinished
    run replaces previous rows in one transaction, while successful runs remain immutable.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        max_insert_rows: int = 5000,
    ) -> None:
        if (
            isinstance(max_insert_rows, bool)
            or not isinstance(max_insert_rows, int)
            or max_insert_rows <= 0
        ):
            raise ValueError("max_insert_rows must be a positive integer")
        self._session_factory = session_factory
        self._max_insert_rows = max_insert_rows
        self._table = cast(Table, GeneratedZone.__table__)

    def replace(
        self,
        *,
        run_id: uuid.UUID,
        partition: ZoningPartitionResult,
        assignment: ZoneAssignmentResult,
        constraint_evaluation: ZoneConstraintEvaluationResult,
    ) -> GeneratedZoneWriteResult:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        if not isinstance(partition, ZoningPartitionResult):
            raise TypeError("partition must be ZoningPartitionResult")
        if not isinstance(assignment, ZoneAssignmentResult):
            raise TypeError("assignment must be ZoneAssignmentResult")
        if not isinstance(constraint_evaluation, ZoneConstraintEvaluationResult):
            raise TypeError("constraint_evaluation must be ZoneConstraintEvaluationResult")

        try:
            with self._session_factory() as session:
                with session.begin():
                    status, working_srid = self._load_run_context(session, run_id)
                    if status == "succeeded":
                        raise GeneratedZoneImmutableError(
                            "generated zones of a successful generation run are immutable"
                        )
                    if partition.working_srid != working_srid:
                        raise GeneratedZonePersistenceError(
                            "partition working_srid must match generation run working_srid"
                        )

                    rows = self._rows_from_results(
                        run_id=run_id,
                        working_srid=working_srid,
                        partition=partition,
                        assignment=assignment,
                        constraint_evaluation=constraint_evaluation,
                    )

                    deleted_rows = int(
                        session.scalar(
                            select(func.count())
                            .select_from(self._table)
                            .where(self._table.c.run_id == run_id)
                        )
                        or 0
                    )
                    session.execute(
                        delete(self._table).where(self._table.c.run_id == run_id)
                    )

                    inserted_rows = 0
                    insert_statements = 0
                    for start in range(0, len(rows), self._max_insert_rows):
                        chunk = rows[start : start + self._max_insert_rows]
                        if not chunk:
                            continue
                        session.execute(insert(self._table), chunk)
                        inserted_rows += len(chunk)
                        insert_statements += 1

            return GeneratedZoneWriteResult(
                run_id=run_id,
                working_srid=working_srid,
                deleted_rows=deleted_rows,
                inserted_rows=inserted_rows,
                insert_statements=insert_statements,
            )
        except GeneratedZonePersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise GeneratedZonePersistenceError(
                f"unable to persist generated zones for run {run_id}"
            ) from exc

    @staticmethod
    def _load_run_context(session: Session, run_id: uuid.UUID) -> tuple[str, int]:
        row = session.execute(
            select(GenerationRun.status, GenerationRun.working_srid)
            .where(GenerationRun.id == run_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            raise GeneratedZonePersistenceError(f"generation run not found: {run_id}")
        return str(row.status), int(row.working_srid)

    @staticmethod
    def _rows_from_results(
        *,
        run_id: uuid.UUID,
        working_srid: int,
        partition: ZoningPartitionResult,
        assignment: ZoneAssignmentResult,
        constraint_evaluation: ZoneConstraintEvaluationResult,
    ) -> list[dict[str, Any]]:
        zone_count = len(partition.cells)
        if len(assignment.assignments) != zone_count:
            raise GeneratedZonePersistenceError(
                "partition and assignment must contain the same number of zones"
            )
        if len(constraint_evaluation.evaluations) != zone_count:
            raise GeneratedZonePersistenceError(
                "partition and constraint evaluation must contain the same number of zones"
            )

        rows: list[dict[str, Any]] = []
        for cell_index, (cell, assigned, evaluated) in enumerate(
            zip(
                partition.cells,
                assignment.assignments,
                constraint_evaluation.evaluations,
                strict=True,
            )
        ):
            subject = evaluated.subject
            if (
                assigned.cell_index != cell_index
                or assigned.seed_index != cell.seed_index
                or subject.cell_index != cell_index
                or subject.seed_index != cell.seed_index
            ):
                raise GeneratedZonePersistenceError(
                    "zoning result cell/seed references must align before persistence"
                )
            if assigned.zone_class is not subject.zone_class:
                raise GeneratedZonePersistenceError(
                    "assignment and constraint evaluation zone classes must match"
                )
            if subject.working_srid != working_srid:
                raise GeneratedZonePersistenceError(
                    "constraint subject working_srid must match generation run working_srid"
                )
            if not math.isclose(
                assigned.area_m2,
                cell.area_m2,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ) or not math.isclose(
                subject.area_m2,
                cell.area_m2,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise GeneratedZonePersistenceError(
                    "partition, assignment and constraint evaluation areas must match"
                )
            if not subject.geometry.equals(cell.geometry):
                raise GeneratedZonePersistenceError(
                    "constraint subject geometry must match partition cell geometry"
                )
            if not math.isclose(
                assigned.suitability_score,
                subject.suitability_score,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise GeneratedZonePersistenceError(
                    "assignment and constraint suitability scores must match"
                )

            diagnostics = {
                "cell_index": cell_index,
                "seed_index": cell.seed_index,
                "suitability_score": assigned.suitability_score,
                "validity_repaired": cell.validity_repaired,
                "constraints": {
                    "is_valid": evaluated.report.is_valid,
                    "results": [
                        _serialize_constraint_result(result)
                        for result in evaluated.report.results
                    ],
                },
                "provenance": {
                    "zoning_config_version": assignment.zoning_config_version,
                    "zoning_config_fingerprint": assignment.zoning_config_fingerprint,
                    "assignment_strategy_version": assignment.strategy_version,
                    "constraint_stage": constraint_evaluation.stage,
                    "constraint_evaluator_version": constraint_evaluation.evaluator_version,
                },
            }
            rows.append(
                {
                    "id": generated_zone_uuid(
                        run_id,
                        cell_index=cell_index,
                        seed_index=cell.seed_index,
                    ),
                    "run_id": run_id,
                    "geometry": from_shape(
                        _as_multipolygon(cell.geometry),
                        srid=working_srid,
                    ),
                    "zone_class": assigned.zone_class.value,
                    "area_m2": cell.area_m2,
                    "diagnostics_json": diagnostics,
                    "attributes_json": {},
                }
            )

        return rows


def _serialize_constraint_result(result: ConstraintResult) -> dict[str, Any]:
    return {
        "code": result.code,
        "severity": result.severity.value,
        "scope": result.scope.value,
        "passed": result.passed,
        "message": result.message,
    }


def _as_multipolygon(geometry: object) -> MultiPolygon:
    if isinstance(geometry, Polygon):
        return MultiPolygon([geometry])
    if isinstance(geometry, MultiPolygon):
        return geometry
    raise GeneratedZonePersistenceError("generated zone geometry must be polygonal")
