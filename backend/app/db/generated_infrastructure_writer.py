from __future__ import annotations

import math
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
    InfrastructureAcceptedFacility,
    InfrastructureCandidateGeometry,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidateGeometryResult,
    InfrastructureCandidateSnap,
    InfrastructureFeasibilityResult,
    InfrastructureGreedyPlacementState,
    InfrastructureNetworkSnapBatchResult,
    InfrastructureType,
)


class GeneratedInfrastructurePersistenceError(DataError):
    """Base error for generated infrastructure persistence."""


class GeneratedInfrastructureImmutableError(
    GeneratedInfrastructurePersistenceError
):
    """Raised before replacing infrastructure owned by a successful run."""


@dataclass(frozen=True, slots=True)
class GeneratedInfrastructureWriteResult:
    run_id: uuid.UUID
    infrastructure_type_code: str
    working_srid: int
    snapshot_id: str
    deleted_rows: int
    inserted_rows: int
    insert_statements: int
    host_building_ref_count: int


@dataclass(frozen=True, slots=True)
class _AcceptedInfrastructureSubject:
    accepted: InfrastructureAcceptedFacility
    candidate: InfrastructureCandidateGeometry
    feasibility: InfrastructureFeasibilityResult
    snap: InfrastructureCandidateSnap


class SqlAlchemyGeneratedInfrastructureWriter:
    """Atomically replace one run/type's accepted generated facilities.

    The writer composes already authoritative T01/T05/T06/T08/T09 results. It does
    not regenerate geometry, rerun snapping/routing, or recalculate capacity.
    """

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
        infrastructure_type: InfrastructureType,
        candidates: InfrastructureCandidateGeometryResult,
        placement: InfrastructureGreedyPlacementState,
        feasibility_results: tuple[InfrastructureFeasibilityResult, ...],
        network_snaps: InfrastructureNetworkSnapBatchResult,
    ) -> GeneratedInfrastructureWriteResult:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        if not isinstance(infrastructure_type, InfrastructureType):
            raise TypeError("infrastructure_type must be InfrastructureType")
        if not isinstance(candidates, InfrastructureCandidateGeometryResult):
            raise TypeError(
                "candidates must be InfrastructureCandidateGeometryResult"
            )
        if not isinstance(placement, InfrastructureGreedyPlacementState):
            raise TypeError(
                "placement must be InfrastructureGreedyPlacementState"
            )
        if not isinstance(feasibility_results, tuple):
            raise TypeError("feasibility_results must be an immutable tuple")
        if any(
            not isinstance(item, InfrastructureFeasibilityResult)
            for item in feasibility_results
        ):
            raise TypeError(
                "feasibility_results must contain InfrastructureFeasibilityResult values"
            )
        if not isinstance(network_snaps, InfrastructureNetworkSnapBatchResult):
            raise TypeError(
                "network_snaps must be InfrastructureNetworkSnapBatchResult"
            )

        try:
            with self._session_factory() as session:
                with session.begin():
                    status, working_srid = self._load_run_context(
                        session,
                        run_id,
                    )
                    if status == "succeeded":
                        raise GeneratedInfrastructureImmutableError(
                            "generated infrastructure of a successful generation "
                            "run is immutable"
                        )

                    subjects = self._validate_alignment(
                        working_srid=working_srid,
                        infrastructure_type=infrastructure_type,
                        candidates=candidates,
                        placement=placement,
                        feasibility_results=feasibility_results,
                        network_snaps=network_snaps,
                    )
                    host_refs = self._resolve_host_building_refs(
                        session,
                        run_id=run_id,
                        subjects=subjects,
                    )
                    rows = self._rows_from_subjects(
                        run_id=run_id,
                        working_srid=working_srid,
                        infrastructure_type=infrastructure_type,
                        snapshot_id=network_snaps.snapshot_id,
                        subjects=subjects,
                        host_refs=host_refs,
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
                snapshot_id=network_snaps.snapshot_id,
                deleted_rows=deleted_rows,
                inserted_rows=inserted_rows,
                insert_statements=insert_statements,
                host_building_ref_count=len(host_refs),
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

    def _validate_alignment(
        self,
        *,
        working_srid: int,
        infrastructure_type: InfrastructureType,
        candidates: InfrastructureCandidateGeometryResult,
        placement: InfrastructureGreedyPlacementState,
        feasibility_results: tuple[InfrastructureFeasibilityResult, ...],
        network_snaps: InfrastructureNetworkSnapBatchResult,
    ) -> tuple[_AcceptedInfrastructureSubject, ...]:
        type_code = infrastructure_type.code
        if candidates.infrastructure_type_code != type_code:
            raise GeneratedInfrastructurePersistenceError(
                "candidate geometry type must match InfrastructureType code"
            )
        if placement.infrastructure_type_code != type_code:
            raise GeneratedInfrastructurePersistenceError(
                "placement type must match InfrastructureType code"
            )
        if candidates.working_srid != working_srid:
            raise GeneratedInfrastructurePersistenceError(
                "candidate geometry working_srid must match generation run"
            )
        if network_snaps.working_srid != working_srid:
            raise GeneratedInfrastructurePersistenceError(
                "network snap working_srid must match generation run"
            )
        if placement.snapshot_id != network_snaps.snapshot_id:
            raise GeneratedInfrastructurePersistenceError(
                "placement snapshot_id must match network snap snapshot_id"
            )
        if len(placement.accepted_facilities) > self._max_rows:
            raise GeneratedInfrastructurePersistenceError(
                "generated infrastructure row limit exceeded: "
                f"{len(placement.accepted_facilities)} > {self._max_rows}"
            )

        candidate_by_key = {
            (item.candidate_id, item.infrastructure_type_code): item
            for item in candidates.candidates
        }
        if len(candidate_by_key) != len(candidates.candidates):
            raise GeneratedInfrastructurePersistenceError(
                "candidate geometries must have unique candidate/type identities"
            )

        expected_feasibility_keys = tuple(
            (item.candidate_id, item.infrastructure_type_code)
            for item in candidates.candidates
        )
        actual_feasibility_keys = tuple(
            item.key for item in feasibility_results
        )
        if actual_feasibility_keys != expected_feasibility_keys:
            raise GeneratedInfrastructurePersistenceError(
                "feasibility results must exactly match canonical candidate geometry order"
            )
        feasibility_by_key = {
            item.key: item for item in feasibility_results
        }

        candidate_snaps = tuple(
            item
            for item in network_snaps.snapped
            if isinstance(item, InfrastructureCandidateSnap)
            and item.ref.infrastructure_type_code == type_code
        )
        snap_by_key = {item.ref.key: item for item in candidate_snaps}
        if len(snap_by_key) != len(candidate_snaps):
            raise GeneratedInfrastructurePersistenceError(
                "candidate network snaps must have unique candidate/type identities"
            )

        subjects: list[_AcceptedInfrastructureSubject] = []
        for accepted in placement.accepted_facilities:
            key = accepted.candidate_ref.key
            candidate = candidate_by_key.get(key)
            feasibility = feasibility_by_key.get(key)
            snap = snap_by_key.get(key)
            if candidate is None:
                raise GeneratedInfrastructurePersistenceError(
                    "accepted facility references candidate outside T05 geometry result"
                )
            if feasibility is None:
                raise GeneratedInfrastructurePersistenceError(
                    "accepted facility is missing T09 feasibility result"
                )
            if not feasibility.is_feasible:
                raise GeneratedInfrastructurePersistenceError(
                    "accepted facility must have a feasible T09 result"
                )
            if feasibility.geometry_kind is not candidate.kind:
                raise GeneratedInfrastructurePersistenceError(
                    "feasibility geometry kind must match T05 candidate geometry"
                )
            if feasibility.working_srid != working_srid:
                raise GeneratedInfrastructurePersistenceError(
                    "feasibility working_srid must match generation run"
                )
            if not math.isclose(
                feasibility.proposed_capacity,
                infrastructure_type.capacity,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise GeneratedInfrastructurePersistenceError(
                    "accepted facility capacity must match InfrastructureType capacity"
                )
            if snap is None:
                raise GeneratedInfrastructurePersistenceError(
                    "accepted facility requires a successful T06 candidate network snap"
                )
            subjects.append(
                _AcceptedInfrastructureSubject(
                    accepted=accepted,
                    candidate=candidate,
                    feasibility=feasibility,
                    snap=snap,
                )
            )

        return tuple(subjects)

    @staticmethod
    def _resolve_host_building_refs(
        session: Session,
        *,
        run_id: uuid.UUID,
        subjects: tuple[_AcceptedInfrastructureSubject, ...],
    ) -> dict[str, uuid.UUID]:
        host_keys = tuple(
            sorted(
                {
                    subject.candidate.host_building_id
                    for subject in subjects
                    if subject.candidate.kind
                    is InfrastructureCandidateGeometryKind.HOST_BUILDING
                    and subject.candidate.host_building_id is not None
                }
            )
        )
        if not host_keys:
            return {}

        rows = session.execute(
            select(
                GeneratedBuilding.building_key,
                GeneratedBuilding.id,
            ).where(
                GeneratedBuilding.run_id == run_id,
                GeneratedBuilding.building_key.in_(host_keys),
            )
        ).all()
        refs = {
            str(building_key): building_id
            for building_key, building_id in rows
            if building_key is not None
        }
        missing = tuple(key for key in host_keys if key not in refs)
        if missing:
            raise GeneratedInfrastructurePersistenceError(
                "host-building candidates must resolve to generated buildings "
                f"of the same run: {missing!r}"
            )
        return refs

    @classmethod
    def _rows_from_subjects(
        cls,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        infrastructure_type: InfrastructureType,
        snapshot_id: str,
        subjects: tuple[_AcceptedInfrastructureSubject, ...],
        host_refs: dict[str, uuid.UUID],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for subject in subjects:
            candidate = subject.candidate
            host_building_id: uuid.UUID | None = None
            site_area_m2: float | None = None
            if candidate.kind is InfrastructureCandidateGeometryKind.SITE:
                geometry = candidate.site_geometry
                site_area_m2 = candidate.site_area_m2
                if geometry is None or site_area_m2 is None:
                    raise GeneratedInfrastructurePersistenceError(
                        "site candidate requires T05 site geometry and area"
                    )
            else:
                geometry = candidate.anchor
                host_key = candidate.host_building_id
                if host_key is None or host_key not in host_refs:
                    raise GeneratedInfrastructurePersistenceError(
                        "host-building candidate requires resolved generated building"
                    )
                host_building_id = host_refs[host_key]

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
                    "capacity": subject.feasibility.proposed_capacity,
                    "acceptance_index": subject.accepted.acceptance_index,
                    "geometry_kind": candidate.kind.value,
                    "host_building_id": host_building_id,
                    "site_area_m2": site_area_m2,
                    "network_snapshot_id": snapshot_id,
                    "network_node_id": subject.snap.node.node_id,
                    "network_snap_distance_m": subject.snap.distance_m,
                    "attributes_json": {
                        "source_kind": candidate.source_kind.value,
                        "source_id": candidate.source_id,
                        "zone_class": candidate.zone_class.value,
                        "zone_id": candidate.zone_id,
                        "block_id": candidate.block_id,
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
