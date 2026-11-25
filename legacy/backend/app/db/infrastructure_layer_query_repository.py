from __future__ import annotations

import json
import uuid
from typing import Any, cast

from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from backend.app.application.infrastructure_layers import (
    InfrastructureAgeCoverageSummary,
    InfrastructureDemandFeature,
    InfrastructureFacilityAccessibilitySummary,
    InfrastructureFeature,
    InfrastructureMetricsSummary,
    InfrastructureOrigin,
    InfrastructureRawMetricSummary,
    InfrastructureRunContext,
    InfrastructureRunSummary,
)
from backend.app.application.source_layers import GEOJSON_SRID, SourceLayerBbox
from backend.app.models.generated_entity import (
    GeneratedBlock,
    GeneratedInfrastructure,
)
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


def _mapping(value: object, *, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return {str(key): item for key, item in value.items()}


def _string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    return float(value)


def _optional_number(value: object, *, field_name: str) -> float | None:
    if value is None:
        return None
    return _number(value, field_name=field_name)


def _integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _optional_integer(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field_name=field_name)


def _optional_string(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name=field_name)


class SqlAlchemyInfrastructureLayerQueryRepository:
    """PostGIS adapter for bounded run-scoped S10 infrastructure read-models."""

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

    def read_model_exists(self, *, run_id: uuid.UUID) -> bool:
        raw_metrics = self._session.scalar(
            select(GenerationRun.metrics_json).where(GenerationRun.id == run_id)
        )
        if not isinstance(raw_metrics, dict):
            return False
        payload = raw_metrics.get("infrastructure")
        return (
            isinstance(payload, dict)
            and isinstance(payload.get("read_model_version"), str)
        )

    def get_metrics(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> InfrastructureMetricsSummary | None:
        row = self._session.execute(
            select(
                GenerationRun.project_id,
                GenerationRun.id,
                GenerationRun.metrics_json,
            ).where(
                GenerationRun.id == run_id,
                GenerationRun.project_id == project_id,
            )
        ).mappings().one_or_none()
        if row is None or not isinstance(row["metrics_json"], dict):
            return None
        payload = row["metrics_json"].get("infrastructure")
        if not isinstance(payload, dict):
            return None
        return self._metrics_summary(
            project_id=row["project_id"],
            run_id=row["id"],
            payload=payload,
        )

    def list_facilities(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
        origin: InfrastructureOrigin | None = None,
    ) -> list[InfrastructureFeature]:
        if origin == "existing":
            return self._list_existing(
                run_id=run_id,
                working_srid=working_srid,
                bbox=bbox,
                limit=limit,
            )
        if origin == "generated":
            return self._list_generated(
                run_id=run_id,
                working_srid=working_srid,
                bbox=bbox,
                limit=limit,
            )

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

    def list_demand_blocks(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[InfrastructureDemandFeature]:
        table = cast(Table, GeneratedBlock.__table__)
        working_envelope = self._working_envelope(bbox, working_srid)
        geometry_geojson = self._geometry_geojson(table)
        statement = (
            select(
                table.c.id,
                table.c.block_key,
                table.c.zone_id,
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
        features: list[InfrastructureDemandFeature] = []
        for row in rows:
            attributes = _attributes(row["attributes_json"])
            payload = attributes.get("infrastructure")
            if not isinstance(payload, dict):
                continue
            properties = {str(key): value for key, value in payload.items()}
            properties["block_key"] = row["block_key"]
            zone_id = row["zone_id"]
            properties["zone_id"] = (
                str(zone_id) if isinstance(zone_id, uuid.UUID) else zone_id
            )
            features.append(
                InfrastructureDemandFeature(
                    id=row["id"],
                    geometry=_parse_geojson_geometry(row["geometry_geojson"]),
                    properties=properties,
                )
            )
        return features

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
    def _metrics_summary(
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        payload: dict[str, object],
    ) -> InfrastructureMetricsSummary:
        raw_metrics_value = payload.get("raw_metrics")
        if not isinstance(raw_metrics_value, list):
            raise ValueError("infrastructure raw_metrics must be an array")
        raw_metrics: list[InfrastructureRawMetricSummary] = []
        for index, raw_item in enumerate(raw_metrics_value):
            item = _mapping(
                raw_item,
                field_name=f"raw_metrics[{index}]",
            )
            age_value = item.get("age_coverage", [])
            if not isinstance(age_value, list):
                raise ValueError("age_coverage must be an array")
            age_coverage = tuple(
                InfrastructureAgeCoverageSummary(
                    demographic_group=_string(
                        age_item["demographic_group"],
                        field_name="demographic_group",
                    ),
                    population=_number(
                        age_item["population"],
                        field_name="population",
                    ),
                    covered_population=_number(
                        age_item["covered_population"],
                        field_name="covered_population",
                    ),
                    coverage_ratio=_number(
                        age_item["coverage_ratio"],
                        field_name="coverage_ratio",
                    ),
                )
                for age_item in (
                    _mapping(value, field_name="age_coverage item")
                    for value in age_value
                )
            )
            raw_metrics.append(
                InfrastructureRawMetricSummary(
                    metric_id=_string(
                        item.get("metric_id"),
                        field_name="metric_id",
                    ),
                    scalar_value=_optional_number(
                        item.get("scalar_value"),
                        field_name="scalar_value",
                    ),
                    age_coverage=age_coverage,
                )
            )

        diagnostics_raw = _mapping(
            payload.get("diagnostics"),
            field_name="diagnostics",
        )
        diagnostics = {
            str(key): _integer(value, field_name=f"diagnostics.{key}")
            for key, value in diagnostics_raw.items()
        }

        facility_payload = _mapping(
            payload.get("facility_accessibility"),
            field_name="facility_accessibility",
        )
        summaries: list[InfrastructureFacilityAccessibilitySummary] = []
        for origin in ("existing", "generated"):
            values = facility_payload.get(origin, [])
            if not isinstance(values, list):
                raise ValueError(
                    f"facility_accessibility.{origin} must be an array"
                )
            for index, raw_summary in enumerate(values):
                item = _mapping(
                    raw_summary,
                    field_name=f"facility_accessibility.{origin}[{index}]",
                )
                summaries.append(
                    InfrastructureFacilityAccessibilitySummary(
                        origin=cast(InfrastructureOrigin, origin),
                        infrastructure_type_code=_string(
                            item.get("infrastructure_type_code"),
                            field_name="infrastructure_type_code",
                        ),
                        max_network_distance_m=_number(
                            item.get("max_network_distance_m"),
                            field_name="max_network_distance_m",
                        ),
                        reachable_demand_count=_integer(
                            item.get("reachable_demand_count"),
                            field_name="reachable_demand_count",
                        ),
                        nearest_distance_m=_optional_number(
                            item.get("nearest_distance_m"),
                            field_name="nearest_distance_m",
                        ),
                        farthest_distance_m=_optional_number(
                            item.get("farthest_distance_m"),
                            field_name="farthest_distance_m",
                        ),
                        facility_id=_optional_string(
                            item.get("facility_id"),
                            field_name="facility_id",
                        ),
                        source_ref=_optional_string(
                            item.get("source_ref"),
                            field_name="source_ref",
                        ),
                        source_feature_id=_optional_string(
                            item.get("source_feature_id"),
                            field_name="source_feature_id",
                        ),
                        candidate_id=_optional_string(
                            item.get("candidate_id"),
                            field_name="candidate_id",
                        ),
                        acceptance_index=_optional_integer(
                            item.get("acceptance_index"),
                            field_name="acceptance_index",
                        ),
                        capacity=_optional_number(
                            item.get("capacity"),
                            field_name="capacity",
                        ),
                        network_snapshot_id=_optional_string(
                            item.get("network_snapshot_id"),
                            field_name="network_snapshot_id",
                        ),
                    )
                )

        return InfrastructureMetricsSummary(
            project_id=project_id,
            run_id=run_id,
            read_model_version=_string(
                payload.get("read_model_version"),
                field_name="read_model_version",
            ),
            scenario_version=_string(
                payload.get("scenario_version"),
                field_name="scenario_version",
            ),
            scenario_fingerprint=_string(
                payload.get("scenario_fingerprint"),
                field_name="scenario_fingerprint",
            ),
            raw_metrics=tuple(raw_metrics),
            diagnostics=diagnostics,
            facility_accessibility=tuple(summaries),
        )

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
