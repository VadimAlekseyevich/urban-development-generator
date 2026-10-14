"""Bounded, project- and run-scoped source-road hydration for canonical RoadStage.

This is a source read adapter, not a second pipeline or a full GenerationInputResolver.
It consumes only immutable snapshot identities and returns typed fixed road inputs.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from geoalchemy2.shape import to_shape
from shapely.geometry import LineString, MultiLineString
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import (
    GenerationRun,
    generation_run_dataset_versions,
)
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceRoad
from core.urban_generator.domain import SnapshotLayerRef, TerritorySnapshot
from core.urban_generator.roads import SemanticRoad
from core.urban_generator.stages import FixedRoadStageInput

DEFAULT_MAX_FIXED_ROADS = 5_000
MAX_FIXED_ROADS = 50_000


class FixedRoadSourceReadError(ValueError):
    """A run cannot safely resolve its fixed road inputs from persisted sources."""


def _source_version(ref: SnapshotLayerRef) -> tuple[uuid.UUID, int]:
    parts = ref.source_ref.split(":")
    if len(parts) != 3 or parts[0] != "dataset-version" or not parts[2].startswith("v"):
        raise FixedRoadSourceReadError(
            f"road layer has unsupported source reference: {ref.source_ref!r}"
        )
    try:
        version_id = uuid.UUID(parts[1])
        version_number = int(parts[2][1:])
    except (ValueError, TypeError) as exc:
        raise FixedRoadSourceReadError("road layer has invalid dataset-version identity") from exc
    if (
        str(version_id) != parts[1]
        or version_number <= 0
        or str(version_number) != parts[2][1:]
    ):
        raise FixedRoadSourceReadError("road layer identity must use canonical UUID/version")
    return version_id, version_number


class SqlAlchemyFixedRoadSourceReader:
    """Hydrate road semantics without reprojecting, truncating or mutating source rows.

    The run must reference a ready, same-project roads/OSM dataset version.
    Spatial filtering is limited to the already-stored project boundary.
    A strict LIMIT(max_roads+1) detects oversized input instead of silently
    sampling a city; exact source geometry is preserved for downstream noding.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        max_roads: int = DEFAULT_MAX_FIXED_ROADS,
    ) -> None:
        if (
            isinstance(max_roads, bool)
            or not isinstance(max_roads, int)
            or not 1 <= max_roads <= MAX_FIXED_ROADS
        ):
            raise ValueError(f"max_roads must be an integer in 1..{MAX_FIXED_ROADS}")
        self._session_factory = session_factory
        self._max_roads = max_roads

    def read(self, *, snapshot: TerritorySnapshot) -> tuple[FixedRoadStageInput, ...]:
        if not isinstance(snapshot, TerritorySnapshot):
            raise TypeError("snapshot must be a TerritorySnapshot")
        if not snapshot.roads:
            return ()

        hydrated: list[FixedRoadStageInput] = []
        seen_versions: set[uuid.UUID] = set()
        with self._session_factory() as session:
            for ref in snapshot.roads:
                version_id, version_number = _source_version(ref)
                if version_id in seen_versions:
                    raise FixedRoadSourceReadError(
                        f"duplicate road dataset-version reference: {version_id}"
                    )
                seen_versions.add(version_id)
                authorized = session.scalar(
                    select(DatasetVersion.id)
                    .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                    .join(
                        generation_run_dataset_versions,
                        generation_run_dataset_versions.c.dataset_version_id
                        == DatasetVersion.id,
                    )
                    .join(
                        GenerationRun,
                        GenerationRun.id == generation_run_dataset_versions.c.run_id,
                    )
                    .join(Project, Project.id == Dataset.project_id)
                    .where(
                        DatasetVersion.id == version_id,
                        DatasetVersion.version == version_number,
                        DatasetVersion.status == "ready",
                        Dataset.kind.in_(("roads", "osm")),
                        GenerationRun.id == snapshot.snapshot_id,
                        GenerationRun.project_id == snapshot.project.project_id,
                        GenerationRun.working_srid == snapshot.settings.working_srid,
                        Project.id == snapshot.project.project_id,
                        Project.working_srid == snapshot.settings.working_srid,
                        Project.boundary.is_not(None),
                    )
                )
                if authorized is None:
                    raise FixedRoadSourceReadError(
                        "road dataset version is missing, not ready, not linked to "
                        f"run or belongs to another project: {version_id}"
                    )

                remaining = self._max_roads - len(hydrated)
                rows = session.scalars(
                    select(SourceRoad)
                    .join(
                        DatasetVersion,
                        DatasetVersion.id == SourceRoad.dataset_version_id,
                    )
                    .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                    .join(Project, Project.id == Dataset.project_id)
                    .where(
                        SourceRoad.dataset_version_id == version_id,
                        func.ST_Intersects(SourceRoad.geometry, Project.boundary),
                    )
                    .order_by(
                        SourceRoad.source_feature_id.asc().nulls_last(),
                        SourceRoad.id,
                    )
                    .limit(remaining + 1)
                ).all()
                if len(rows) > remaining:
                    raise FixedRoadSourceReadError(
                        f"fixed roads exceed configured limit {self._max_roads}"
                    )

                for row in rows:
                    if row.geometry.srid != snapshot.settings.working_srid:
                        raise FixedRoadSourceReadError(
                            f"source road {row.id} has incompatible metric SRID"
                        )
                    geometry = to_shape(row.geometry)
                    if (
                        not isinstance(geometry, (LineString, MultiLineString))
                        or geometry.is_empty
                        or not geometry.is_valid
                        or geometry.length <= 0
                    ):
                        raise FixedRoadSourceReadError(
                            f"source road {row.id} has invalid line geometry"
                        )
                    hydrated.append(
                        FixedRoadStageInput(
                            road=SemanticRoad(
                                road_id=f"fixed:{version_id}:{row.source_feature_id or row.id}",
                                geometry=geometry,
                                layer=row.layer,
                                bridge=row.bridge,
                                tunnel=row.tunnel,
                            ),
                            existing_class=row.road_class,
                        )
                    )
        return tuple(hydrated)
