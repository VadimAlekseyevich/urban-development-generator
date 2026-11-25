"""Run-scoped PostGIS landuse and fixed building input for existing core algorithms."""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import numpy as np
from geoalchemy2.shape import to_shape
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from shapely import get_num_coordinates
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.strtree import STRtree
from sqlalchemy import Table, func, select
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
from backend.app.models.source_layer import SourceBuilding, SourceLanduse
from core.urban_generator.buildings import PlacedBuildingFootprint
from core.urban_generator.domain import SnapshotLayerRef, TerritorySnapshot
from core.urban_generator.suitability import LanduseWindow, SuitabilityGridSpec

_MAX_FEATURES = 50_000
_MAX_COORDINATES = 2_000_000
_MAX_GRID_CELLS = 25_000_000
_MAX_WINDOW_CELLS = 1_100_000


class UrbanPolygonSourceError(ValueError):
    """Persisted source polygons cannot safely supply the selected generation run."""


@dataclass(frozen=True, slots=True)
class _SourcePolygon:
    row_id: uuid.UUID
    category: str
    geometry: Polygon | MultiPolygon


class VectorLanduseSource:
    """Rasterize cell centres in bounded windows; conflicting classes remain NoData."""

    def __init__(
        self, *, grid: SuitabilityGridSpec, polygons: tuple[_SourcePolygon, ...],
    ) -> None:
        self.grid = grid
        self._polygons = polygons
        self._classes = tuple(sorted({item.category for item in polygons}))
        self._codes = {value: index + 1 for index, value in enumerate(self._classes)}
        self._index = STRtree([item.geometry for item in polygons])

    def read_window(
        self, *, window: LanduseWindow,
    ) -> tuple[tuple[str | None, ...], ...]:
        if (
            window.row_off + window.height > self.grid.height
            or window.col_off + window.width > self.grid.width
            or window.cell_count > _MAX_WINDOW_CELLS
        ):
            raise UrbanPolygonSourceError("landuse window outside grid or exceeds budget")
        left, _, _, top = self.grid.bounds
        x0 = left + window.col_off * self.grid.cell_width_m
        x1 = x0 + window.width * self.grid.cell_width_m
        y1 = top - window.row_off * self.grid.cell_height_m
        y0 = y1 - window.height * self.grid.cell_height_m
        bounds = (x0, y0, x1, y1)
        indices = sorted(int(i) for i in self._index.query(box(*bounds)))
        if not indices:
            return tuple((None,) * window.width for _ in range(window.height))
        shapes = [
            (self._polygons[i].geometry, self._codes[self._polygons[i].category])
            for i in indices
        ]
        # With a common cell-centre rule, first and last class alone can miss
        # A/B/A overlaps. Sorting by class makes min/max detect every conflict.
        shapes.sort(key=lambda item: item[1])
        transform = from_bounds(*bounds, window.width, window.height)
        low = rasterize(
            list(reversed(shapes)), out_shape=(window.height, window.width),
            transform=transform, fill=0, all_touched=False, dtype="int32",
        )
        high = rasterize(
            shapes, out_shape=(window.height, window.width),
            transform=transform, fill=0, all_touched=False, dtype="int32",
        )
        codes = np.where(low == high, low, 0)
        return tuple(
            tuple(self._classes[int(code) - 1] if code else None for code in row)
            for row in codes
        )


