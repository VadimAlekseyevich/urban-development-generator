from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from geoalchemy2.shape import from_shape
from sqlalchemy import Table, delete, func, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.generated_entity import (
    GeneratedBuilding,
    GeneratedInfrastructure,
)
from backend.app.models.generation_run import GenerationRun
from core.urban_generator.domain import DataError
from core.urban_generator.infrastructure import (
    MAX_INFRASTRUCTURE_PLACEMENT_FACILITIES,
    InfrastructureCandidateGeometry,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidateGeometryResult,
    InfrastructureCandidateRef,
    InfrastructureCandidateSnap,
    InfrastructureCandidateUnsnapped,
    InfrastructureGreedyPlacementState,
    InfrastructureNetworkSnapBatchResult,
    InfrastructureType,
)


class GeneratedInfrastructurePersistenceError(DataError):
    """Base error for generated-infrastructure persistence."""


class GeneratedInfrastructureImmutableError(
    GeneratedInfrastructurePersistenceError
):
    """Raised before replacing infrastructure owned by a successful run."""


@dataclass(frozen=True, slots=True)
class GeneratedInfrastructureWriteResult:
    run_id: uuid.UUID
    infrastructure_type_code: str
    working_srid: int
    deleted_rows: int
    inserted_rows: int
    insert_statements: int
    host_building_ref_count: int


