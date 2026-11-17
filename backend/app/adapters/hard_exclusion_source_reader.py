"""Bounded run-scoped PostGIS source geometry for canonical hard-exclusion input.

No synthetic constraints, duplicate stage implementation, implicit reprojection
or direct worker orchestration: read the exact ready dataset versions linked to
the GenerationRun, and return existing core HardExclusion* typed objects.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from geoalchemy2.elements import WKBElement, WKTElement
from geoalchemy2.shape import to_shape
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session

from backend.app.adapters.fixed_road_source_reader import (
    FixedRoadSourceReadError,
    _source_version,
)
from backend.app.adapters.pipeline_context import SqlAlchemyPipelineContextAdapter
from backend.app.db.session import SessionLocal
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import (
    GenerationRun,
    generation_run_dataset_versions,
)
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceConstraint, SourceWater
from core.urban_generator.domain import (
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)
from core.urban_generator.suitability import (
    HardExclusionBoundary,
    HardExclusionGeometryLayer,
)

DEFAULT_MAX_EXCLUSION_GEOMETRIES = 10_000
MAX_EXCLUSION_GEOMETRIES = 100_000
_SOURCE_TABLES: tuple[
    tuple[SnapshotLayerKind, Table, str],
    ...,
] = (
    (
        SnapshotLayerKind.WATER,
        cast(Table, SourceWater.__table__),
        "water",
    ),
    (
        SnapshotLayerKind.CONSTRAINTS,
        cast(Table, SourceConstraint.__table__),
        "constraints",
    ),
)


class HardExclusionSourceReadError(ValueError):
    """A fixed source cannot be used safely as the run's exclusion input."""


@dataclass(frozen=True, slots=True)
class HardExclusionSources:
    """Exactly the core types accepted by ConstraintMaskStageInput."""

    boundary: HardExclusionBoundary
    geometry_layers: tuple[HardExclusionGeometryLayer, ...]
    source_feature_count: int


