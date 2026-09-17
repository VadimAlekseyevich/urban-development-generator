from __future__ import annotations

import json
import uuid
from typing import cast

from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session

from backend.app.application.block_parcel_layers import BlockParcelFeature, BlockParcelRunContext, BlockParcelRunSummary
from backend.app.application.source_layers import GEOJSON_SRID, SourceLayerBbox
from backend.app.models.generated_entity import GeneratedBlock, GeneratedParcel
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


class SqlAlchemyBlockParcelLayerQueryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        return self._session.scalar(select(Project.id).where(Project.id == project_id)) is not None

    def list_runs(self, *, project_id: uuid.UUID) -> list[BlockParcelRunSummary]:
        block_count = select(func.count()).select_from(GeneratedBlock).where(GeneratedBlock.run_id == GenerationRun.id).correlate(GenerationRun).scalar_subquery()
        parcel_count = select(func.count()).select_from(GeneratedParcel).where(GeneratedParcel.run_id == GenerationRun.id).correlate(GenerationRun).scalar_subquery()
        rows = self._session.execute(
            select(
                GenerationRun.id,
                GenerationRun.project_id,
                GenerationRun.status,
                GenerationRun.mode,
                GenerationRun.seed,
                GenerationRun.working_srid,
                GenerationRun.created_at,
                GenerationRun.finished_at,
                block_count.label("generated_block_count"),
                parcel_count.label("generated_parcel_count"),
            )
            .where(GenerationRun.project_id == project_id)
            .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
        ).mappings().all()
        return [
            BlockParcelRunSummary(
                id=row["id"], project_id=row["project_id"], status=row["status"], mode=row["mode"],
                seed=row["seed"], working_srid=row["working_srid"],
                generated_block_count=int(row["generated_block_count"] or 0),
                generated_parcel_count=int(row["generated_parcel_count"] or 0),
                created_at=row["created_at"], finished_at=row["finished_at"],
            ) for row in rows
        ]

    def get_run_context(self, *, project_id: uuid.UUID, run_id: uuid.UUID) -> BlockParcelRunContext | None:
        row = self._session.execute(
            select(GenerationRun.project_id, GenerationRun.id, GenerationRun.working_srid, GenerationRun.status)
            .where(GenerationRun.id == run_id, GenerationRun.project_id == project_id)
        ).mappings().one_or_none()
        if row is None:
            return None
        return BlockParcelRunContext(project_id=row["project_id"], run_id=row["id"], working_srid=row["working_srid"], status=row["status"])

    def list_blocks(self, *, run_id: uuid.UUID, working_srid: int, bbox: SourceLayerBbox, limit: int) -> list[BlockParcelFeature]:
        return self._list_features(cast(Table, GeneratedBlock.__table__), run_id, working_srid, bbox, limit, ("block_key", "zone_id", "area_m2", "association_status"))

    def list_parcels(self, *, run_id: uuid.UUID, working_srid: int, bbox: SourceLayerBbox, limit: int) -> list[BlockParcelFeature]:
        return self._list_features(cast(Table, GeneratedParcel.__table__), run_id, working_srid, bbox, limit, ("parcel_key", "block_id", "zone_id", "area_m2", "buildable_area_m2", "frontage_m"))

    def _list_features(self, table: Table, run_id: uuid.UUID, working_srid: int, bbox: SourceLayerBbox, limit: int, property_columns: tuple[str, ...]) -> list[BlockParcelFeature]:
        wgs84_envelope = func.ST_MakeEnvelope(bbox.west, bbox.south, bbox.east, bbox.north, GEOJSON_SRID)
        working_envelope = func.ST_Transform(wgs84_envelope, working_srid)
        geometry_geojson = func.ST_AsGeoJSON(func.ST_Transform(table.c.geometry, GEOJSON_SRID), 9).label("geometry_geojson")
        columns = [table.c.id, table.c.attributes_json, geometry_geojson, *(table.c[name] for name in property_columns)]
        rows = self._session.execute(
            select(*columns).where(
                table.c.run_id == run_id,
                table.c.geometry.op("&&")(working_envelope),
                func.ST_Intersects(table.c.geometry, working_envelope),
            ).order_by(table.c.id).limit(limit)
        ).mappings().all()
        result: list[BlockParcelFeature] = []
        for row in rows:
            properties = _attributes(row["attributes_json"])
            for name in property_columns:
                value = row[name]
                properties[name] = str(value) if isinstance(value, uuid.UUID) else value
            result.append(BlockParcelFeature(id=row["id"], geometry=_parse_geojson_geometry(row["geometry_geojson"]), properties=properties))
        return result
