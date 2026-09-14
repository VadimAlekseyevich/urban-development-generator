from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import cast

from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session

from backend.app.application.source_layers import (
    GEOJSON_CRS,
    GEOJSON_SRID,
    ProjectBoundaryFeature,
    ProjectBoundaryProperties,
    SourceLayerBbox,
    SourceLayerContext,
    SourceLayerFeature,
    SourceLayerName,
)
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.project import Project
from backend.app.models.source_layer import (
    SourceBuilding,
    SourceConstraint,
    SourceFacility,
    SourceLanduse,
    SourceRoad,
    SourceWater,
)


@dataclass(frozen=True, slots=True)
class _LayerSpec:
    table: Table
    property_columns: tuple[str, ...]


_LAYER_SPECS: dict[SourceLayerName, _LayerSpec] = {
    SourceLayerName.ROADS: _LayerSpec(
        table=cast(Table, SourceRoad.__table__),
        property_columns=(
            "road_class",
            "name",
            "lanes",
            "max_speed_kph",
            "one_way",
            "one_way_direction",
            "bridge",
            "tunnel",
            "layer",
        ),
    ),
    SourceLayerName.BUILDINGS: _LayerSpec(
        table=cast(Table, SourceBuilding.__table__),
        property_columns=("building_class", "name", "levels", "height_m"),
    ),
    SourceLayerName.LANDUSE: _LayerSpec(
        table=cast(Table, SourceLanduse.__table__),
        property_columns=("landuse_class",),
    ),
    SourceLayerName.WATER: _LayerSpec(
        table=cast(Table, SourceWater.__table__),
        property_columns=("water_class",),
    ),
    SourceLayerName.FACILITIES: _LayerSpec(
        table=cast(Table, SourceFacility.__table__),
        property_columns=("facility_class", "name", "capacity"),
    ),
    SourceLayerName.CONSTRAINTS: _LayerSpec(
        table=cast(Table, SourceConstraint.__table__),
        property_columns=("constraint_code", "severity", "scope"),
    ),
}


def _parse_geojson_geometry(raw_geometry: object) -> dict[str, object]:
    if not isinstance(raw_geometry, str):
        raise ValueError("PostGIS returned a non-text GeoJSON geometry")
    parsed_geometry: object = json.loads(raw_geometry)
    if not isinstance(parsed_geometry, dict):
        raise ValueError("PostGIS returned an invalid GeoJSON geometry")
    return {str(key): value for key, value in parsed_geometry.items()}


class SqlAlchemySourceLayerQueryRepository:
    """PostGIS adapter for bounded source-layer and project-boundary map reads."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_context(
        self,
        *,
        project_id: uuid.UUID,
        dataset_version_id: uuid.UUID,
    ) -> SourceLayerContext | None:
        statement = (
            select(Project.working_srid)
            .select_from(DatasetVersion)
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .join(Project, Project.id == Dataset.project_id)
            .where(
                DatasetVersion.id == dataset_version_id,
                Project.id == project_id,
            )
        )
        working_srid = self._session.scalar(statement)
        if working_srid is None:
            return None
        return SourceLayerContext(
            project_id=project_id,
            dataset_version_id=dataset_version_id,
            working_srid=working_srid,
        )

    def get_project_boundary(
        self,
        *,
        project_id: uuid.UUID,
    ) -> ProjectBoundaryFeature | None:
        geometry_geojson = func.ST_AsGeoJSON(
            func.ST_Transform(Project.boundary, GEOJSON_SRID),
            9,
        ).label("geometry_geojson")
        statement = select(
            Project.id,
            Project.working_srid,
            geometry_geojson,
        ).where(Project.id == project_id)
        row = self._session.execute(statement).mappings().one_or_none()
        if row is None:
            return None

        raw_geometry = row["geometry_geojson"]
        geometry = None if raw_geometry is None else _parse_geojson_geometry(raw_geometry)
        return ProjectBoundaryFeature(
            id=row["id"],
            geometry=geometry,
            properties=ProjectBoundaryProperties(
                project_id=row["id"],
                working_srid=row["working_srid"],
                geojson_crs=GEOJSON_CRS,
            ),
        )

    def list_features(
        self,
        *,
        dataset_version_id: uuid.UUID,
        layer: SourceLayerName,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[SourceLayerFeature]:
        spec = _LAYER_SPECS[layer]
        table = spec.table
        wgs84_envelope = func.ST_MakeEnvelope(
            bbox.west,
            bbox.south,
            bbox.east,
            bbox.north,
            GEOJSON_SRID,
        )
        working_envelope = func.ST_Transform(wgs84_envelope, working_srid)
        geometry_geojson = func.ST_AsGeoJSON(
            func.ST_Transform(table.c.geometry, GEOJSON_SRID),
            9,
        ).label("geometry_geojson")
        columns = [
            table.c.id,
            table.c.source_feature_id,
            table.c.attributes_json,
            *(table.c[name] for name in spec.property_columns),
            geometry_geojson,
        ]
        statement = (
            select(*columns)
            .where(
                table.c.dataset_version_id == dataset_version_id,
                table.c.geometry.op("&&")(working_envelope),
                func.ST_Intersects(table.c.geometry, working_envelope),
            )
            .order_by(table.c.id)
            .limit(limit)
        )

        rows = self._session.execute(statement).mappings().all()
        features: list[SourceLayerFeature] = []
        for row in rows:
            geometry = _parse_geojson_geometry(row["geometry_geojson"])
            properties: dict[str, object] = {
                name: row[name] for name in spec.property_columns
            }
            properties["source_feature_id"] = row["source_feature_id"]
            attributes = row["attributes_json"]
            properties["attributes"] = (
                dict(attributes) if isinstance(attributes, dict) else {}
            )
            features.append(
                SourceLayerFeature(
                    id=row["id"],
                    geometry=geometry,
                    properties=properties,
                )
            )
        return features
