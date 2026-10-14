from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from copy import deepcopy
from typing import Protocol, cast

from geoalchemy2.elements import WKBElement, WKTElement
from geoalchemy2.shape import to_shape
from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import (
    GenerationRun,
    generation_run_dataset_versions,
)
from backend.app.models.project import Project
from backend.app.models.source_layer import (
    SourceBuilding,
    SourceConstraint,
    SourceFacility,
    SourceLanduse,
    SourceRoad,
    SourceWater,
)
from core.urban_generator.domain import (
    ArtifactStore,
    ConfigRef,
    CorrelationMetadata,
    NetworkBackend,
    PipelineContext,
    PipelinePorts,
    ProjectRef,
    ProjectSettings,
    ResolvedConfigBinding,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)


class PipelineContextAssemblyError(ValueError):
    """Raised when persisted run state cannot be assembled into core context."""


class PersistedStageConfigResolver(Protocol):
    """Decode persisted run config JSON into typed canonical stage configs."""

    def resolve(
        self,
        *,
        config_json: Mapping[str, object],
        schema_version: str,
        source: ConfigRef,
    ) -> tuple[ResolvedConfigBinding, ...]:
        """Return typed per-stage config bindings for one persisted run."""


_DATASET_KIND_TO_SNAPSHOT_KIND: dict[str, SnapshotLayerKind] = {
    "roads": SnapshotLayerKind.ROADS,
    "buildings": SnapshotLayerKind.BUILDINGS,
    "facilities": SnapshotLayerKind.FACILITIES,
    "landuse": SnapshotLayerKind.LANDUSE,
    "zones": SnapshotLayerKind.ZONES,
    "water": SnapshotLayerKind.WATER,
    "constraints": SnapshotLayerKind.CONSTRAINTS,
    "dem": SnapshotLayerKind.DEM,
    "demography": SnapshotLayerKind.DEMOGRAPHY,
}


# A single ready OSM DatasetVersion may own several canonical feature tables.
# Probe the indexed version/id pair; never invent layers from source metadata.
_OSM_LAYER_TABLES: tuple[tuple[SnapshotLayerKind, Table], ...] = (
    (SnapshotLayerKind.ROADS, cast(Table, SourceRoad.__table__)),
    (SnapshotLayerKind.BUILDINGS, cast(Table, SourceBuilding.__table__)),
    (SnapshotLayerKind.FACILITIES, cast(Table, SourceFacility.__table__)),
    (SnapshotLayerKind.LANDUSE, cast(Table, SourceLanduse.__table__)),
    (SnapshotLayerKind.WATER, cast(Table, SourceWater.__table__)),
    (SnapshotLayerKind.CONSTRAINTS, cast(Table, SourceConstraint.__table__)),
)


