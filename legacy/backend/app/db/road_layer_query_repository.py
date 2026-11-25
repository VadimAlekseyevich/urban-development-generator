from __future__ import annotations

import json
import uuid
from typing import cast

from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session

from backend.app.application.road_layers import (
    GeneratedRoadFeature,
    RoadDiagnosticEdge,
    RoadRunContext,
    RoadRunSummary,
)
from backend.app.application.source_layers import GEOJSON_SRID, SourceLayerBbox
from backend.app.models.generated_entity import GeneratedRoad
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


def _text_attribute(attributes: dict[str, object], key: str, default: str = "unknown") -> str:
    value = attributes.get(key)
    return value if isinstance(value, str) and value else default


def _optional_text_attribute(attributes: dict[str, object], key: str) -> str | None:
    value = attributes.get(key)
    return value if isinstance(value, str) and value else None


def _float_attribute(attributes: dict[str, object], key: str) -> float:
    value = attributes.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


class SqlAlchemyRoadLayerQueryRepository:
    """PostGIS adapter for bounded generated-road reads and run diagnostics."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        statement = select(Project.id).where(Project.id == project_id)
        return self._session.scalar(statement) is not None

    def list_runs(self, *, project_id: uuid.UUID) -> list[RoadRunSummary]:
        road_count = (
            select(func.count())
            .select_from(GeneratedRoad)
            .where(GeneratedRoad.run_id == GenerationRun.id)
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
                road_count.label("generated_road_count"),
            )
            .where(GenerationRun.project_id == project_id)
            .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
        )
        rows = self._session.execute(statement).mappings().all()
        return [
            RoadRunSummary(
                id=row["id"],
                project_id=row["project_id"],
                status=row["status"],
                mode=row["mode"],
                seed=row["seed"],
                working_srid=row["working_srid"],
                generated_road_count=int(row["generated_road_count"] or 0),
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
    ) -> RoadRunContext | None:
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
        return RoadRunContext(
            project_id=row["project_id"],
            run_id=row["id"],
            working_srid=row["working_srid"],
            status=row["status"],
        )

    def list_generated_roads(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[GeneratedRoadFeature]:
        table = cast(Table, GeneratedRoad.__table__)
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
            select(table.c.id, table.c.attributes_json, geometry_geojson)
            .where(
                table.c.run_id == run_id,
                table.c.geometry.op("&&")(working_envelope),
                func.ST_Intersects(table.c.geometry, working_envelope),
            )
            .order_by(table.c.id)
            .limit(limit)
        )
        rows = self._session.execute(statement).mappings().all()
        return [
            GeneratedRoadFeature(
                id=row["id"],
                geometry=_parse_geojson_geometry(row["geometry_geojson"]),
                properties=_attributes(row["attributes_json"]),
            )
            for row in rows
        ]

    def list_diagnostic_edges(self, *, run_id: uuid.UUID) -> list[RoadDiagnosticEdge]:
        statement = (
            select(GeneratedRoad.attributes_json)
            .where(GeneratedRoad.run_id == run_id)
            .order_by(GeneratedRoad.id)
        )
        attributes_rows = self._session.scalars(statement).all()
        edges: list[RoadDiagnosticEdge] = []
        for raw_attributes in attributes_rows:
            attributes = _attributes(raw_attributes)
            edges.append(
                RoadDiagnosticEdge(
                    road_id=_text_attribute(attributes, "road_id", ""),
                    source_node_id=_optional_text_attribute(attributes, "source_node_id"),
                    target_node_id=_optional_text_attribute(attributes, "target_node_id"),
                    length_m=_float_attribute(attributes, "length_m"),
                    road_class=_text_attribute(attributes, "road_class"),
                    origin=_text_attribute(attributes, "origin"),
                )
            )
        return edges