class SqlAlchemyUrbanPolygonSourceReader:
    """Resolve only ready run-linked source polygons, without clipping or mutation."""

    def __init__(
        self, *, session_factory: Callable[[], Session] = SessionLocal,
        max_features: int = _MAX_FEATURES,
    ) -> None:
        if type(max_features) is not int or not 1 <= max_features <= _MAX_FEATURES:
            raise ValueError("max_features outside polygon source budget")
        self._session_factory = session_factory
        self._max_features = max_features

    def read_landuse(
        self, *, snapshot: TerritorySnapshot, grid: SuitabilityGridSpec,
    ) -> VectorLanduseSource:
        if grid.working_srid != snapshot.settings.working_srid:
            raise UrbanPolygonSourceError("landuse grid working SRID mismatch")
        if grid.cell_count > _MAX_GRID_CELLS:
            raise UrbanPolygonSourceError("landuse grid exceeds cell budget")
        if not snapshot.landuse:
            raise UrbanPolygonSourceError("landuse factor requires selected source versions")
        polygons = self._read(snapshot, kind="landuse", refs=snapshot.landuse)
        return VectorLanduseSource(grid=grid, polygons=polygons)

    def read_buildings(
        self, *, snapshot: TerritorySnapshot,
    ) -> tuple[PlacedBuildingFootprint, ...]:
        polygons = self._read(snapshot, kind="buildings", refs=snapshot.buildings)
        return tuple(
            PlacedBuildingFootprint(
                building_id=f"fixed:{item.row_id}",
                geometry=item.geometry, working_srid=snapshot.settings.working_srid,
            )
            for item in polygons
        )

    def _read(
        self, snapshot: TerritorySnapshot, *, kind: str,
        refs: tuple[SnapshotLayerRef, ...],
    ) -> tuple[_SourcePolygon, ...]:
        if kind == "landuse":
            table = cast(Table, SourceLanduse.__table__)
            category = table.c.landuse_class
        else:
            table = cast(Table, SourceBuilding.__table__)
            category = table.c.building_class
        polygons: list[_SourcePolygon] = []
        coordinates = 0
        seen: set[uuid.UUID] = set()
        with self._session_factory() as session:
            row = session.execute(
                select(GenerationRun, Project)
                .join(Project, Project.id == GenerationRun.project_id)
                .where(GenerationRun.id == snapshot.snapshot_id)
            ).one_or_none()
            if row is None:
                raise UrbanPolygonSourceError("source snapshot run does not exist")
            run, project = row
            if (
                run.project_id != snapshot.project.project_id
                or run.working_srid != snapshot.settings.working_srid
                or project.working_srid != snapshot.settings.working_srid
                or project.boundary is None
                or project.boundary.srid != snapshot.settings.working_srid
                or SqlAlchemyPipelineContextAdapter._boundary_source_ref(project)
                != snapshot.boundary.source_ref
            ):
                raise UrbanPolygonSourceError("snapshot project, CRS or boundary differs from run")
            expected = set(session.execute(
                select(DatasetVersion.id, DatasetVersion.version)
                .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                .join(
                    generation_run_dataset_versions,
                    generation_run_dataset_versions.c.dataset_version_id == DatasetVersion.id,
                )
                .where(
                    generation_run_dataset_versions.c.run_id == run.id,
                    Dataset.project_id == run.project_id,
                    DatasetVersion.status == "ready",
                    (Dataset.kind == kind) | (
                        (Dataset.kind == "osm") & select(table.c.id).where(
                            table.c.dataset_version_id == DatasetVersion.id
                        ).exists()
                    ),
                )
            ).tuples().all())
            if kind == "buildings" and run.mode == "FROM_SCRATCH" and (refs or expected):
                raise UrbanPolygonSourceError("FROM_SCRATCH must not consume fixed buildings")
            selected: set[tuple[uuid.UUID, int]] = set()
            for ref in refs:
                try:
                    version_id, version_number = _source_version(ref)
                except FixedRoadSourceReadError as exc:
                    raise UrbanPolygonSourceError(
                        "invalid polygon dataset version reference"
                    ) from exc
                if version_id in seen:
                    raise UrbanPolygonSourceError("duplicate polygon dataset version")
                seen.add(version_id)
                selected.add((version_id, version_number))
            if selected != expected:
                raise UrbanPolygonSourceError(
                    "polygon versions differ from ready run-linked inputs"
                )
            for version_id, _ in sorted(selected):
                wrong = session.scalar(
                    select(table.c.id).where(
                        table.c.dataset_version_id == version_id,
                        func.ST_SRID(table.c.geometry) != run.working_srid,
                    ).limit(1)
                )
                if wrong is not None:
                    raise UrbanPolygonSourceError("source polygon has incompatible metric SRID")
                remaining = self._max_features - len(polygons)
                rows = session.execute(
                    select(table.c.id, category, table.c.geometry)
                    .join(DatasetVersion, DatasetVersion.id == table.c.dataset_version_id)
                    .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                    .join(Project, Project.id == Dataset.project_id)
                    .where(
                        table.c.dataset_version_id == version_id,
                        Project.id == project.id,
                        func.ST_Intersects(table.c.geometry, Project.boundary),
                    )
                    .order_by(table.c.id)
                    .limit(remaining + 1)
                ).all()
                if len(rows) > remaining:
                    raise UrbanPolygonSourceError("polygon source exceeds feature budget")
                for record_id, source_class, element in rows:
                    geometry = to_shape(element)
                    if (
                        not isinstance(geometry, (Polygon, MultiPolygon))
                        or geometry.is_empty or not geometry.is_valid or geometry.area <= 0
                        or not math.isfinite(geometry.area)
                        or any(not math.isfinite(v) for v in geometry.bounds)
                    ):
                        raise UrbanPolygonSourceError(f"invalid source polygon: {record_id}")
                    coordinates += int(get_num_coordinates(geometry))
                    if coordinates > _MAX_COORDINATES:
                        raise UrbanPolygonSourceError("polygon source exceeds coordinate budget")
                    if (
                        not isinstance(source_class, str) or not source_class
                        or source_class != source_class.strip()
                        or any(ord(char) < 32 or ord(char) == 127 for char in source_class)
                    ):
                        raise UrbanPolygonSourceError(f"invalid source class: {record_id}")
                    polygons.append(_SourcePolygon(record_id, source_class, geometry))
        return tuple(polygons)
