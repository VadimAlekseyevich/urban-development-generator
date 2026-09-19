from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from geoalchemy2.shape import from_shape
from shapely.geometry import Polygon
from sqlalchemy import Table, delete, func, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.generated_entity import (
    GeneratedBlock,
    GeneratedBuilding,
    GeneratedParcel,
)
from backend.app.models.generation_run import GenerationRun
from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingAreaCalculationResult,
    BuildingAreaMetrics,
    BuildingAreaSubject,
    BuildingAttributeAssignmentResult,
)
from core.urban_generator.domain import DataError


class GeneratedBuildingPersistenceError(DataError):
    """Base error for persistence of generated buildings."""


class GeneratedBuildingImmutableError(GeneratedBuildingPersistenceError):
    """Raised before replacing buildings owned by a successful run."""


@dataclass(frozen=True, slots=True)
class GeneratedBuildingWriteResult:
    run_id: uuid.UUID
    working_srid: int
    deleted_rows: int
    inserted_rows: int
    insert_statements: int
    block_ref_count: int
    parcel_ref_count: int


@dataclass(frozen=True, slots=True)
class _BuildingSourceRef:
    source_kind: str
    block_id: uuid.UUID
    parcel_id: uuid.UUID | None


class SqlAlchemyGeneratedBuildingWriter:
    """Atomically replace one unfinished run's generated building rows.

    Core building ids remain backend-independent strings. This adapter resolves each
    T10 source_id against persisted run-scoped block/parcel keys, validates exact
    T10/T11 alignment, then stores typed building semantics plus provenance.
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
        self._table = cast(Table, GeneratedBuilding.__table__)

    def replace(
        self,
        *,
        run_id: uuid.UUID,
        subjects: tuple[BuildingAreaSubject, ...],
        assignment: BuildingAttributeAssignmentResult,
        area_result: BuildingAreaCalculationResult,
    ) -> GeneratedBuildingWriteResult:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        if not isinstance(subjects, tuple):
            raise TypeError("subjects must be an immutable tuple")
        if any(not isinstance(item, BuildingAreaSubject) for item in subjects):
            raise TypeError("subjects must contain BuildingAreaSubject values")
        if not isinstance(assignment, BuildingAttributeAssignmentResult):
            raise TypeError("assignment must be BuildingAttributeAssignmentResult")
        if not isinstance(area_result, BuildingAreaCalculationResult):
            raise TypeError("area_result must be BuildingAreaCalculationResult")

        try:
            with self._session_factory() as session:
                with session.begin():
                    status, working_srid = self._load_run_context(session, run_id)
                    if status == "succeeded":
                        raise GeneratedBuildingImmutableError(
                            "generated buildings of a successful generation run "
                            "are immutable"
                        )

                    ordered_subjects, assignments, metrics = self._validate_alignment(
                        working_srid=working_srid,
                        subjects=subjects,
                        assignment=assignment,
                        area_result=area_result,
                    )
                    refs = self._resolve_source_refs(
                        session,
                        run_id=run_id,
                        subjects=ordered_subjects,
                    )
                    rows = self._rows_from_results(
                        run_id=run_id,
                        working_srid=working_srid,
                        subjects=ordered_subjects,
                        assignments=assignments,
                        metrics=metrics,
                        refs=refs,
                        assignment=assignment,
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

            block_refs = {ref.block_id for ref in refs.values()}
            parcel_refs = {
                ref.parcel_id for ref in refs.values() if ref.parcel_id is not None
            }
            return GeneratedBuildingWriteResult(
                run_id=run_id,
                working_srid=working_srid,
                deleted_rows=deleted_rows,
                inserted_rows=inserted_rows,
                insert_statements=insert_statements,
                block_ref_count=len(block_refs),
                parcel_ref_count=len(parcel_refs),
            )
        except GeneratedBuildingPersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise GeneratedBuildingPersistenceError(
                f"unable to persist generated buildings for run {run_id}"
            ) from exc

    @staticmethod
    def _load_run_context(session: Session, run_id: uuid.UUID) -> tuple[str, int]:
        row = session.execute(
            select(GenerationRun.status, GenerationRun.working_srid)
            .where(GenerationRun.id == run_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            raise GeneratedBuildingPersistenceError(
                f"generation run not found: {run_id}"
            )
        return str(row.status), int(row.working_srid)

    @staticmethod
    def _validate_alignment(
        *,
        working_srid: int,
        subjects: tuple[BuildingAreaSubject, ...],
        assignment: BuildingAttributeAssignmentResult,
        area_result: BuildingAreaCalculationResult,
    ) -> tuple[
        tuple[BuildingAreaSubject, ...],
        dict[str, AssignedBuildingAttributes],
        dict[str, BuildingAreaMetrics],
    ]:
        if area_result.working_srid != working_srid:
            raise GeneratedBuildingPersistenceError(
                "area result working_srid must match generation run working_srid"
            )
        if any(subject.working_srid != working_srid for subject in subjects):
            raise GeneratedBuildingPersistenceError(
                "building subject working_srid must match generation run working_srid"
            )

        ordered = tuple(sorted(subjects, key=lambda item: item.building_id))
        subject_ids = tuple(item.building_id for item in ordered)
        if len(subject_ids) != len(set(subject_ids)):
            raise GeneratedBuildingPersistenceError(
                "building subject ids must be unique"
            )

        assignments = {item.building_id: item for item in assignment.buildings}
        metrics = {item.building_id: item for item in area_result.buildings}
        if tuple(sorted(assignments)) != subject_ids:
            raise GeneratedBuildingPersistenceError(
                "attribute assignments must cover exactly the persisted buildings"
            )
        if tuple(sorted(metrics)) != subject_ids:
            raise GeneratedBuildingPersistenceError(
                "area metrics must cover exactly the persisted buildings"
            )

        for subject in ordered:
            assigned = assignments[subject.building_id]
            metric = metrics[subject.building_id]
            if subject.attributes != assigned:
                raise GeneratedBuildingPersistenceError(
                    "subject attributes do not match assignment for "
                    f"{subject.building_id!r}"
                )
            if metric.floors != assigned.floors:
                raise GeneratedBuildingPersistenceError(
                    "area metric floors do not match assignment for "
                    f"{subject.building_id!r}"
                )
            footprint_area = float(subject.geometry.area)
            if not math.isclose(
                metric.footprint_area_m2,
                footprint_area,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise GeneratedBuildingPersistenceError(
                    "area metric footprint does not match geometry for "
                    f"{subject.building_id!r}"
                )
            expected_gfa = footprint_area * assigned.floors
            if not math.isclose(
                metric.gfa_m2,
                expected_gfa,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise GeneratedBuildingPersistenceError(
                    "area metric GFA does not match assigned floors for "
                    f"{subject.building_id!r}"
                )
            if not isinstance(subject.geometry, Polygon):
                raise GeneratedBuildingPersistenceError(
                    "generated building persistence requires Polygon geometry"
                )

        return ordered, assignments, metrics

    @staticmethod
    def _resolve_source_refs(
        session: Session,
        *,
        run_id: uuid.UUID,
        subjects: tuple[BuildingAreaSubject, ...],
    ) -> dict[str, _BuildingSourceRef]:
        source_ids = tuple(
            sorted({subject.attributes.source_id for subject in subjects})
        )
        if not source_ids:
            return {}

        parcel_rows = session.execute(
            select(
                GeneratedParcel.parcel_key,
                GeneratedParcel.id,
                GeneratedParcel.block_id,
            ).where(
                GeneratedParcel.run_id == run_id,
                GeneratedParcel.parcel_key.in_(source_ids),
            )
        ).all()
        block_rows = session.execute(
            select(GeneratedBlock.block_key, GeneratedBlock.id).where(
                GeneratedBlock.run_id == run_id,
                GeneratedBlock.block_key.in_(source_ids),
            )
        ).all()

        parcels: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
        referenced_block_ids: set[uuid.UUID] = set()
        for parcel_key, parcel_id, block_id in parcel_rows:
            if parcel_key is None or block_id is None:
                raise GeneratedBuildingPersistenceError(
                    "persisted parcel source must have parcel_key and block_id"
                )
            parcels[str(parcel_key)] = (parcel_id, block_id)
            referenced_block_ids.add(block_id)

        blocks = {
            str(block_key): block_id
            for block_key, block_id in block_rows
            if block_key is not None
        }
        if referenced_block_ids:
            valid_block_ids = set(
                session.scalars(
                    select(GeneratedBlock.id).where(
                        GeneratedBlock.run_id == run_id,
                        GeneratedBlock.id.in_(tuple(referenced_block_ids)),
                    )
                ).all()
            )
            missing_blocks = referenced_block_ids - valid_block_ids
            if missing_blocks:
                raise GeneratedBuildingPersistenceError(
                    "persisted parcel block refs must belong to the same generation run"
                )

        refs: dict[str, _BuildingSourceRef] = {}
        for source_id in source_ids:
            parcel = parcels.get(source_id)
            block = blocks.get(source_id)
            if parcel is not None and block is not None:
                raise GeneratedBuildingPersistenceError(
                    f"building source_id {source_id!r} is ambiguous between "
                    "persisted block and parcel"
                )
            if parcel is not None:
                parcel_id, block_id = parcel
                refs[source_id] = _BuildingSourceRef(
                    source_kind="parcel",
                    block_id=block_id,
                    parcel_id=parcel_id,
                )
                continue
            if block is not None:
                refs[source_id] = _BuildingSourceRef(
                    source_kind="block",
                    block_id=block,
                    parcel_id=None,
                )
                continue
            raise GeneratedBuildingPersistenceError(
                f"building source_id {source_id!r} does not resolve to a "
                "persisted block or parcel for this run"
            )
        return refs

    @classmethod
    def _rows_from_results(
        cls,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        subjects: tuple[BuildingAreaSubject, ...],
        assignments: dict[str, AssignedBuildingAttributes],
        metrics: dict[str, BuildingAreaMetrics],
        refs: dict[str, _BuildingSourceRef],
        assignment: BuildingAttributeAssignmentResult,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for subject in subjects:
            assigned = assignments[subject.building_id]
            metric = metrics[subject.building_id]
            ref = refs[assigned.source_id]
            rows.append(
                {
                    "id": cls._stable_entity_id(run_id, subject.building_id),
                    "run_id": run_id,
                    "geometry": from_shape(subject.geometry, srid=working_srid),
                    "building_key": subject.building_id,
                    "source_id": assigned.source_id,
                    "block_id": ref.block_id,
                    "parcel_id": ref.parcel_id,
                    "zone_class": assigned.zone_class.value,
                    "archetype": assigned.archetype.value,
                    "building_use": assigned.use.value,
                    "floors": assigned.floors,
                    "footprint_area_m2": metric.footprint_area_m2,
                    "gfa_m2": metric.gfa_m2,
                    "attributes_json": {
                        "source_kind": ref.source_kind,
                        "attribute_config_version": assignment.config_version,
                        "attribute_config_fingerprint": (
                            assignment.config_fingerprint
                        ),
                        "attribute_assignment_version": assigned.assignment_version,
                    },
                }
            )
        return rows

    @staticmethod
    def _stable_entity_id(run_id: uuid.UUID, building_key: str) -> uuid.UUID:
        return uuid.uuid5(run_id, f"generated-building:{building_key}")