class SqlAlchemyGeneratedInfrastructureWriter:
    """Atomically replace one unfinished run/type's accepted facilities."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        max_rows: int = MAX_INFRASTRUCTURE_PLACEMENT_FACILITIES,
        max_insert_rows: int = 5000,
    ) -> None:
        self._require_positive_int("max_rows", max_rows)
        self._require_positive_int("max_insert_rows", max_insert_rows)
        if max_rows > MAX_INFRASTRUCTURE_PLACEMENT_FACILITIES:
            raise ValueError(
                "max_rows exceeds infrastructure placement hard limit: "
                f"{max_rows} > {MAX_INFRASTRUCTURE_PLACEMENT_FACILITIES}"
            )
        self._session_factory = session_factory
        self._max_rows = max_rows
        self._max_insert_rows = max_insert_rows
        self._table = cast(Table, GeneratedInfrastructure.__table__)

    def replace(
        self,
        *,
        run_id: uuid.UUID,
        state: InfrastructureGreedyPlacementState,
        candidate_geometry: InfrastructureCandidateGeometryResult,
        candidate_snaps: InfrastructureNetworkSnapBatchResult,
        infrastructure_type: InfrastructureType,
    ) -> GeneratedInfrastructureWriteResult:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        if not isinstance(state, InfrastructureGreedyPlacementState):
            raise TypeError("state must be InfrastructureGreedyPlacementState")
        if not isinstance(
            candidate_geometry,
            InfrastructureCandidateGeometryResult,
        ):
            raise TypeError(
                "candidate_geometry must be InfrastructureCandidateGeometryResult"
            )
        if not isinstance(
            candidate_snaps,
            InfrastructureNetworkSnapBatchResult,
        ):
            raise TypeError(
                "candidate_snaps must be InfrastructureNetworkSnapBatchResult"
            )
        if not isinstance(infrastructure_type, InfrastructureType):
            raise TypeError("infrastructure_type must be InfrastructureType")
        if len(state.accepted_facilities) > self._max_rows:
            raise GeneratedInfrastructurePersistenceError(
                "generated infrastructure row limit exceeded: "
                f"{len(state.accepted_facilities)} > {self._max_rows}"
            )

        try:
            with self._session_factory() as session:
                with session.begin():
                    status, working_srid = self._load_run_context(session, run_id)
                    if status == "succeeded":
                        raise GeneratedInfrastructureImmutableError(
                            "generated infrastructure of a successful generation "
                            "run is immutable"
                        )

                    geometry_by_key, snap_by_key = self._validate_alignment(
                        working_srid=working_srid,
                        state=state,
                        candidate_geometry=candidate_geometry,
                        candidate_snaps=candidate_snaps,
                        infrastructure_type=infrastructure_type,
                    )
                    host_buildings = self._resolve_host_buildings(
                        session,
                        run_id=run_id,
                        state=state,
                        geometry_by_key=geometry_by_key,
                    )
                    rows = self._rows_from_results(
                        run_id=run_id,
                        working_srid=working_srid,
                        state=state,
                        geometry_by_key=geometry_by_key,
                        snap_by_key=snap_by_key,
                        host_buildings=host_buildings,
                        infrastructure_type=infrastructure_type,
                        network_snapshot_id=candidate_snaps.snapshot_id,
                    )

                    scope = (
                        self._table.c.run_id == run_id,
                        self._table.c.infrastructure_type_code
                        == infrastructure_type.code,
                    )
                    deleted_rows = int(
                        session.scalar(
                            select(func.count())
                            .select_from(self._table)
                            .where(*scope)
                        )
                        or 0
                    )
                    session.execute(delete(self._table).where(*scope))

                    inserted_rows = 0
                    insert_statements = 0
                    for start in range(0, len(rows), self._max_insert_rows):
                        chunk = rows[start : start + self._max_insert_rows]
                        if not chunk:
                            continue
                        session.execute(insert(self._table), chunk)
                        inserted_rows += len(chunk)
                        insert_statements += 1

            return GeneratedInfrastructureWriteResult(
                run_id=run_id,
                infrastructure_type_code=infrastructure_type.code,
                working_srid=working_srid,
                deleted_rows=deleted_rows,
                inserted_rows=inserted_rows,
                insert_statements=insert_statements,
                host_building_ref_count=len(host_buildings),
            )
        except GeneratedInfrastructurePersistenceError:
            raise
        except SQLAlchemyError as exc:
            raise GeneratedInfrastructurePersistenceError(
                "unable to persist generated infrastructure for "
                f"run {run_id} and type {infrastructure_type.code!r}"
            ) from exc

    @staticmethod
    def _load_run_context(
        session: Session,
        run_id: uuid.UUID,
    ) -> tuple[str, int]:
        row = session.execute(
            select(GenerationRun.status, GenerationRun.working_srid)
            .where(GenerationRun.id == run_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            raise GeneratedInfrastructurePersistenceError(
                f"generation run not found: {run_id}"
            )
        return str(row.status), int(row.working_srid)

    @staticmethod
    def _validate_alignment(
        *,
        working_srid: int,
        state: InfrastructureGreedyPlacementState,
        candidate_geometry: InfrastructureCandidateGeometryResult,
        candidate_snaps: InfrastructureNetworkSnapBatchResult,
        infrastructure_type: InfrastructureType,
    ) -> tuple[
        dict[tuple[str, str], InfrastructureCandidateGeometry],
        dict[tuple[str, str], InfrastructureCandidateSnap],
    ]:
        type_code = infrastructure_type.code
        if state.infrastructure_type_code != type_code:
            raise GeneratedInfrastructurePersistenceError(
                "placement state infrastructure type must match InfrastructureType"
            )
        if candidate_geometry.infrastructure_type_code != type_code:
            raise GeneratedInfrastructurePersistenceError(
                "candidate geometry infrastructure type must match InfrastructureType"
            )
        if candidate_geometry.working_srid != working_srid:
            raise GeneratedInfrastructurePersistenceError(
                "candidate geometry working_srid must match generation run"
            )
        if candidate_snaps.working_srid != working_srid:
            raise GeneratedInfrastructurePersistenceError(
                "candidate snap working_srid must match generation run"
            )
        if candidate_snaps.snapshot_id != state.snapshot_id:
            raise GeneratedInfrastructurePersistenceError(
                "candidate snap snapshot_id must match placement state"
            )

        expected_keys = tuple(item.key for item in state.candidate_order)
        geometry_by_key = {
            (item.candidate_id, item.infrastructure_type_code): item
            for item in candidate_geometry.candidates
        }
        if tuple(sorted(geometry_by_key)) != expected_keys:
            raise GeneratedInfrastructurePersistenceError(
                "candidate geometry must cover exactly placement candidate_order"
            )

        snapped: dict[tuple[str, str], InfrastructureCandidateSnap] = {}
        for item in candidate_snaps.snapped:
            if not isinstance(item, InfrastructureCandidateSnap):
                continue
            snapped[item.ref.key] = item

        unsnapped = {
            item.ref.key
            for item in candidate_snaps.unsnapped
            if isinstance(item, InfrastructureCandidateUnsnapped)
        }
        accepted_keys = tuple(
            item.candidate_ref.key for item in state.accepted_facilities
        )
        for key in accepted_keys:
            if key in unsnapped:
                raise GeneratedInfrastructurePersistenceError(
                    "accepted infrastructure candidate must have a successful "
                    f"network snap: {key!r}"
                )
            if key not in snapped:
                raise GeneratedInfrastructurePersistenceError(
                    "accepted infrastructure candidate snap is missing: "
                    f"{key!r}"
                )

        return geometry_by_key, snapped

    @staticmethod
    def _resolve_host_buildings(
        session: Session,
        *,
        run_id: uuid.UUID,
        state: InfrastructureGreedyPlacementState,
        geometry_by_key: dict[
            tuple[str, str],
            InfrastructureCandidateGeometry,
        ],
    ) -> dict[str, uuid.UUID]:
        host_keys: set[str] = set()
        for accepted in state.accepted_facilities:
            candidate = geometry_by_key[accepted.candidate_ref.key]
            if candidate.kind is not InfrastructureCandidateGeometryKind.HOST_BUILDING:
                continue
            assert candidate.host_building_id is not None
            host_keys.add(candidate.host_building_id)

        if not host_keys:
            return {}

        rows = session.execute(
            select(
                GeneratedBuilding.building_key,
                GeneratedBuilding.id,
            ).where(
                GeneratedBuilding.run_id == run_id,
                GeneratedBuilding.building_key.in_(tuple(sorted(host_keys))),
            )
        ).all()
        resolved = {
            str(building_key): building_id
            for building_key, building_id in rows
            if building_key is not None
        }
        if set(resolved) != host_keys:
            missing = sorted(host_keys - set(resolved))
            raise GeneratedInfrastructurePersistenceError(
                "host building refs must resolve within the same generation run: "
                f"{missing!r}"
            )
        return resolved

    @classmethod
    def _rows_from_results(
        cls,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        state: InfrastructureGreedyPlacementState,
        geometry_by_key: dict[
            tuple[str, str],
            InfrastructureCandidateGeometry,
        ],
        snap_by_key: dict[tuple[str, str], InfrastructureCandidateSnap],
        host_buildings: dict[str, uuid.UUID],
        infrastructure_type: InfrastructureType,
        network_snapshot_id: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for accepted in state.accepted_facilities:
            candidate = geometry_by_key[accepted.candidate_ref.key]
            snap = snap_by_key[accepted.candidate_ref.key]

            if candidate.kind is InfrastructureCandidateGeometryKind.SITE:
                assert candidate.site_geometry is not None
                assert candidate.site_area_m2 is not None
                geometry = candidate.site_geometry
                host_building_id = None
                site_area_m2 = candidate.site_area_m2
            else:
                assert candidate.host_building_id is not None
                geometry = candidate.anchor
                host_building_id = host_buildings[candidate.host_building_id]
                site_area_m2 = None

            rows.append(
                {
                    "id": cls._stable_entity_id(
                        run_id,
                        infrastructure_type.code,
                        candidate.candidate_id,
                    ),
                    "run_id": run_id,
                    "geometry": from_shape(geometry, srid=working_srid),
                    "candidate_id": candidate.candidate_id,
                    "infrastructure_type_code": infrastructure_type.code,
                    "category": infrastructure_type.category.value,
                    "capacity": infrastructure_type.capacity,
                    "acceptance_index": accepted.acceptance_index,
                    "geometry_kind": candidate.kind.value,
                    "host_building_id": host_building_id,
                    "site_area_m2": site_area_m2,
                    "network_snapshot_id": network_snapshot_id,
                    "network_node_id": snap.node.node_id,
                    "network_snap_distance_m": snap.distance_m,
                    "attributes_json": {
                        "source_kind": candidate.source_kind.value,
                        "source_id": candidate.source_id,
                        "zone_class": candidate.zone_class.value,
                        "zone_id": candidate.zone_id,
                        "block_id": candidate.block_id,
                        "infrastructure_type_version": infrastructure_type.version,
                        "infrastructure_type_fingerprint": (
                            infrastructure_type.fingerprint
                        ),
                    },
                }
            )
        return rows

    @staticmethod
    def _stable_entity_id(
        run_id: uuid.UUID,
        infrastructure_type_code: str,
        candidate_id: str,
    ) -> uuid.UUID:
        return uuid.uuid5(
            run_id,
            "generated-infrastructure:"
            f"{infrastructure_type_code}:{candidate_id}",
        )

    @staticmethod
    def _require_positive_int(field_name: str, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{field_name} must be a positive integer")
