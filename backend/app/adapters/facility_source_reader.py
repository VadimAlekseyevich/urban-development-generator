"""Read only the actual fixed facilities selected by one generation run."""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable

from geoalchemy2.shape import to_shape
from shapely import get_num_coordinates
from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.adapters.fixed_road_source_reader import (
    FixedRoadSourceReadError,
    _source_version,
)
from backend.app.adapters.pipeline_context import SqlAlchemyPipelineContextAdapter
from backend.app.db.session import SessionLocal
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun, generation_run_dataset_versions
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceFacility
from core.urban_generator.domain import TerritorySnapshot
from core.urban_generator.infrastructure import ExistingFacilitySourceRecord

_MAX_FEATURES = 50_000
_MAX_COORDINATES = 2_000_000


class FacilitySourceReadError(ValueError):
    """Fixed facility source is inconsistent with the persisted snapshot."""


class SqlAlchemyFacilitySourceReader:
    def __init__(
        self, *, session_factory: Callable[[], Session] = SessionLocal,
        max_features: int = _MAX_FEATURES,
    ) -> None:
        if type(max_features) is not int or not 1 <= max_features <= _MAX_FEATURES:
            raise ValueError("max_features outside facility source budget")
        self._session_factory = session_factory
        self._max_features = max_features

    def read(self, *, snapshot: TerritorySnapshot) -> tuple[ExistingFacilitySourceRecord, ...]:
        records: list[ExistingFacilitySourceRecord] = []
        coordinates = 0
        with self._session_factory() as session:
            pair = session.execute(
                select(GenerationRun, Project)
                .join(Project, Project.id == GenerationRun.project_id)
                .where(GenerationRun.id == snapshot.snapshot_id)
            ).one_or_none()
            if pair is None:
                raise FacilitySourceReadError("facility snapshot run does not exist")
            run, project = pair
            srid = snapshot.settings.working_srid
            if (
                run.project_id != snapshot.project.project_id
                or run.working_srid != srid or project.working_srid != srid
                or project.boundary is None or project.boundary.srid != srid
                or SqlAlchemyPipelineContextAdapter._boundary_source_ref(project)
                != snapshot.boundary.source_ref
            ):
                raise FacilitySourceReadError("snapshot project, CRS or boundary differs from run")
            expected = set(session.execute(
                select(DatasetVersion.id, DatasetVersion.version)
                .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                .join(
                    generation_run_dataset_versions,
                    generation_run_dataset_versions.c.dataset_version_id == DatasetVersion.id,
                )
                .where(
                    generation_run_dataset_versions.c.run_id == run.id,
                    Dataset.project_id == project.id,
                    DatasetVersion.status == "ready",
                    (Dataset.kind == "facilities") | (
                        (Dataset.kind == "osm") & select(SourceFacility.id).where(
                            SourceFacility.dataset_version_id == DatasetVersion.id,
                        ).exists()
                    ),
                )
            ).tuples().all())
            selected: dict[uuid.UUID, tuple[int, str]] = {}
            for ref in snapshot.facilities:
                try:
                    version_id, version = _source_version(ref)
                except FixedRoadSourceReadError as exc:
                    raise FacilitySourceReadError("invalid facility version reference") from exc
                if version_id in selected:
                    raise FacilitySourceReadError("duplicate facility version")
                selected[version_id] = (version, ref.source_ref)
            if {(key, value[0]) for key, value in selected.items()} != expected:
                raise FacilitySourceReadError(
                    "facility versions differ from ready run-linked inputs"
                )
            if run.mode == "FROM_SCRATCH" and expected:
                raise FacilitySourceReadError("FROM_SCRATCH must not consume fixed facilities")
            for version_id, (_, source_ref) in sorted(selected.items()):
                wrong_srid = session.scalar(
                    select(SourceFacility.id).where(
                        SourceFacility.dataset_version_id == version_id,
                        func.ST_SRID(SourceFacility.geometry) != srid,
                    ).limit(1)
                )
                if wrong_srid is not None:
                    raise FacilitySourceReadError("source facility has incompatible metric SRID")
                remaining = self._max_features - len(records)
                rows = session.scalars(
                    select(SourceFacility)
                    .join(DatasetVersion, DatasetVersion.id == SourceFacility.dataset_version_id)
                    .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                    .join(Project, Project.id == Dataset.project_id)
                    .where(
                        SourceFacility.dataset_version_id == version_id,
                        Project.id == project.id,
                        func.ST_Intersects(SourceFacility.geometry, Project.boundary),
                    )
                    .order_by(SourceFacility.source_feature_id, SourceFacility.id)
                    .limit(remaining + 1)
                ).all()
                if len(rows) > remaining:
                    raise FacilitySourceReadError("facility source exceeds feature budget")
                for row in rows:
                    geometry = to_shape(row.geometry)
                    if (
                        not isinstance(geometry, (Point, MultiPoint, Polygon, MultiPolygon))
                        or geometry.is_empty or not geometry.is_valid or geometry.has_z
                        or any(not math.isfinite(value) for value in geometry.bounds)
                    ):
                        raise FacilitySourceReadError(f"invalid facility geometry: {row.id}")
                    coordinates += int(get_num_coordinates(geometry))
                    if coordinates > _MAX_COORDINATES:
                        raise FacilitySourceReadError("facility source exceeds coordinate budget")
                    if row.capacity is not None and (
                        not math.isfinite(row.capacity) or row.capacity <= 0
                    ):
                        raise FacilitySourceReadError(
                            f"explicit facility capacity must be positive: {row.id}"
                        )
                    records.append(ExistingFacilitySourceRecord(
                        source_ref=source_ref,
                        source_feature_id=row.source_feature_id or str(row.id),
                        facility_use=row.facility_class,
                        geometry=geometry, working_srid=srid,
                        capacity=row.capacity, name=row.name,
                    ))
        return tuple(sorted(records, key=lambda item: (item.source_ref, item.source_feature_id)))
