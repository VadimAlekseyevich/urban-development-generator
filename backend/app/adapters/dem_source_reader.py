"""Read the exact run-owned normalized DEM through the core windowed source port."""

from __future__ import annotations

import hashlib
import math
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.adapters.fixed_road_source_reader import (
    FixedRoadSourceReadError,
    _source_version,
)
from backend.app.adapters.pipeline_context import SqlAlchemyPipelineContextAdapter
from backend.app.db.session import SessionLocal
from backend.app.models.artifact import Artifact
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun, generation_run_dataset_versions
from backend.app.models.project import Project
from core.urban_generator.domain import ArtifactRef, ArtifactStore, TerritorySnapshot
from core.urban_generator.suitability import DEMSlopeSource, DEMSlopeWindow, SuitabilityGridSpec

_MAX_GRID_CELLS = 25_000_000
_MAX_WINDOW_CELLS = 1_100_000
_DEFAULT_MAX_BYTES = 2 * 1024**3
_CHUNK_BYTES = 1024**2


class DEMSourceReadError(ValueError):
    """A normalized DEM cannot safely supply this generation run."""


class _AlignedDEMSource:
    def __init__(self, grid: SuitabilityGridSpec, vrt: WarpedVRT) -> None:
        self.grid = grid
        self._vrt = vrt

    def read_window(
        self, *, window: DEMSlopeWindow,
    ) -> tuple[tuple[float | int | None, ...], ...]:
        if (
            window.row_off + window.height > self.grid.height
            or window.col_off + window.width > self.grid.width
            or window.cell_count > _MAX_WINDOW_CELLS
        ):
            raise DEMSourceReadError("DEM window outside grid or exceeds read budget")
        data = self._vrt.read(
            1, window=Window(window.col_off, window.row_off, window.width, window.height),
            masked=True,
        )
        values = np.asarray(data.data, dtype=np.float64)
        valid = ~np.ma.getmaskarray(data) & np.isfinite(values)
        return tuple(
            tuple(float(value) if ok else None for value, ok in zip(row, mask, strict=True))
            for row, mask in zip(values, valid, strict=True)
        )


