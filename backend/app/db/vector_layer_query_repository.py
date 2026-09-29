"""PostGIS adapter for the canonical 15 table-backed LayerCatalog viewports.

Every selectable table and typed property column is a literal entry in the
server registry. The user supplies only a validated canonical layer ID,
project-scoped owner UUIDs and bounded query values. All spatial filtering
uses original working-CRS geometry and existing GiST indexes; optional
simplification changes *only* serialized geometry, never persistence or
membership in the result set.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import cast

from sqlalchemy import Table, case, func, select
from sqlalchemy.orm import Session

from backend.app.application.layer_catalog import LayerCatalogEntry, LayerOwnerScope
from backend.app.application.source_layers import GEOJSON_SRID, SourceLayerBbox
from backend.app.application.vector_layers import VectorFeature, VectorLayerContext
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generated_entity import (
    GeneratedBlock,
    GeneratedBuilding,
    GeneratedInfrastructure,
    GeneratedParcel,
    GeneratedRoad,
    GeneratedZone,
)
from backend.app.models.generation_run import (
    GenerationRun,
    generation_run_dataset_versions,
)
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
class _VectorTableSpec:
    table: Table
    columns: tuple[str, ...] = ()
    source: bool = False
    derived_json_key: str | None = None


def _spec(
    model: type[object],
    *columns: str,
    source: bool = False,
    derived_json_key: str | None = None,
) -> _VectorTableSpec:
    return _VectorTableSpec(
        table=cast(Table, model.__table__),  # type: ignore[attr-defined]
        columns=columns,
        source=source,
        derived_json_key=derived_json_key,
    )


# Static allowlist: no client-derived SQL identifier or arbitrary table access.
_VECTOR_TABLES: dict[str, _VectorTableSpec] = {
    "source.landuse": _spec(SourceLanduse, "landuse_class", source=True),
    "source.water": _spec(SourceWater, "water_class", source=True),
    "source.constraints": _spec(
        SourceConstraint, "constraint_code", "severity", "scope", source=True,
    ),
    "source.facilities": _spec(
        SourceFacility, "facility_class", "name", "capacity", source=True,
    ),
    "source.buildings": _spec(
        SourceBuilding, "building_class", "name", "levels", "height_m", source=True,
    ),
    "source.roads": _spec(
        SourceRoad, "road_class", "name", "lanes", "max_speed_kph",
        "one_way", "one_way_direction", "bridge", "tunnel", "layer", source=True,
    ),
    "generated.zones": _spec(
        GeneratedZone, "zone_class", "area_m2", "diagnostics_json",
    ),
    "generated.demography": _spec(
        GeneratedBlock, "block_key", "zone_id", "area_m2",
        derived_json_key="demography",
    ),
    "generated.blocks": _spec(
        GeneratedBlock, "block_key", "zone_id", "area_m2", "association_status",
    ),
    "generated.parcels": _spec(
        GeneratedParcel, "parcel_key", "block_id", "zone_id", "area_m2",
        "buildable_area_m2", "frontage_m",
    ),
    "generated.buildings": _spec(
        GeneratedBuilding, "building_key", "source_id", "block_id", "parcel_id",
        "zone_class", "archetype", "building_use", "floors",
        "footprint_area_m2", "gfa_m2",
    ),
    "generated.roads": _spec(GeneratedRoad),
    "run.existing_facilities": _spec(
        SourceFacility, "facility_class", "name", "capacity", source=True,
    ),
    "generated.facilities": _spec(
        GeneratedInfrastructure, "candidate_id", "infrastructure_type_code",
        "category", "capacity", "acceptance_index", "geometry_kind",
        "host_building_id", "site_area_m2", "network_snapshot_id",
        "network_node_id", "network_snap_distance_m",
    ),
    "generated.infrastructure_demand": _spec(
        GeneratedBlock, "block_key", "zone_id",
        derived_json_key="infrastructure",
    ),
}


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _property_value(value: object) -> object:
    return str(value) if isinstance(value, uuid.UUID) else value


def _feature_properties(
    entry: LayerCatalogEntry,
    spec: _VectorTableSpec,
    row: dict[str, object],
) -> dict[str, object]:
    attributes = _object(row["attributes_json"])
    if spec.source:
        props: dict[str, object] = {
            name: _property_value(row[name]) for name in spec.columns
        }
        props["source_feature_id"] = row["source_feature_id"]
        props["attributes"] = attributes
        if entry.definition.layer_id == "run.existing_facilities":
            props["origin"] = "existing"
            props["dataset_version_id"] = str(row["dataset_version_id"])
        return props

    if spec.derived_json_key is not None:
        payload = _object(attributes.get(spec.derived_json_key))
        props = dict(payload)
        # Same feature identity and block metadata as the existing derived
        # viewport, with no recalculation of canonical demographic/demand data.
        props.update({name: _property_value(row[name]) for name in spec.columns})
        if spec.derived_json_key == "demography":
            props["zone_class"] = attributes.get("zone_class")
        return props

    if entry.definition.layer_id == "generated.zones":
        props = {name: _property_value(row[name]) for name in spec.columns}
        props["attributes"] = attributes
        props["diagnostics"] = _object(props.pop("diagnostics_json"))
        return props

    props = dict(attributes)
    props.update({name: _property_value(row[name]) for name in spec.columns})
    if entry.definition.layer_id == "generated.facilities":
        props["origin"] = "generated"
    return props


class SqlAlchemyVectorLayerRepository:
    """Project-authorized, keyset-ordered read model for table-backed layers."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_context(self, entry: LayerCatalogEntry) -> VectorLayerContext | None:
        owner = entry.owner
        if owner.scope is LayerOwnerScope.DATASET_VERSION:
            statement = (
                select(Project.working_srid)
                .select_from(DatasetVersion)
                .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
                .join(Project, Project.id == Dataset.project_id)
                .where(
                    Project.id == owner.project_id,
                    DatasetVersion.id == owner.dataset_version_id,
                )
            )
            srid = self._session.scalar(statement)
            return VectorLayerContext(working_srid=srid) if srid is not None else None
        if owner.scope is not LayerOwnerScope.RUN:
            return None
        row = self._session.execute(
            select(GenerationRun.working_srid, GenerationRun.metrics_json).where(
                GenerationRun.id == owner.run_id,
                GenerationRun.project_id == owner.project_id,
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        metrics = _object(row["metrics_json"])
        layer_id = entry.definition.layer_id
        ready = True
        if layer_id == "generated.demography":
            ready = isinstance(metrics.get("demography"), dict)
        elif layer_id == "generated.infrastructure_demand":
            payload = metrics.get("infrastructure")
            ready = (
                isinstance(payload, dict)
                and isinstance(payload.get("read_model_version"), str)
            )
        return VectorLayerContext(
            working_srid=row["working_srid"],
            read_model_ready=ready,
        )

    def list_features(
        self,
        *,
        entry: LayerCatalogEntry,
        working_srid: int,
        bbox: SourceLayerBbox,
        after: uuid.UUID | None,
        limit: int,
        simplify_m: float,
    ) -> list[VectorFeature]:
        spec = _VECTOR_TABLES[entry.definition.layer_id]
        table = spec.table
        owner = entry.owner
        working_bbox = func.ST_Transform(
            func.ST_MakeEnvelope(
                bbox.west, bbox.south, bbox.east, bbox.north, GEOJSON_SRID,
            ),
            working_srid,
        )
        # Filtering must use original geometry. Never simplify the predicate,
        # change source data or apply a tolerance in WGS84 degrees.
        original = table.c.geometry
        drawn = original
        if simplify_m > 0:
            simplified = func.ST_SimplifyPreserveTopology(original, simplify_m)
            drawn = case((func.ST_IsEmpty(simplified), original), else_=simplified)
        geojson = func.ST_AsGeoJSON(
            func.ST_Transform(drawn, GEOJSON_SRID), 9,
        ).label("geometry_geojson")
        columns = [
            table.c.id,
            table.c.attributes_json,
            *(table.c[name] for name in spec.columns),
        ]
        if spec.source:
            columns.append(table.c.source_feature_id)
        if entry.definition.layer_id == "run.existing_facilities":
            columns.append(table.c.dataset_version_id)
        columns.append(geojson)

        statement = select(*columns)
        if owner.scope is LayerOwnerScope.DATASET_VERSION:
            statement = statement.where(
                table.c.dataset_version_id == owner.dataset_version_id,
            )
        elif entry.definition.layer_id == "run.existing_facilities":
            statement = statement.join(
                generation_run_dataset_versions,
                generation_run_dataset_versions.c.dataset_version_id
                == table.c.dataset_version_id,
            ).where(generation_run_dataset_versions.c.run_id == owner.run_id)
        else:
            statement = statement.where(table.c.run_id == owner.run_id)
        statement = statement.where(
            original.op("&&")(working_bbox),
            func.ST_Intersects(original, working_bbox),
        )
        if spec.derived_json_key is not None:
            statement = statement.where(
                func.jsonb_typeof(
                    table.c.attributes_json.op("->")(spec.derived_json_key),
                ) == "object"
            )
        if after is not None:
            statement = statement.where(table.c.id > after)
        statement = statement.order_by(table.c.id).limit(limit)
        rows = self._session.execute(statement).mappings().all()
        result: list[VectorFeature] = []
        for row in rows:
            raw_geometry = row["geometry_geojson"]
            if not isinstance(raw_geometry, str):
                raise ValueError("PostGIS returned non-text GeoJSON geometry")
            geometry: object = json.loads(raw_geometry)
            if not isinstance(geometry, dict):
                raise ValueError("PostGIS returned invalid GeoJSON geometry")
            result.append(
                VectorFeature(
                    id=row["id"],
                    geometry={str(key): value for key, value in geometry.items()},
                    properties=_feature_properties(entry, spec, dict(row)),
                )
            )
        return result
