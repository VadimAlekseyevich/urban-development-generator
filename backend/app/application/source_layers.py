from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol


GEOJSON_SRID = 4326
GEOJSON_CRS = "EPSG:4326"
DEFAULT_SOURCE_LAYER_LIMIT = 1000
MAX_SOURCE_LAYER_LIMIT = 5000


class SourceLayerName(StrEnum):
    ROADS = "roads"
    BUILDINGS = "buildings"
    LANDUSE = "landuse"
    WATER = "water"
    FACILITIES = "facilities"
    CONSTRAINTS = "constraints"


class SourceLayerQueryError(ValueError):
    """Raised when a source-layer viewport query violates the public contract."""


class SourceLayerDatasetVersionNotFoundError(LookupError):
    """Raised when a dataset version does not belong to the requested project."""

    def __init__(self, project_id: uuid.UUID, dataset_version_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.dataset_version_id = dataset_version_id
        super().__init__(
            f"dataset version {dataset_version_id} was not found in project {project_id}"
        )


@dataclass(frozen=True, slots=True)
class SourceLayerBbox:
    """WGS84 viewport bounds used by the GeoJSON source-layer API."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        coordinates = (self.west, self.south, self.east, self.north)
        if not all(math.isfinite(value) for value in coordinates):
            raise SourceLayerQueryError("bbox coordinates must be finite")
        if not (-180.0 <= self.west < self.east <= 180.0):
            raise SourceLayerQueryError(
                "bbox longitude must satisfy -180 <= west < east <= 180"
            )
        if not (-90.0 <= self.south < self.north <= 90.0):
            raise SourceLayerQueryError(
                "bbox latitude must satisfy -90 <= south < north <= 90"
            )

    @classmethod
    def parse(cls, value: str) -> SourceLayerBbox:
        if not isinstance(value, str):
            raise SourceLayerQueryError("bbox must be a comma-separated string")
        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 4 or any(not part for part in parts):
            raise SourceLayerQueryError(
                "bbox must contain west,south,east,north in EPSG:4326"
            )
        try:
            values = [float(part) for part in parts]
        except ValueError as exc:
            raise SourceLayerQueryError("bbox coordinates must be numbers") from exc
        return cls(values[0], values[1], values[2], values[3])

    @property
    def tuple(self) -> tuple[float, float, float, float]:
        return (self.west, self.south, self.east, self.north)


@dataclass(frozen=True, slots=True)
class SourceLayerContext:
    project_id: uuid.UUID
    dataset_version_id: uuid.UUID
    working_srid: int


@dataclass(frozen=True, slots=True)
class SourceLayerFeature:
    id: uuid.UUID
    geometry: dict[str, object]
    properties: dict[str, object]
    type: Literal["Feature"] = field(init=False, default="Feature")


@dataclass(frozen=True, slots=True)
class SourceLayerQueryResult:
    project_id: uuid.UUID
    dataset_version_id: uuid.UUID
    layer: SourceLayerName
    query_bbox: tuple[float, float, float, float]
    geojson_crs: Literal["EPSG:4326"]
    working_srid: int
    limit: int
    truncated: bool
    features: tuple[SourceLayerFeature, ...]
    type: Literal["FeatureCollection"] = field(init=False, default="FeatureCollection")


class SourceLayerQueryRepository(Protocol):
    """Read-only persistence port for viewport-scoped canonical source features."""

    def get_context(
        self,
        *,
        project_id: uuid.UUID,
        dataset_version_id: uuid.UUID,
    ) -> SourceLayerContext | None:
        ...

    def list_features(
        self,
        *,
        dataset_version_id: uuid.UUID,
        layer: SourceLayerName,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[SourceLayerFeature]:
        ...


class SourceLayerQueryService:
    """Validate viewport requests and keep HTTP independent from spatial SQL."""

    def __init__(self, repository: SourceLayerQueryRepository) -> None:
        self._repository = repository

    def get_geojson(
        self,
        *,
        project_id: uuid.UUID,
        dataset_version_id: uuid.UUID,
        layer: SourceLayerName,
        bbox_text: str,
        limit: int = DEFAULT_SOURCE_LAYER_LIMIT,
    ) -> SourceLayerQueryResult:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise SourceLayerQueryError("limit must be an integer")
        if limit < 1 or limit > MAX_SOURCE_LAYER_LIMIT:
            raise SourceLayerQueryError(
                f"limit must be between 1 and {MAX_SOURCE_LAYER_LIMIT}"
            )

        bbox = SourceLayerBbox.parse(bbox_text)
        context = self._repository.get_context(
            project_id=project_id,
            dataset_version_id=dataset_version_id,
        )
        if context is None:
            raise SourceLayerDatasetVersionNotFoundError(
                project_id, dataset_version_id
            )

        features = self._repository.list_features(
            dataset_version_id=dataset_version_id,
            layer=layer,
            working_srid=context.working_srid,
            bbox=bbox,
            limit=limit + 1,
        )
        truncated = len(features) > limit
        visible_features = tuple(features[:limit])
        return SourceLayerQueryResult(
            project_id=project_id,
            dataset_version_id=dataset_version_id,
            layer=layer,
            query_bbox=bbox.tuple,
            geojson_crs=GEOJSON_CRS,
            working_srid=context.working_srid,
            limit=limit,
            truncated=truncated,
            features=visible_features,
        )
