from __future__ import annotations

import json
import uuid
from typing import Any, cast

from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from backend.app.application.infrastructure_layers import (
    InfrastructureFeature,
    InfrastructureRunContext,
    InfrastructureRunSummary,
)
from backend.app.application.source_layers import GEOJSON_SRID, SourceLayerBbox
from backend.app.models.generated_entity import GeneratedInfrastructure
from backend.app.models.generation_run import (
    GenerationRun,
    generation_run_dataset_versions,
)
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceFacility


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


class SqlAlchemyInfrastructureLayerQueryRepository:
    """PostGIS adapter for bounded run-scoped existing/generated facility reads."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        return self._session.scalar(
            select(Project.id).where(Project.id == project_id)
        ) is not None

    def list_runs(
        self,
        *,
        project_id: uuid.UUID,
    ) -> list[InfrastructureRunSummary]:
        existing_count = (
            select(func.count())
            .select_from(SourceFacility)
            .join(
                generation_run_dataset_versions,
                generation_run_dataset_versions.c.dataset_version_id
                == SourceFacility.dataset_version_id,
            )
            .where(generation_run_dataset_versions.c.run_id == GenerationRun.id)
            .correlate(GenerationRun)
            .scalar_subquery()
        )
        generated_count = (
            select(func.count())
            .select_from(GeneratedInfrastructure)
            .where(GeneratedInfrastructure.run_id == GenerationRun.id)
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
                existing_count.label("existing_facility_count"),
                generated_count.label("generated_facility_count"),
            )
            .where(GenerationRun.project_id == project_id)
            .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
        )
        rows = self._session.execute(statement).mappings().all()
        return [
            InfrastructureRunSummary(
                id=row["id"],
                project_id=row["project_id"],
                status=row["status"],
                mode=row["mode"],
                seed=row["seed"],
                working_srid=row["working_srid"],
                existing_facility_count=int(row["existing_facility_count"] or 0),
                generated_facility_count=int(row["generated_facility_count"] or 0),
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
    ) -> InfrastructureRunContext | None:
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
        return InfrastructureRunContext(
            project_id=row["project_id"],
            run_id=row["id"],
            working_srid=row["working_srid"],
            status=row["status"],
        )

    def list_facilities(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[InfrastructureFeature]:
        existing = self._list_existing(
            run_id=run_id,
            working_srid=working_srid,
            bbox=bbox,
            limit=limit,
        )
        generated = self._list_generated(
            run_id=run_id,
            working_srid=working_srid,
            bbox=bbox,
            limit=limit,
        )
        combined = sorted(
            (*existing, *generated),
            key=lambda item: (0 if item.origin == "existing" else 1, item.id),
        )
        return combined[:limit]

    def _list_existing(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[InfrastructureFeature]:
        table = cast(Table, SourceFacility.__table__)
        working_envelope = self._working_envelope(bbox, working_srid)
        geometry_geojson = self._geometry_geojson(table)
        statement = (
            select(
                table.c.id,
                table.c.dataset_version_id,
                table.c.source_feature_id,
                table.c.facility_class,
                table.c.name,
                table.c.capacity,
                table.c.attributes_json,
                geometry_geojson,
            )
            .join(
                generation_run_dataset_versions,
                generation_run_dataset_versions.c.dataset_version_id
                == table.c.dataset_version_id,
            )
            .where(
                generation_run_dataset_versions.c.run_id == run_id,
                table.c.geometry.op("&&")(working_envelope),
                func.ST_Intersects(table.c.geometry, working_envelope),
            )
            .order_by(table.c.id)
            .limit(limit)
        )
        rows = self._session.execute(statement).mappings().all()
        features: list[InfrastructureFeature] = []
        for row in rows:
            properties = _attributes(row["attributes_json"])
            properties.update(
                {
                    "origin": "existing",
                    "dataset_version_id": str(row["dataset_version_id"]),
                    "source_feature_id": row["source_feature_id"],
                    "facility_class": row["facility_class"],
                    "name": row["name"],
                    "capacity": row["capacity"],
                }
            )
            features.append(
                InfrastructureFeature(
                    id=row["id"],
                    origin="existing",
                    geometry=_parse_geojson_geometry(row["geometry_geojson"]),
                    properties=properties,
                )
            )
        return features

    def _list_generated(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[InfrastructureFeature]:
        table = cast(Table, GeneratedInfrastructure.__table__)
        working_envelope = self._working_envelope(bbox, working_srid)
        geometry_geojson = self._geometry_geojson(table)
        statement = (
            select(
                table.c.id,
                table.c.candidate_id,
                table.c.infrastructure_type_code,
                table.c.category,
                table.c.capacity,
                table.c.acceptance_index,
                table.c.geometry_kind,
                table.c.host_building_id,
                table.c.site_area_m2,
                table.c.network_snapshot_id,
                table.c.network_node_id,
                table.c.network_snap_distance_m,
                table.c.attributes_json,
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
        features: list[InfrastructureFeature] = []
        typed_names = (
            "candidate_id",
            "infrastructure_type_code",
            "category",
            "capacity",
            "acceptance_index",
            "geometry_kind",
            "host_building_id",
            "site_area_m2",
            "network_snapshot_id",
            "network_node_id",
            "network_snap_distance_m",
        )
        for row in rows:
            properties = _attributes(row["attributes_json"])
            properties["origin"] = "generated"
            for name in typed_names:
                value = row[name]
                properties[name] = (
                    str(value) if isinstance(value, uuid.UUID) else value
                )
            features.append(
                InfrastructureFeature(
                    id=row["id"],
                    origin="generated",
                    geometry=_parse_geojson_geometry(row["geometry_geojson"]),
                    properties=properties,
                )
            )
        return features

    @staticmethod
    def _working_envelope(
        bbox: SourceLayerBbox,
        working_srid: int,
    ) -> ColumnElement[Any]:
        wgs84_envelope = func.ST_MakeEnvelope(
            bbox.west,
            bbox.south,
            bbox.east,
            bbox.north,
            GEOJSON_SRID,
        )
        return func.ST_Transform(wgs84_envelope, working_srid)

    @staticmethod
    def _geometry_geojson(table: Table) -> ColumnElement[Any]:
        return func.ST_AsGeoJSON(
            func.ST_Transform(table.c.geometry, GEOJSON_SRID),
            9,
        ).label("geometry_geojson")