class SqlAlchemyPipelineContextAdapter:
    """Read persisted run inputs and return infrastructure-neutral core context."""

    def __init__(
        self,
        *,
        session: Session,
        artifact_store: ArtifactStore,
        config_resolver: PersistedStageConfigResolver,
        network_backend: NetworkBackend | None = None,
    ) -> None:
        self._session = session
        self._artifact_store = artifact_store
        self._config_resolver = config_resolver
        self._network_backend = network_backend

    def assemble(
        self,
        run_id: uuid.UUID,
        *,
        correlation: CorrelationMetadata | None = None,
    ) -> PipelineContext:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")

        run, project = self._load_run_and_project(run_id)
        self._validate_run_project(run, project)

        config_ref = ConfigRef(
            name="generation",
            ref=(
                f"generation-run:{run.id}:config:"
                f"{run.config_schema_version}"
            ),
        )
        run_context = RunContext(
            run_id=run.id,
            mode=self._run_mode(run.mode),
            seed=run.seed,
            working_srid=run.working_srid,
            config_refs=(config_ref,),
            correlation=correlation
            or CorrelationMetadata(
                correlation_id=f"generation-run:{run.id}"
            ),
        )

        snapshot = self._build_snapshot(run, project)
        configs = self._config_resolver.resolve(
            config_json=deepcopy(run.config_json),
            schema_version=run.config_schema_version,
            source=config_ref,
        )

        return PipelineContext(
            run=run_context,
            snapshot=snapshot,
            configs=configs,
            ports=PipelinePorts(
                artifact_store=self._artifact_store,
                network_backend=self._network_backend,
            ),
        )

    def _load_run_and_project(
        self,
        run_id: uuid.UUID,
    ) -> tuple[GenerationRun, Project]:
        row = self._session.execute(
            select(GenerationRun, Project)
            .join(Project, Project.id == GenerationRun.project_id)
            .where(GenerationRun.id == run_id)
        ).one_or_none()
        if row is None:
            raise PipelineContextAssemblyError(
                f"generation run does not exist: {run_id}"
            )
        return row[0], row[1]

    @staticmethod
    def _validate_run_project(
        run: GenerationRun,
        project: Project,
    ) -> None:
        if run.project_id != project.id:
            raise PipelineContextAssemblyError(
                "generation run project identity is inconsistent"
            )
        if run.working_srid != project.working_srid:
            raise PipelineContextAssemblyError(
                "generation run working_srid does not match project working_srid"
            )
        if project.boundary is None:
            raise PipelineContextAssemblyError(
                "project boundary is required to assemble TerritorySnapshot"
            )
        boundary_srid = int(project.boundary.srid)
        if boundary_srid != run.working_srid:
            raise PipelineContextAssemblyError(
                "project boundary SRID does not match generation run working_srid"
            )

    def _build_snapshot(
        self,
        run: GenerationRun,
        project: Project,
    ) -> TerritorySnapshot:
        layers: dict[SnapshotLayerKind, list[SnapshotLayerRef]] = {
            kind: []
            for kind in SnapshotLayerKind
            if kind is not SnapshotLayerKind.BOUNDARY
        }

        rows = self._session.execute(
            select(
                DatasetVersion.id,
                DatasetVersion.status,
                DatasetVersion.version,
                Dataset.kind,
                Dataset.project_id,
            )
            .join(
                generation_run_dataset_versions,
                generation_run_dataset_versions.c.dataset_version_id
                == DatasetVersion.id,
            )
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .where(generation_run_dataset_versions.c.run_id == run.id)
            .order_by(
                Dataset.kind,
                DatasetVersion.dataset_id,
                DatasetVersion.version,
                DatasetVersion.id,
            )
        ).all()

        for version_id, status, version, dataset_kind, project_id in rows:
            if project_id != run.project_id:
                raise PipelineContextAssemblyError(
                    "generation run references a dataset from another project"
                )
            if status != "ready":
                raise PipelineContextAssemblyError(
                    f"dataset version is not ready: {version_id}"
                )
            if dataset_kind == "osm":
                # A real PBF import stores roads, buildings and other source
                # families in one version. Preserve typed snapshot ownership
                # by looking at the actual canonical tables for that version.
                kinds = self._osm_snapshot_kinds(version_id)
                if not kinds:
                    raise PipelineContextAssemblyError(
                        f"ready OSM dataset version has no canonical source rows: {version_id}"
                    )
            else:
                snapshot_kind = _DATASET_KIND_TO_SNAPSHOT_KIND.get(dataset_kind)
                if snapshot_kind is None:
                    raise PipelineContextAssemblyError(
                        f"unsupported Dataset.kind for TerritorySnapshot: {dataset_kind}"
                    )
                kinds = (snapshot_kind,)
            for snapshot_kind in kinds:
                layers[snapshot_kind].append(
                    SnapshotLayerRef(
                        kind=snapshot_kind,
                        source_ref=f"dataset-version:{version_id}:v{version}",
                    )
                )

        return TerritorySnapshot(
            snapshot_id=run.id,
            project=ProjectRef(project_id=project.id),
            settings=ProjectSettings(working_srid=run.working_srid),
            boundary=SnapshotLayerRef(
                kind=SnapshotLayerKind.BOUNDARY,
                source_ref=self._boundary_source_ref(project),
            ),
            roads=tuple(layers[SnapshotLayerKind.ROADS]),
            buildings=tuple(layers[SnapshotLayerKind.BUILDINGS]),
            facilities=tuple(layers[SnapshotLayerKind.FACILITIES]),
            landuse=tuple(layers[SnapshotLayerKind.LANDUSE]),
            fixed_zones=tuple(layers[SnapshotLayerKind.ZONES]),
            water=tuple(layers[SnapshotLayerKind.WATER]),
            constraints=tuple(layers[SnapshotLayerKind.CONSTRAINTS]),
            dem=tuple(layers[SnapshotLayerKind.DEM]),
            demography=tuple(layers[SnapshotLayerKind.DEMOGRAPHY]),
        )

    def _osm_snapshot_kinds(self, version_id: uuid.UUID) -> tuple[SnapshotLayerKind, ...]:
        # Each table has a (dataset_version_id, id) keyset index. These six
        # LIMIT 1 probes are independent of the number of city features.
        return tuple(
            kind
            for kind, table in _OSM_LAYER_TABLES
            if self._session.scalar(
                select(table.c.id)
                .where(table.c.dataset_version_id == version_id)
                .limit(1)
            )
            is not None
        )

    @staticmethod
    def _run_mode(value: str) -> RunMode:
        try:
            return RunMode(value)
        except ValueError as exc:
            raise PipelineContextAssemblyError(
                f"unsupported generation run mode: {value}"
            ) from exc

    @staticmethod
    def _boundary_source_ref(project: Project) -> str:
        boundary = project.boundary
        if not isinstance(boundary, (WKBElement, WKTElement)):
            raise PipelineContextAssemblyError(
                "project boundary must be a GeoAlchemy geometry element"
            )
        payload = to_shape(boundary).wkb
        digest = hashlib.sha256(payload).hexdigest()
        return f"project-boundary:{project.id}:sha256:{digest}"