class SqlAlchemyDEMSourceReader:
    """Authorize, checksum and align one ready normalized DEM, without mutating inputs.

    The yielded source is usable only within the context manager. No mosaic or
    implicit version selection: the run must link exactly one DEM version.
    """

    def __init__(
        self, *, artifact_store: ArtifactStore,
        session_factory: Callable[[], Session] = SessionLocal,
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> None:
        if type(max_bytes) is not int or not 1 <= max_bytes <= _DEFAULT_MAX_BYTES:
            raise ValueError("max_bytes outside DEM scratch budget")
        self._store = artifact_store
        self._session_factory = session_factory
        self._max_bytes = max_bytes

    @contextmanager
    def open(
        self, *, snapshot: TerritorySnapshot, grid: SuitabilityGridSpec,
    ) -> Iterator[DEMSlopeSource]:
        if (
            grid.working_srid != snapshot.settings.working_srid
            or grid.cell_count > _MAX_GRID_CELLS
        ):
            raise DEMSourceReadError("DEM grid CRS or cell budget mismatch")
        ref, size, checksum = self._resolve(snapshot)
        if not 0 < size <= self._max_bytes:
            raise DEMSourceReadError("DEM artifact exceeds scratch budget or is empty")
        with tempfile.TemporaryDirectory(prefix="urban-dem-") as scratch:
            path = Path(scratch) / "normalized.tif"
            self._materialize(ref, path, size, checksum)
            with rasterio.open(path) as source:
                if (
                    source.driver != "GTiff" or source.count != 1
                    or source.crs is None or source.crs.to_epsg() != grid.working_srid
                    or np.dtype(source.dtypes[0]).kind not in "iuf"
                ):
                    raise DEMSourceReadError("expected single-band normalized metric GeoTIFF")
                if source.scales[0] != 1 or source.offsets[0] != 0:
                    raise DEMSourceReadError("DEM must store unscaled elevations in metres")
                unit = source.units[0]
                if unit and unit.strip().lower() not in {"m", "metre", "meter", "metres", "meters"}:
                    raise DEMSourceReadError("DEM elevation unit must be metres")
                if any(not math.isfinite(v) for v in source.bounds):
                    raise DEMSourceReadError("DEM bounds must be finite")
                left, bottom, right, top = grid.bounds
                if (
                    source.bounds.right <= left or source.bounds.left >= right
                    or source.bounds.top <= bottom or source.bounds.bottom >= top
                ):
                    raise DEMSourceReadError("DEM has no overlap with target grid")
                with WarpedVRT(
                    source, crs=source.crs,
                    transform=from_bounds(*grid.bounds, grid.width, grid.height),
                    width=grid.width, height=grid.height, dtype="float64",
                    nodata=float("nan"), resampling=Resampling.bilinear,
                    init_dest_nodata=True, warp_mem_limit=64,
                ) as vrt:
                    yield _AlignedDEMSource(grid, vrt)

    def _resolve(self, snapshot: TerritorySnapshot) -> tuple[ArtifactRef, int, str]:
        if len(snapshot.dem) != 1:
            raise DEMSourceReadError("run must select exactly one DEM version")
        try:
            version_id, version_number = _source_version(snapshot.dem[0])
        except FixedRoadSourceReadError as exc:
            raise DEMSourceReadError("invalid DEM version reference") from exc
        ref = ArtifactRef(key=f"datasets/{version_id}/normalized/raster.tif").as_ready()
        with self._session_factory() as session:
            row = session.execute(
                select(GenerationRun, Project)
                .join(Project, Project.id == GenerationRun.project_id)
                .where(GenerationRun.id == snapshot.snapshot_id)
            ).one_or_none()
            if row is None:
                raise DEMSourceReadError("DEM snapshot run does not exist")
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
                raise DEMSourceReadError("DEM snapshot project, CRS or boundary differs from run")
            versions = session.execute(
                select(DatasetVersion.id, DatasetVersion.version, DatasetVersion.status)
                .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                .join(
                    generation_run_dataset_versions,
                    generation_run_dataset_versions.c.dataset_version_id == DatasetVersion.id,
                )
                .where(
                    generation_run_dataset_versions.c.run_id == run.id,
                    Dataset.project_id == run.project_id,
                    Dataset.kind == "dem",
                )
            ).all()
            if len(versions) != 1 or tuple(versions[0]) != (version_id, version_number, "ready"):
                raise DEMSourceReadError("DEM version is not the exact ready run-linked version")
            artifact = session.scalar(
                select(Artifact).where(
                    Artifact.uri == f"artifact://{ref.key}",
                    Artifact.state == "referenced",
                    Artifact.owner_type == "dataset_version",
                    Artifact.owner_id == version_id,
                )
            )
            if artifact is None:
                raise DEMSourceReadError("normalized DEM artifact is not owned by this version")
            return ref, artifact.size_bytes, artifact.checksum

    def _materialize(self, ref: ArtifactRef, path: Path, size: int, checksum: str) -> None:
        try:
            stat = self._store.stat(ref)
            if stat.size_bytes != size or stat.checksum != checksum:
                raise DEMSourceReadError("DEM storage metadata differs from database")
            digest = hashlib.sha256()
            observed = 0
            with self._store.open(ref) as source, path.open("wb") as target:
                while chunk := source.read(_CHUNK_BYTES):
                    observed += len(chunk)
                    if observed > size:
                        raise DEMSourceReadError("DEM stream exceeds declared size")
                    digest.update(chunk)
                    target.write(chunk)
        except (KeyError, OSError) as exc:
            raise DEMSourceReadError("unable to read normalized DEM artifact") from exc
        if observed != size or f"sha256:{digest.hexdigest()}" != checksum:
            raise DEMSourceReadError("DEM stream size or checksum differs from database")
