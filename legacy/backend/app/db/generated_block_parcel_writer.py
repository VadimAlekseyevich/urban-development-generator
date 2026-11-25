from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from geoalchemy2.shape import from_shape
from sqlalchemy import Table, delete, func, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.generated_entity import (
    GeneratedBlock,
    GeneratedParcel,
    GeneratedZone,
)
from backend.app.models.generation_run import GenerationRun
from core.urban_generator.blocks import (
    BlockZoneAssociationResult,
    BlockZoneAssociationStatus,
    ParcelSubdivisionResult,
    PlanningParcel,
)
from core.urban_generator.domain import DataError


class GeneratedBlockParcelPersistenceError(DataError):
    """Base error for generated block/parcel persistence."""


class GeneratedBlockParcelImmutableError(GeneratedBlockParcelPersistenceError):
    """Raised before replacing block/parcel rows owned by a successful run."""


@dataclass(frozen=True, slots=True)
class GeneratedBlockParcelWriteResult:
    run_id: uuid.UUID
    working_srid: int
    deleted_block_rows: int
    deleted_parcel_rows: int
    inserted_block_rows: int
    inserted_parcel_rows: int
    block_insert_statements: int
    parcel_insert_statements: int
    zone_ref_count: int


class SqlAlchemyGeneratedBlockParcelWriter:
    """Atomically replace one unfinished run's generated blocks and planning parcels.

    Core block/parcel keys are preserved in typed columns while database row identifiers are
    deterministic UUID5 values scoped by the generation run. All generated-zone references are
    validated against the same run before any existing block or parcel row is removed.
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
        self._block_table = cast(Table, GeneratedBlock.__table__)
        self._parcel_table = cast(Table, GeneratedParcel.__table__)

    def replace(
        self,
        *,
        run_id: uuid.UUID,
        zoned_blocks: BlockZoneAssociationResult,
        subdivision: ParcelSubdivisionResult,
    ) -> GeneratedBlockParcelWriteResult:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        if not isinstance(zoned_blocks, BlockZoneAssociationResult):
            raise TypeError("zoned_blocks must be BlockZoneAssociationResult")
        if not isinstance(subdivision, ParcelSubdivisionResult):
            raise TypeError("subdivision must be ParcelSubdivisionResult")

        try:
            with self._session_factory() as session:
                with session.begin():
                    status, working_srid = self._load_run_context(session, run_id)
                    if status == "succeeded":
                        raise GeneratedBlockParcelImmutableError(
                            "generated blocks/parcels of a successful generation run are immutable"
                        )
                    self._validate_crs(
                        working_srid=working_srid,
                        zoned_blocks=zoned_blocks,
                        subdivision=subdivision,
                    )
                    decisions = self._validate_result_alignment(
                        zoned_blocks=zoned_blocks,
                        subdivision=subdivision,
                    )
                    expected_zone_classes = self._collect_zone_refs(
                        zoned_blocks=zoned_blocks,
                        subdivision=subdivision,
                    )
                    self._require_zones_exist(
                        session,
                        run_id=run_id,
                        expected_zone_classes=expected_zone_classes,
                    )
                    block_rows, block_db_ids = self._block_rows(
                        run_id=run_id,
                        working_srid=working_srid,
                        zoned_blocks=zoned_blocks,
                        decisions=decisions,
                    )
                    parcel_rows = self._parcel_rows(
                        run_id=run_id,
                        working_srid=working_srid,
                        subdivision=subdivision,
                        block_db_ids=block_db_ids,
                    )

                    deleted_parcel_rows = self._count_rows(
                        session,
                        self._parcel_table,
                        run_id,
                    )
                    deleted_block_rows = self._count_rows(
                        session,
                        self._block_table,
                        run_id,
                    )
                    session.execute(
                        delete(self._parcel_table).where(
                            self._parcel_table.c.run_id == run_id
                        )
                    )
                    session.execute(
                        delete(self._block_table).where(
                            self._block_table.c.run_id == run_id
                        )
                    )

                    inserted_block_rows, block_insert_statements = self._insert_chunks(
                        session,
                        self._block_table,
                        block_rows,
                    )
                    inserted_parcel_rows, parcel_insert_statements = self._insert_chunks(
                        session,
                        self._parcel_table,
                        parcel_rows,
                    )

            return GeneratedBlockParcelWriteResult(
                run_id=run_id,
                working_srid=working_srid,
                deleted_block_rows=deleted_block_rows,
                deleted_parcel_rows=deleted_parcel_rows,
                inserted_block_rows=inserted_block_rows,
                inserted_parcel_rows=inserted_parcel_rows,
                block_insert_statements=block_insert_statements,
                parcel_insert_statements=parcel_insert_statements,
                zone_ref_count=len(expected_zone_classes),
            )
        except GeneratedBlockParcelPersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise GeneratedBlockParcelPersistenceError(
                f"unable to persist generated blocks/parcels for run {run_id}"
            ) from exc

    @staticmethod
    def _load_run_context(session: Session, run_id: uuid.UUID) -> tuple[str, int]:
        row = session.execute(
            select(GenerationRun.status, GenerationRun.working_srid)
            .where(GenerationRun.id == run_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            raise GeneratedBlockParcelPersistenceError(
                f"generation run not found: {run_id}"
            )
        return str(row.status), int(row.working_srid)

    @staticmethod
    def _validate_crs(
        *,
        working_srid: int,
        zoned_blocks: BlockZoneAssociationResult,
        subdivision: ParcelSubdivisionResult,
    ) -> None:
        if zoned_blocks.working_crs.srid != working_srid:
            raise GeneratedBlockParcelPersistenceError(
                "zoned block working_srid must match generation run working_srid"
            )
        if subdivision.working_crs.srid != working_srid:
            raise GeneratedBlockParcelPersistenceError(
                "parcel subdivision working_srid must match generation run working_srid"
            )
        for parcel in subdivision.parcels:
            if parcel.working_srid != working_srid:
                raise GeneratedBlockParcelPersistenceError(
                    f"parcel {parcel.parcel_id!r} working_srid must match generation run"
                )

    @staticmethod
    def _validate_result_alignment(
        *,
        zoned_blocks: BlockZoneAssociationResult,
        subdivision: ParcelSubdivisionResult,
    ) -> dict[str, Any]:
        block_ids = tuple(
            sorted(item.cleaned_block.block_id for item in zoned_blocks.blocks)
        )
        decision_by_block = {decision.block_id: decision for decision in subdivision.decisions}
        if tuple(sorted(decision_by_block)) != block_ids:
            raise GeneratedBlockParcelPersistenceError(
                "subdivision decisions must cover exactly the persisted zoned blocks"
            )

        parcels_by_block: dict[str, list[PlanningParcel]] = defaultdict(list)
        for parcel in subdivision.parcels:
            if parcel.block_id not in decision_by_block:
                raise GeneratedBlockParcelPersistenceError(
                    f"parcel {parcel.parcel_id!r} references unknown block {parcel.block_id!r}"
                )
            parcels_by_block[parcel.block_id].append(parcel)

        for block_id, decision in decision_by_block.items():
            actual_ids = tuple(
                sorted(parcel.parcel_id for parcel in parcels_by_block.get(block_id, ()))
            )
            if decision.parcel_ids != actual_ids:
                raise GeneratedBlockParcelPersistenceError(
                    f"subdivision decision parcel ids do not match output for block {block_id!r}"
                )
        return decision_by_block

    @classmethod
    def _collect_zone_refs(
        cls,
        *,
        zoned_blocks: BlockZoneAssociationResult,
        subdivision: ParcelSubdivisionResult,
    ) -> dict[uuid.UUID, str]:
        expected: dict[uuid.UUID, str] = {}
        block_zone_by_key: dict[str, tuple[str | None, str | None]] = {}
        for item in zoned_blocks.blocks:
            association = item.association
            block_key = item.cleaned_block.block_id
            if association.status is BlockZoneAssociationStatus.ASSOCIATED:
                if association.zone_id is None or association.zone_class is None:
                    raise GeneratedBlockParcelPersistenceError(
                        f"associated block {block_key!r} is missing zone identity"
                    )
                zone_uuid = cls._register_zone_ref(
                    expected,
                    association.zone_id,
                    association.zone_class.value,
                    context=f"block {block_key!r}",
                )
                block_zone_by_key[block_key] = (
                    str(zone_uuid),
                    association.zone_class.value,
                )
            else:
                block_zone_by_key[block_key] = (None, None)

        for parcel in subdivision.parcels:
            expected_zone_id, expected_zone_class = block_zone_by_key[parcel.block_id]
            if expected_zone_id is None:
                if parcel.zone_id is not None or parcel.zone_class is not None:
                    raise GeneratedBlockParcelPersistenceError(
                        f"parcel {parcel.parcel_id!r} carries a zone for an unassociated block"
                    )
                continue
            if parcel.zone_id is None or parcel.zone_class is None:
                raise GeneratedBlockParcelPersistenceError(
                    f"parcel {parcel.parcel_id!r} must preserve its block zone relation"
                )
            zone_uuid = cls._register_zone_ref(
                expected,
                parcel.zone_id,
                parcel.zone_class.value,
                context=f"parcel {parcel.parcel_id!r}",
            )
            if str(zone_uuid) != expected_zone_id or parcel.zone_class.value != expected_zone_class:
                raise GeneratedBlockParcelPersistenceError(
                    f"parcel {parcel.parcel_id!r} zone relation must match its block"
                )
        return expected

    @staticmethod
    def _register_zone_ref(
        expected: dict[uuid.UUID, str],
        zone_id: str,
        zone_class: str,
        *,
        context: str,
    ) -> uuid.UUID:
        try:
            zone_uuid = uuid.UUID(zone_id)
        except ValueError as exc:
            raise GeneratedBlockParcelPersistenceError(
                f"{context} zone_id must be a persisted GeneratedZone UUID"
            ) from exc
        previous = expected.get(zone_uuid)
        if previous is not None and previous != zone_class:
            raise GeneratedBlockParcelPersistenceError(
                f"zone {zone_uuid} has conflicting expected classes"
            )
        expected[zone_uuid] = zone_class
        return zone_uuid

    @staticmethod
    def _require_zones_exist(
        session: Session,
        *,
        run_id: uuid.UUID,
        expected_zone_classes: dict[uuid.UUID, str],
    ) -> None:
        if not expected_zone_classes:
            return
        rows = session.execute(
            select(GeneratedZone.id, GeneratedZone.zone_class).where(
                GeneratedZone.run_id == run_id,
                GeneratedZone.id.in_(tuple(expected_zone_classes)),
            )
        ).all()
        persisted = {zone_id: str(zone_class) for zone_id, zone_class in rows}
        missing = sorted(str(value) for value in expected_zone_classes.keys() - persisted.keys())
        if missing:
            raise GeneratedBlockParcelPersistenceError(
                f"generated zone refs do not exist for run {run_id}: {missing!r}"
            )
        mismatched = sorted(
            str(zone_id)
            for zone_id, expected_class in expected_zone_classes.items()
            if persisted[zone_id] != expected_class
        )
        if mismatched:
            raise GeneratedBlockParcelPersistenceError(
                f"generated zone refs have unexpected zone_class: {mismatched!r}"
            )

    @classmethod
    def _block_rows(
        cls,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        zoned_blocks: BlockZoneAssociationResult,
        decisions: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, uuid.UUID]]:
        rows: list[dict[str, Any]] = []
        db_ids: dict[str, uuid.UUID] = {}
        for item in sorted(zoned_blocks.blocks, key=lambda value: value.cleaned_block.block_id):
            block = item.cleaned_block
            association = item.association
            block_key = block.block_id
            db_id = cls._stable_entity_id(run_id, "block", block_key)
            db_ids[block_key] = db_id
            zone_id = (
                uuid.UUID(association.zone_id)
                if association.zone_id is not None
                else None
            )
            decision = decisions[block_key]
            rows.append(
                {
                    "id": db_id,
                    "run_id": run_id,
                    "geometry": from_shape(block.geometry, srid=working_srid),
                    "block_key": block_key,
                    "zone_id": zone_id,
                    "area_m2": float(block.geometry.area),
                    "association_status": association.status.value,
                    "attributes_json": {
                        "member_block_ids": list(block.member_block_ids),
                        "input_block_ids": list(block.input_block_ids),
                        "zone_class": (
                            association.zone_class.value
                            if association.zone_class is not None
                            else None
                        ),
                        "positive_overlap_zone_ids": list(
                            association.positive_overlap_zone_ids
                        ),
                        "maximum_overlap_ratio": association.maximum_overlap_ratio,
                        "subdivision": {
                            "parcel_ids": list(decision.parcel_ids),
                            "parcel_count": len(decision.parcel_ids),
                            "skip_reason": (
                                decision.skip_reason.value
                                if decision.skip_reason is not None
                                else None
                            ),
                            "selected_frontage_road_id": decision.selected_frontage_road_id,
                            "selected_frontage_length_m": decision.selected_frontage_length_m,
                        },
                    },
                }
            )
        return rows, db_ids

    @classmethod
    def _parcel_rows(
        cls,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        subdivision: ParcelSubdivisionResult,
        block_db_ids: dict[str, uuid.UUID],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for parcel in sorted(subdivision.parcels, key=lambda value: value.parcel_id):
            block_db_id = block_db_ids.get(parcel.block_id)
            if block_db_id is None:
                raise GeneratedBlockParcelPersistenceError(
                    f"parcel {parcel.parcel_id!r} references unknown persisted block"
                )
            zone_id = uuid.UUID(parcel.zone_id) if parcel.zone_id is not None else None
            rows.append(
                {
                    "id": cls._stable_entity_id(run_id, "parcel", parcel.parcel_id),
                    "run_id": run_id,
                    "geometry": from_shape(parcel.geometry, srid=working_srid),
                    "parcel_key": parcel.parcel_id,
                    "block_id": block_db_id,
                    "zone_id": zone_id,
                    "area_m2": parcel.area_m2,
                    "buildable_area_m2": parcel.buildable_area_m2,
                    "frontage_m": parcel.frontage_length_m,
                    "buildable_geometry": from_shape(
                        parcel.buildable_envelope,
                        srid=working_srid,
                    ),
                    "attributes_json": {
                        "semantics": parcel.semantics,
                        "block_key": parcel.block_id,
                        "zone_class": (
                            parcel.zone_class.value
                            if parcel.zone_class is not None
                            else None
                        ),
                        "buildable_ratio": parcel.buildable_ratio,
                        "has_frontage": parcel.has_frontage,
                        "frontage_road_ids": list(parcel.frontage_road_ids),
                        "frontages": [
                            {
                                "road_id": frontage.road_id,
                                "length_m": frontage.length_m,
                                "geometry_wkt": frontage.geometry.wkt,
                            }
                            for frontage in parcel.frontages
                        ],
                    },
                }
            )
        return rows

    @staticmethod
    def _stable_entity_id(run_id: uuid.UUID, entity_kind: str, core_key: str) -> uuid.UUID:
        return uuid.uuid5(run_id, f"generated-{entity_kind}:{core_key}")

    @staticmethod
    def _count_rows(session: Session, table: Table, run_id: uuid.UUID) -> int:
        return int(
            session.scalar(
                select(func.count()).select_from(table).where(table.c.run_id == run_id)
            )
            or 0
        )

    def _insert_chunks(
        self,
        session: Session,
        table: Table,
        rows: list[dict[str, Any]],
    ) -> tuple[int, int]:
        inserted_rows = 0
        insert_statements = 0
        for start in range(0, len(rows), self._max_insert_rows):
            chunk = rows[start : start + self._max_insert_rows]
            if not chunk:
                continue
            session.execute(insert(table), chunk)
            inserted_rows += len(chunk)
            insert_statements += 1
        return inserted_rows, insert_statements
