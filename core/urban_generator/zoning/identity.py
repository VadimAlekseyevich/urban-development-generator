from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.zoning.assignment import ZoneAssignmentResult
from core.urban_generator.zoning.config import ZoneClass
from core.urban_generator.zoning.partition import ZoningPartitionResult


class GeneratedZoneIdentityError(ValueError):
    """Raised when a generated-zone identity cannot be derived deterministically."""


@dataclass(frozen=True, slots=True)
class GeneratedZoneRef:
    """Stable run-scoped generated-zone identity shared by downstream core stages."""

    zone_id: str
    cell_index: int
    seed_index: int
    zone_class: ZoneClass
    geometry: BaseGeometry
    area_m2: float
    working_srid: int

    def __post_init__(self) -> None:
        try:
            uuid.UUID(self.zone_id)
        except (TypeError, ValueError) as exc:
            raise GeneratedZoneIdentityError("zone_id must be a UUID string") from exc
        for field_name in ("cell_index", "seed_index"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise GeneratedZoneIdentityError(
                    f"{field_name} must be a non-negative integer"
                )
        if not isinstance(self.zone_class, ZoneClass):
            raise GeneratedZoneIdentityError("zone_class must be a ZoneClass")
        if not isinstance(self.geometry, (Polygon, MultiPolygon)):
            raise GeneratedZoneIdentityError("geometry must be Polygon or MultiPolygon")
        if self.geometry.is_empty or not self.geometry.is_valid or self.geometry.has_z:
            raise GeneratedZoneIdentityError("geometry must be non-empty, valid and 2D")
        if (
            isinstance(self.area_m2, bool)
            or not isinstance(self.area_m2, (int, float))
            or not math.isfinite(self.area_m2)
            or self.area_m2 <= 0.0
        ):
            raise GeneratedZoneIdentityError("area_m2 must be positive and finite")
        if not math.isclose(
            float(self.geometry.area),
            float(self.area_m2),
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise GeneratedZoneIdentityError("area_m2 must match geometry area")
        require_working_crs(self.working_srid)


def generated_zone_uuid(
    run_id: uuid.UUID,
    *,
    cell_index: int,
    seed_index: int,
) -> uuid.UUID:
    """Return the canonical deterministic DB/core identity for one generated zone."""

    if not isinstance(run_id, uuid.UUID):
        raise GeneratedZoneIdentityError("run_id must be UUID")
    for field_name, value in (("cell_index", cell_index), ("seed_index", seed_index)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GeneratedZoneIdentityError(
                f"{field_name} must be a non-negative integer"
            )
    return uuid.uuid5(
        run_id,
        f"generated-zone:cell:{cell_index}:seed:{seed_index}",
    )


def build_generated_zone_refs(
    *,
    run_id: uuid.UUID,
    partition: ZoningPartitionResult,
    assignment: ZoneAssignmentResult,
) -> tuple[GeneratedZoneRef, ...]:
    """Build stable zone refs while validating partition/assignment alignment."""

    if not isinstance(partition, ZoningPartitionResult):
        raise GeneratedZoneIdentityError("partition must be ZoningPartitionResult")
    if not isinstance(assignment, ZoneAssignmentResult):
        raise GeneratedZoneIdentityError("assignment must be ZoneAssignmentResult")
    if len(partition.cells) != len(assignment.assignments):
        raise GeneratedZoneIdentityError(
            "partition and assignment must contain the same number of zones"
        )

    refs: list[GeneratedZoneRef] = []
    for cell_index, (cell, assigned) in enumerate(
        zip(partition.cells, assignment.assignments, strict=True)
    ):
        if (
            assigned.cell_index != cell_index
            or assigned.seed_index != cell.seed_index
        ):
            raise GeneratedZoneIdentityError(
                "assignment cell/seed references must match partition"
            )
        refs.append(
            GeneratedZoneRef(
                zone_id=str(
                    generated_zone_uuid(
                        run_id,
                        cell_index=cell_index,
                        seed_index=cell.seed_index,
                    )
                ),
                cell_index=cell_index,
                seed_index=cell.seed_index,
                zone_class=assigned.zone_class,
                geometry=cell.geometry,
                area_m2=cell.area_m2,
                working_srid=partition.working_srid,
            )
        )
    return tuple(refs)
