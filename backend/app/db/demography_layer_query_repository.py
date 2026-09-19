from __future__ import annotations

import json
import math
import uuid
from typing import cast

from sqlalchemy import Table, func, select
from sqlalchemy.orm import Session

from backend.app.application.demography_layers import (
    DemographyAgeGroupMetric,
    DemographyFeature,
    DemographyMetricsSummary,
    DemographyRunContext,
    DemographyRunSummary,
)
from backend.app.application.source_layers import GEOJSON_SRID, SourceLayerBbox
from backend.app.models.generated_entity import GeneratedBlock
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project


def _parse_geojson_geometry(raw_geometry: object) -> dict[str, object]:
    if not isinstance(raw_geometry, str):
        raise ValueError("PostGIS returned a non-text GeoJSON geometry")
    parsed: object = json.loads(raw_geometry)
    if not isinstance(parsed, dict):
        raise ValueError("PostGIS returned an invalid GeoJSON geometry")
    return {str(key): value for key, value in parsed.items()}


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _string(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"demography metric {key} must be a non-empty string")
    return value


def _int(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"demography metric {key} must be a non-negative integer")
    return value


def _float(data: dict[str, object], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"demography metric {key} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"demography metric {key} must be finite and non-negative")
    return number


def _age_groups(data: dict[str, object]) -> tuple[DemographyAgeGroupMetric, ...]:
    raw = data.get("age_groups")
    if not isinstance(raw, list):
        raise ValueError("demography age_groups must be an array")
    groups: list[DemographyAgeGroupMetric] = []
    for item in raw:
        group = _object(item)
        max_age_value = group.get("max_age")
        if max_age_value is not None and (
            isinstance(max_age_value, bool) or not isinstance(max_age_value, int)
        ):
            raise ValueError("demography age-group max_age must be integer or null")
        groups.append(
            DemographyAgeGroupMetric(
                code=_string(group, "code"),
                min_age=_int(group, "min_age"),
                max_age=max_age_value,
                residents=_int(group, "residents"),
                share=_float(group, "share"),
            )
        )
    return tuple(groups)


def _demography_payload(value: object) -> dict[str, object] | None:
    root = _object(value)
    payload = root.get("demography")
    if not isinstance(payload, dict):
        return None
    return {str(key): item for key, item in payload.items()}


class SqlAlchemyDemographyLayerQueryRepository:
    """PostGIS/JSONB adapter for S09-T10 demographic metrics and choropleth reads."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        statement = select(Project.id).where(Project.id == project_id)
        return self._session.scalar(statement) is not None

    def list_runs(self, *, project_id: uuid.UUID) -> list[DemographyRunSummary]:
        statement = (
            select(GenerationRun)
            .where(GenerationRun.project_id == project_id)
            .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
        )
        runs = self._session.scalars(statement).all()
        result: list[DemographyRunSummary] = []
        for run in runs:
            payload = _demography_payload(run.metrics_json)
            if payload is None:
                continue
            result.append(
                DemographyRunSummary(
                    id=run.id,
                    project_id=run.project_id,
                    status=run.status,
                    mode=run.mode,
                    seed=run.seed,
                    working_srid=run.working_srid,
                    block_count=_int(payload, "block_count"),
                    population=_int(payload, "population"),
                    population_density_per_km2=_float(
                        payload,
                        "population_density_per_km2",
                    ),
                    jobs_estimate=_float(payload, "jobs_estimate"),
                    created_at=run.created_at,
                    finished_at=run.finished_at,
                )
            )
        return result

    def get_run_context(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> DemographyRunContext | None:
        statement = select(GenerationRun).where(
            GenerationRun.id == run_id,
            GenerationRun.project_id == project_id,
        )
        run = self._session.scalars(statement).one_or_none()
        if run is None or _demography_payload(run.metrics_json) is None:
            return None
        return DemographyRunContext(
            project_id=run.project_id,
            run_id=run.id,
            working_srid=run.working_srid,
            status=run.status,
        )

    def get_metrics(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> DemographyMetricsSummary | None:
        statement = select(GenerationRun).where(
            GenerationRun.id == run_id,
            GenerationRun.project_id == project_id,
        )
        run = self._session.scalars(statement).one_or_none()
        if run is None:
            return None
        payload = _demography_payload(run.metrics_json)
        if payload is None:
            return None
        return DemographyMetricsSummary(
            project_id=project_id,
            run_id=run_id,
            scenario_version=_string(payload, "scenario_version"),
            scenario_fingerprint=_string(payload, "scenario_fingerprint"),
            employment_config_version=_string(
                payload,
                "employment_config_version",
            ),
            employment_config_fingerprint=_string(
                payload,
                "employment_config_fingerprint",
            ),
            block_count=_int(payload, "block_count"),
            area_m2=_float(payload, "area_m2"),
            population=_int(payload, "population"),
            population_density_per_km2=_float(
                payload,
                "population_density_per_km2",
            ),
            jobs_estimate=_float(payload, "jobs_estimate"),
            age_groups=_age_groups(payload),
        )

    def list_blocks(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[DemographyFeature]:
        table = cast(Table, GeneratedBlock.__table__)
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
                table.c.block_key,
                table.c.zone_id,
                table.c.area_m2,
                table.c.attributes_json,
                geometry_geojson,
            )
            .where(
                table.c.run_id == run_id,
                table.c.attributes_json.op("?")("demography"),
                table.c.geometry.op("&&")(working_envelope),
                func.ST_Intersects(table.c.geometry, working_envelope),
            )
            .order_by(table.c.id)
            .limit(limit)
        )
        rows = self._session.execute(statement).mappings().all()
        result: list[DemographyFeature] = []
        for row in rows:
            attributes = _object(row["attributes_json"])
            payload = attributes.get("demography")
            if not isinstance(payload, dict):
                continue
            demography = {str(key): value for key, value in payload.items()}
            age_groups = _age_groups(demography)
            zone_class = attributes.get("zone_class")
            properties: dict[str, object] = {
                "block_key": row["block_key"],
                "zone_id": (
                    str(row["zone_id"]) if row["zone_id"] is not None else None
                ),
                "zone_class": zone_class,
                "area_m2": row["area_m2"],
                "population": _int(demography, "population"),
                "population_density_per_km2": _float(
                    demography,
                    "population_density_per_km2",
                ),
                "jobs_estimate": _float(demography, "jobs_estimate"),
                "age_groups": [
                    {
                        "code": item.code,
                        "min_age": item.min_age,
                        "max_age": item.max_age,
                        "residents": item.residents,
                        "share": item.share,
                    }
                    for item in age_groups
                ],
            }
            result.append(
                DemographyFeature(
                    id=row["id"],
                    geometry=_parse_geojson_geometry(row["geometry_geojson"]),
                    properties=properties,
                )
            )
        return result
