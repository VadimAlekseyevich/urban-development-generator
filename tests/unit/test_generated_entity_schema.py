from typing import Any

import pytest
from geoalchemy2 import Geometry
from sqlalchemy import CheckConstraint

from backend.app.models.generated_entity import (
    GeneratedBlock,
    GeneratedBuilding,
    GeneratedInfrastructure,
    GeneratedParcel,
    GeneratedRoad,
    GeneratedZone,
)

GENERATED_ENTITY_CASES = (
    (GeneratedZone, "generated_zones", "MULTIPOLYGON"),
    (GeneratedRoad, "generated_roads", "LINESTRING"),
    (GeneratedBlock, "generated_blocks", "POLYGON"),
    (GeneratedParcel, "generated_parcels", "POLYGON"),
    (GeneratedBuilding, "generated_buildings", "POLYGON"),
    (GeneratedInfrastructure, "generated_infrastructure", "GEOMETRY"),
)


@pytest.mark.parametrize(("model", "table_name", "geometry_type"), GENERATED_ENTITY_CASES)
def test_generated_entity_schema_is_run_scoped(
    model: Any,
    table_name: str,
    geometry_type: str,
) -> None:
    table = model.__table__

    assert table.name == table_name
    assert {"id", "run_id", "geometry", "attributes_json", "created_at"} <= set(
        table.c.keys()
    )

    run_fk = next(iter(table.c.run_id.foreign_keys))
    assert run_fk.target_fullname == "generation_runs.id"
    assert run_fk.ondelete == "CASCADE"
    assert table.c.run_id.nullable is False

    geometry = table.c.geometry.type
    assert isinstance(geometry, Geometry)
    assert geometry.geometry_type == geometry_type
    assert geometry.srid == -1
    assert geometry.spatial_index is False
    assert table.c.geometry.nullable is False


@pytest.mark.parametrize(("model", "table_name", "_geometry_type"), GENERATED_ENTITY_CASES)
def test_generated_entity_attributes_are_json_objects(
    model: Any,
    table_name: str,
    _geometry_type: str,
) -> None:
    constraint_names = {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert f"ck_{table_name}_attributes_object" in constraint_names
    assert model.__table__.c.attributes_json.nullable is False


@pytest.mark.parametrize(("model", "table_name", "_geometry_type"), GENERATED_ENTITY_CASES)
def test_generated_entity_has_run_and_spatial_access_indexes(
    model: Any,
    table_name: str,
    _geometry_type: str,
) -> None:
    indexes = {index.name: index for index in model.__table__.indexes}

    scoped_index = indexes[f"ix_{table_name}_run_id_id"]
    assert tuple(column.name for column in scoped_index.columns) == ("run_id", "id")

    geometry_index = indexes[f"ix_{table_name}_geometry"]
    assert tuple(column.name for column in geometry_index.columns) == ("geometry",)
    assert geometry_index.dialect_options["postgresql"]["using"] == "gist"
