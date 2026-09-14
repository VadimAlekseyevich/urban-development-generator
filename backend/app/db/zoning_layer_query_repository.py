from __future__ import annotations

import json
import uuid
from typing import cast

from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session

from backend.app.application.source_layers import GEOJSON_SRID, SourceLayerBbox
from backend.app.application.zoning_layers import (
    GeneratedZoneFeature,
    ZoningRunContext,
    ZoningRunSummary,
)
from backend.app.models.generated_entity import GeneratedZone
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project


def _parse_geojson_geometry(raw_geometry: object) -> dict[str, object]:
    if not isinstance(raw_geometry, str):
        raise ValueError("PostGIS returned a non-text GeoJSON geometry")
    parsed: object = json.loads(raw_geometry)
    if not isinstance(parsed, dict):
        raise ValueError("PostGIS returned an invalid GeoJSON geometry")
    return {str(key): value for key, value in parsed.items()}


class SqlAlchemyZoningLayerQueryRepository:
    """PostGIS adapter for run selection and bounded generated-zone map reads."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        return self._session.scalar(select(Project.id).where(Project.id == project_id)) is not None

    def list_runs(self, *, project_id: uuid.UUID) -> list[ZoningRunSummary]:
        zone_count = (
            select(func.count())
            .select_from(GeneratedZone)
            .where(GeneratedZone.run_id == GenerationRun.id)
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
                zone_count.label("zone_count"),
            )
            .where(GenerationRun.project_id == project_id)
            .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
        )
        rows = self._session.execute(statement).mappings().all()
        return [
            ZoningRunSummary(
                id=row["id"],
                project_id=row["project_id"],
                status=row["status"],
                mode=row["mode"],
                seed=row["seed"],
                working_srid=row["working_srid"],
                zone_count=int(row["zone_count"] or 0),
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
    ) -> ZoningRunContext | None:
        row = self._session.execute(
            select(
                GenerationRun.project_id,
                GenerationRun.id,
                GenerationRun.working_srid,
                GenerationRun.status,
            ).where(
                GenerationRun.id == run_id,
                GenerationRun.project_id == project_id,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return ZoningRunContext(
            project_id=row["project_id"],
            run_id=row["id"],
            working_srid=row["working_srid"],
            status=row["status"],
        )

    def list_generated_zones(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[GeneratedZoneFeature]:
        table = cast(Table, GeneratedZone.__table__)
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
                table.c.zone_class,
                table.c.area_m2,
                table.c.attributes_json,
                table.c.diagnostics_json,
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
        features: list[GeneratedZoneFeature] = []
        for row in rows:
            attributes = row["attributes_json"]
            diagnostics = row["diagnostics_json"]
            features.append(
                GeneratedZoneFeature(
                    id=row["id"],
                    geometry=_parse_geojson_geometry(row["geometry_geojson"]),
                    properties={
                        "zone_class": row["zone_class"],
                        "area_m2": float(row["area_m2"]),
                        "attributes": dict(attributes) if isinstance(attributes, dict) else {},
                        "diagnostics": (
                            dict(diagnostics) if isinstance(diagnostics, dict) else {}
                        ),
                    },
                )
            )
        return features
