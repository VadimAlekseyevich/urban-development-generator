from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from geoalchemy2.shape import from_shape
from sqlalchemy import Table, delete, func, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.generated_entity import GeneratedRoad
from backend.app.models.generation_run import GenerationRun
from backend.app.models.source_layer import SourceRoad
from core.urban_generator.domain import DataError
from core.urban_generator.roads import (
    RoadClassificationOrigin,
    RoadClassificationResult,
    RoadGraph,
)


class GeneratedRoadPersistenceError(DataError):
    """Base error for persistence of generated road-network edges."""


class GeneratedRoadImmutableError(GeneratedRoadPersistenceError):
    """Raised before replacing roads owned by a successful run."""


@dataclass(frozen=True, slots=True)
class GeneratedRoadWriteResult:
    run_id: uuid.UUID
    working_srid: int
    deleted_rows: int
    inserted_rows: int
    insert_statements: int
    source_ref_count: int


class SqlAlchemyGeneratedRoadWriter:
    """Atomically replace one unfinished run's generated road edges."""

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
        self._table = cast(Table, GeneratedRoad.__table__)

    def replace(
        self,
        *,
        run_id: uuid.UUID,
        graph: RoadGraph,
        classification: RoadClassificationResult,
        source_road_ids_by_road_id: Mapping[str, tuple[uuid.UUID, ...]] | None = None,
    ) -> GeneratedRoadWriteResult:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        if not isinstance(graph, RoadGraph):
            raise TypeError("graph must be RoadGraph")
        if not isinstance(classification, RoadClassificationResult):
            raise TypeError("classification must be RoadClassificationResult")
        source_refs = source_road_ids_by_road_id or {}

        try:
            with self._session_factory() as session:
                with session.begin():
                    status, working_srid = self._load_run_context(session, run_id)
                    if status == "succeeded":
                        raise GeneratedRoadImmutableError(
                            "generated roads of a successful generation run are immutable"
                        )
                    if graph.working_crs.srid != working_srid:
                        raise GeneratedRoadPersistenceError(
                            "road graph working_srid must match generation run working_srid"
                        )

                    rows, referenced_source_ids = self._rows_from_results(
                        run_id=run_id,
                        working_srid=working_srid,
                        graph=graph,
                        classification=classification,
                        source_refs=source_refs,
                    )
                    self._require_source_roads_exist(session, referenced_source_ids)

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

            return GeneratedRoadWriteResult(
                run_id=run_id,
                working_srid=working_srid,
                deleted_rows=deleted_rows,
                inserted_rows=inserted_rows,
                insert_statements=insert_statements,
                source_ref_count=len(referenced_source_ids),
            )
        except GeneratedRoadPersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise GeneratedRoadPersistenceError(
                f"unable to persist generated roads for run {run_id}"
            ) from exc

    @staticmethod
    def _load_run_context(session: Session, run_id: uuid.UUID) -> tuple[str, int]:
        row = session.execute(
            select(GenerationRun.status, GenerationRun.working_srid)
            .where(GenerationRun.id == run_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            raise GeneratedRoadPersistenceError(f"generation run not found: {run_id}")
        return str(row.status), int(row.working_srid)

    @staticmethod
    def _rows_from_results(
        *,
        run_id: uuid.UUID,
        working_srid: int,
        graph: RoadGraph,
        classification: RoadClassificationResult,
        source_refs: Mapping[str, tuple[uuid.UUID, ...]],
    ) -> tuple[list[dict[str, Any]], frozenset[uuid.UUID]]:
        classified = {item.road_id: item for item in classification.roads}
        generated_edges = tuple(edge for edge in graph.edges if not edge.is_source)
        generated_road_ids = {edge.road_id for edge in generated_edges}

        missing = sorted(generated_road_ids - classified.keys())
        if missing:
            raise GeneratedRoadPersistenceError(
                f"classification is missing generated road ids: {missing!r}"
            )
        unknown_ref_keys = sorted(set(source_refs) - generated_road_ids)
        if unknown_ref_keys:
            raise GeneratedRoadPersistenceError(
                f"source refs contain unknown generated road ids: {unknown_ref_keys!r}"
            )

        rows: list[dict[str, Any]] = []
        referenced: set[uuid.UUID] = set()
        for edge in generated_edges:
            item = classified[edge.road_id]
            if item.origin is RoadClassificationOrigin.EXISTING or item.generated_class is None:
                raise GeneratedRoadPersistenceError(
                    f"generated edge {edge.edge_id!r} has non-generated classification"
                )
            refs = _validated_source_refs(edge.road_id, source_refs.get(edge.road_id, ()))
            referenced.update(refs)
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "run_id": run_id,
                    "geometry": from_shape(edge.geometry, srid=working_srid),
                    "attributes_json": {
                        "edge_id": edge.edge_id,
                        "road_id": edge.road_id,
                        "part_index": edge.part_index,
                        "length_m": edge.length_m,
                        "road_class": item.road_class,
                        "origin": item.origin.value,
                        "classification_reason": item.reason.value,
                        "source_node_id": edge.source.node_id,
                        "target_node_id": edge.target.node_id,
                        "source_road_ids": [str(value) for value in refs],
                        "classification_strategy": {
                            "name": classification.strategy_name,
                            "version": classification.strategy_version,
                        },
                    },
                }
            )

        return rows, frozenset(referenced)

    @staticmethod
    def _require_source_roads_exist(
        session: Session,
        source_road_ids: frozenset[uuid.UUID],
    ) -> None:
        if not source_road_ids:
            return
        existing = set(
            session.scalars(
                select(SourceRoad.id).where(SourceRoad.id.in_(source_road_ids))
            ).all()
        )
        missing = sorted(str(value) for value in source_road_ids - existing)
        if missing:
            raise GeneratedRoadPersistenceError(
                f"source road refs do not exist: {missing!r}"
            )


def _validated_source_refs(
    road_id: str,
    values: tuple[uuid.UUID, ...],
) -> tuple[uuid.UUID, ...]:
    if not isinstance(values, tuple):
        raise GeneratedRoadPersistenceError(
            f"source refs for road {road_id!r} must be an immutable tuple"
        )
    if any(not isinstance(value, uuid.UUID) for value in values):
        raise GeneratedRoadPersistenceError(
            f"source refs for road {road_id!r} must contain UUID values"
        )
    if len(set(values)) != len(values):
        raise GeneratedRoadPersistenceError(
            f"source refs for road {road_id!r} must be unique"
        )
    return tuple(sorted(values, key=str))