class SqlAlchemyHardExclusionSourceReader:
    """Read only run-linked ready water/territory-HARD features.

    The SQL spatial predicate and (limit+1) operate on indexed,
    version-scoped PostGIS rows. A matched source geometry is preserved
    exactly in its working CRS; the canonical mask stage intersects it with
    the project boundary during rasterization. Soft or non-TERRITORY
    constraints are not classified as blanket development exclusions.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        max_geometries: int = DEFAULT_MAX_EXCLUSION_GEOMETRIES,
    ) -> None:
        if (
            isinstance(max_geometries, bool)
            or not isinstance(max_geometries, int)
            or not 1 <= max_geometries <= MAX_EXCLUSION_GEOMETRIES
        ):
            raise ValueError(
                f"max_geometries must be in 1..{MAX_EXCLUSION_GEOMETRIES}"
            )
        self._session_factory = session_factory
        self._max_geometries = max_geometries

    def read(self, *, snapshot: TerritorySnapshot) -> HardExclusionSources:
        if not isinstance(snapshot, TerritorySnapshot):
            raise TypeError("snapshot must be TerritorySnapshot")
        with self._session_factory() as session:
            persisted = session.execute(
                select(GenerationRun, Project)
                .join(Project, Project.id == GenerationRun.project_id)
                .where(GenerationRun.id == snapshot.snapshot_id)
            ).one_or_none()
            if persisted is None:
                raise HardExclusionSourceReadError(
                    "generation run does not exist for source snapshot"
                )
            run, project = persisted
            if (
                run.project_id != snapshot.project.project_id
                or project.id != snapshot.project.project_id
                or run.working_srid != snapshot.settings.working_srid
                or project.working_srid != snapshot.settings.working_srid
            ):
                raise HardExclusionSourceReadError(
                    "snapshot project or working SRID differs from persisted run"
                )
            metric_boundary = self._read_boundary(project, snapshot)
            geometry_layers: list[HardExclusionGeometryLayer] = []
            count = 0
            for kind, table, dataset_kind in _SOURCE_TABLES:
                refs = (
                    snapshot.water
                    if kind is SnapshotLayerKind.WATER
                    else snapshot.constraints
                )
                seen: set[uuid.UUID] = set()
                for ref in sorted(refs, key=lambda item: item.source_ref):
                    version_id, version_number = self._validate_reference(ref)
                    if version_id in seen:
                        raise HardExclusionSourceReadError(
                            f"duplicate {dataset_kind} dataset version: {version_id}"
                        )
                    seen.add(version_id)
                    self._require_ready_run_version(
                        session,
                        run=run,
                        version_id=version_id,
                        version_number=version_number,
                        dataset_kind=dataset_kind,
                    )
                    features = self._read_rows(
                        session,
                        project=project,
                        kind=kind,
                        table=table,
                        version_id=version_id,
                        remaining=self._max_geometries - count,
                    )
                    count += len(features)
                    if features:
                        geometry_layers.append(
                            HardExclusionGeometryLayer(
                                code=f"{dataset_kind}.{version_id.hex}",
                                geometries=features,
                                working_srid=snapshot.settings.working_srid,
                            )
                        )
            return HardExclusionSources(
                boundary=metric_boundary,
                geometry_layers=tuple(geometry_layers),
                source_feature_count=count,
            )

    @staticmethod
    def _read_boundary(
        project: Project, snapshot: TerritorySnapshot
    ) -> HardExclusionBoundary:
        if (
            project.boundary is None
            or not isinstance(project.boundary, (WKBElement, WKTElement))
            or project.boundary.srid != snapshot.settings.working_srid
        ):
            raise HardExclusionSourceReadError(
                "run project requires a persisted boundary in its working CRS"
            )
        actual_ref = SqlAlchemyPipelineContextAdapter._boundary_source_ref(project)
        if snapshot.boundary.source_ref != actual_ref:
            raise HardExclusionSourceReadError(
                "project boundary fingerprint differs from run snapshot"
            )
        shape = to_shape(project.boundary)
        if (
            not isinstance(shape, (Polygon, MultiPolygon))
            or shape.is_empty
            or not shape.is_valid
            or shape.area <= 0
            or any(not math.isfinite(coordinate) for coordinate in shape.bounds)
        ):
            raise HardExclusionSourceReadError(
                "project boundary is not a valid metric polygon"
            )
        return HardExclusionBoundary(
            geometry=shape,
            working_srid=snapshot.settings.working_srid,
        )

    @staticmethod
    def _validate_reference(ref: SnapshotLayerRef) -> tuple[uuid.UUID, int]:
        try:
            return _source_version(ref)
        except FixedRoadSourceReadError as exc:
            raise HardExclusionSourceReadError(str(exc)) from exc

    @staticmethod
    def _require_ready_run_version(
        session: Session,
        *,
        run: GenerationRun,
        version_id: uuid.UUID,
        version_number: int,
        dataset_kind: str,
    ) -> None:
        authorized = session.scalar(
            select(DatasetVersion.id)
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .join(
                generation_run_dataset_versions,
                generation_run_dataset_versions.c.dataset_version_id
                == DatasetVersion.id,
            )
            .where(
                generation_run_dataset_versions.c.run_id == run.id,
                DatasetVersion.id == version_id,
                DatasetVersion.version == version_number,
                DatasetVersion.status == "ready",
                Dataset.project_id == run.project_id,
                Dataset.kind.in_((dataset_kind, "osm")),
            )
        )
        if authorized is None:
            raise HardExclusionSourceReadError(
                f"{dataset_kind} dataset version is missing, not ready, "
                f"not linked to run, or wrong source kind: {version_id}"
            )

    @staticmethod
    def _read_rows(
        session: Session,
        *,
        project: Project,
        kind: SnapshotLayerKind,
        table: Table,
        version_id: uuid.UUID,
        remaining: int,
    ) -> tuple[Polygon | MultiPolygon | LineString | MultiLineString, ...]:
        # Do not silently omit invalid-SRID features. ST_Intersects on
        # mismatched SRIDs can throw inside PostGIS; fail before that query.
        wrong_srid = session.scalar(
            select(table.c.id)
            .where(
                table.c.dataset_version_id == version_id,
                func.ST_SRID(table.c.geometry) != project.working_srid,
            )
            .limit(1)
        )
        if wrong_srid is not None:
            raise HardExclusionSourceReadError(
                f"{kind.value} source contains geometry with incompatible metric SRID"
            )

        statement = (
            select(table.c.id, table.c.geometry)
            .join(
                DatasetVersion,
                DatasetVersion.id == table.c.dataset_version_id,
            )
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .join(Project, Project.id == Dataset.project_id)
            .where(
                table.c.dataset_version_id == version_id,
                Project.id == project.id,
                func.ST_Intersects(table.c.geometry, Project.boundary),
            )
            .order_by(
                table.c.source_feature_id.asc().nulls_last(), table.c.id,
            )
            .limit(remaining + 1)
        )
        if kind is SnapshotLayerKind.CONSTRAINTS:
            statement = statement.where(
                table.c.severity == "HARD", table.c.scope == "TERRITORY"
            )
        rows = session.execute(statement).all()
        if len(rows) > remaining:
            raise HardExclusionSourceReadError(
                "hard exclusion source geometries exceed configured limit"
            )
        geometries: list[
            Polygon | MultiPolygon | LineString | MultiLineString
        ] = []
        for record_id, element in rows:
            shape = to_shape(element)
            if (
                not isinstance(
                    shape, (Polygon, MultiPolygon, LineString, MultiLineString)
                )
                or shape.is_empty
                or not shape.is_valid
                or any(
                    not math.isfinite(coordinate) for coordinate in shape.bounds
                )
            ):
                raise HardExclusionSourceReadError(
                    f"unsupported or invalid {kind.value} geometry: {record_id}"
                )
            if (
                isinstance(shape, (Polygon, MultiPolygon)) and shape.area <= 0
            ) or (
                isinstance(shape, (LineString, MultiLineString))
                and shape.length <= 0
            ):
                raise HardExclusionSourceReadError(
                    f"degenerate {kind.value} geometry: {record_id}"
                )
            geometries.append(shape)
        return tuple(geometries)
