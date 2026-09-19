from __future__ import annotations

import json
import uuid
from typing import cast

from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session

from backend.app.application.building_layers import (
    BuildingFeature,
    BuildingRunContext,
    BuildingRunSummary,
)
from backend.app.application.source_layers import GEOJSON_SRID, SourceLayerBbox
from backend.app.models.generated_entity import GeneratedBuilding
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project


def _parse_geojson_geometry(raw_geometry: object) -> dict[str, object]:
    if not isinstance(raw_geometry, str):
        raise ValueError("PostGIS returned a non-text GeoJSON geometry")
    parsed: object = json.loads(raw_geometry)
    if not isinstance(parsed, dict):
        raise ValueError("PostGIS returned an invalid GeoJSON geometry")
    return {str(key): value for key, value in parsed.items()}


def _attributes(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


class SqlAlchemyBuildingLayerQueryRepository:
    """PostGIS adapter for bounded generated-building viewport reads."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        statement = select(Project.id).where(Project.id == project_id)
        return self._session.scalar(statement) is not None

    def list_runs(self, *, project_id: uuid.UUID) -> list[BuildingRunSummary]:
        building_count = (
            select(func.count())
            .select_from(GeneratedBuilding)
            .where(GeneratedBuilding.run_id == GenerationRun.id)
            .correlate(GenerationRun)
            .scalar_subquery()
        )
        statement = (
            select(
                GenerationRun.id,
                GenerationRun.project_id,
                GenerationRun.status,
                GenerationRun.mode,
                GenerationRun.seed,
                GenerationRun.working_srid,
                GenerationRun.created_at,
                GenerationRun.finished_at,
                building_count.label("generated_building_count"),
            )
            .where(GenerationRun.project_id == project_id)
            .order_by(
                GenerationRun.created_at.desc(),
                GenerationRun.id.desc(),
            )
        )
        rows = self._session.execute(statement).mappings().all()
        return [
            BuildingRunSummary(
                id=row["id"],
                project_id=row["project_id"],
                status=row["status"],
                mode=row["mode"],
                seed=row["seed"],
                working_srid=row["working_srid"],
                generated_building_count=int(
                    row["generated_building_count"] or 0
                ),
                created_at=row["created_at"],
                finished_at=row["finished_at"],
            )
            for row in rows
        ]

    def get_run_context(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> BuildingRunContext | None:
        statement = select(
            GenerationRun.project_id,
            GenerationRun.id,
            GenerationRun.working_srid,
            GenerationRun.status,
        ).where(
            GenerationRun.id == run_id,
            GenerationRun.project_id == project_id,
        )
        row = self._session.execute(statement).mappings().one_or_none()
        if row is None:
            return None
        return BuildingRunContext(
            project_id=row["project_id"],
            run_id=row["id"],
            working_srid=row["working_srid"],
            status=row["status"],
        )

    def list_buildings(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[BuildingFeature]:
        table = cast(Table, GeneratedBuilding.__table__)
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
        statement = (
            select(
                table.c.id,
                table.c.attributes_json,
                table.c.building_key,
                table.c.source_id,
                table.c.block_id,
                table.c.parcel_id,
                table.c.zone_class,
                table.c.archetype,
                table.c.building_use,
                table.c.floors,
                table.c.footprint_area_m2,
                table.c.gfa_m2,
                geometry_geojson,
            )
            .where(
                table.c.run_id == run_id,
                table.c.geometry.op("&&")(working_envelope),
                func.ST_Intersects(table.c.geometry, working_envelope),
            )
            .order_by(table.c.id)
            .limit(limit)
        )
        rows = self._session.execute(statement).mappings().all()

        features: list[BuildingFeature] = []
        property_names = (
            "building_key",
            "source_id",
            "block_id",
            "parcel_id",
            "zone_class",
            "archetype",
            "building_use",
            "floors",
            "footprint_area_m2",
            "gfa_m2",
        )
        for row in rows:
            properties = _attributes(row["attributes_json"])
            for name in property_names:
                value = row[name]
                properties[name] = (
                    str(value) if isinstance(value, uuid.UUID) else value
                )
            features.append(
                BuildingFeature(
                    id=row["id"],
                    geometry=_parse_geojson_geometry(
                        row["geometry_geojson"]
                    ),
                    properties=properties,
                )
            )
        return features
