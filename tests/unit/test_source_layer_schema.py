from typing import Any

import pytest
from geoalchemy2 import Geometry
from sqlalchemy import CheckConstraint, UniqueConstraint

from backend.app.models.source_layer import (
    SourceBuilding,
    SourceConstraint,
    SourceFacility,
    SourceLanduse,
    SourceRoad,
    SourceWater,
)

SOURCE_LAYER_CASES = (
    (SourceRoad, "source_roads", "MULTILINESTRING", "road_class"),
    (SourceBuilding, "source_buildings", "MULTIPOLYGON", "building_class"),
    (SourceLanduse, "source_landuse", "MULTIPOLYGON", "landuse_class"),
    (SourceWater, "source_water", "GEOMETRY", "water_class"),
    (SourceFacility, "source_facilities", "GEOMETRY", "facility_class"),
    (SourceConstraint, "source_constraints", "GEOMETRY", "constraint_code"),
)


@pytest.mark.parametrize(
    ("model", "table_name", "geometry_type", "classification_column"),
    SOURCE_LAYER_CASES,
)
def test_source_layer_schema_is_dataset_version_scoped(
    model: Any,
    table_name: str,
    geometry_type: str,
    classification_column: str,
) -> None:
    table = model.__table__

    assert table.name == table_name
    assert {
        "id",
        "dataset_version_id",
        "source_feature_id",
        "geometry",
        "attributes_json",
        "created_at",
        classification_column,
    } <= set(table.c.keys())

    version_fk = next(iter(table.c.dataset_version_id.foreign_keys))
    assert version_fk.target_fullname == "dataset_versions.id"
    assert version_fk.ondelete == "CASCADE"
    assert table.c.dataset_version_id.nullable is False
    assert table.c[classification_column].nullable is False

    geometry = table.c.geometry.type
    assert isinstance(geometry, Geometry)
    assert geometry.geometry_type == geometry_type
    assert geometry.srid == -1
    assert geometry.spatial_index is False
    assert table.c.geometry.nullable is False


@pytest.mark.parametrize(
    ("model", "table_name", "_geometry_type", "_classification_column"),
    SOURCE_LAYER_CASES,
)
def test_source_layer_attributes_and_external_identity_are_constrained(
    model: Any,
    table_name: str,
    _geometry_type: str,
    _classification_column: str,
) -> None:
    table = model.__table__
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert f"ck_{table_name}_attributes_object" in check_names
    assert f"uq_{table_name}_version_feature" in unique_names
    assert table.c.attributes_json.nullable is False
    assert table.c.source_feature_id.nullable is True


@pytest.mark.parametrize(
    ("model", "table_name", "_geometry_type", "_classification_column"),
    SOURCE_LAYER_CASES,
)
def test_source_layer_has_version_and_spatial_access_indexes(
    model: Any,
    table_name: str,
    _geometry_type: str,
    _classification_column: str,
) -> None:
    indexes = {index.name: index for index in model.__table__.indexes}

    scoped_index = indexes[f"ix_{table_name}_dataset_version_id_id"]
    assert tuple(column.name for column in scoped_index.columns) == (
        "dataset_version_id",
        "id",
    )

    geometry_index = indexes[f"ix_{table_name}_geometry"]
    assert tuple(column.name for column in geometry_index.columns) == ("geometry",)
    assert geometry_index.dialect_options["postgresql"]["using"] == "gist"


def test_source_road_has_normalized_transport_fields() -> None:
    table = SourceRoad.__table__
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "road_class",
        "name",
        "lanes",
        "max_speed_kph",
        "one_way",
        "one_way_direction",
        "bridge",
        "tunnel",
        "layer",
    } <= set(table.c.keys())
    assert table.c.one_way.nullable is False
    assert table.c.one_way_direction.nullable is False
    assert table.c.bridge.nullable is False
    assert table.c.tunnel.nullable is False
    assert table.c.layer.nullable is False
    assert "ck_source_roads_one_way_direction" in check_names


def test_source_building_has_normalized_form_fields() -> None:
    table = SourceBuilding.__table__

    assert {"building_class", "name", "levels", "height_m"} <= set(table.c.keys())


def test_source_facility_has_normalized_capacity_fields() -> None:
    table = SourceFacility.__table__

    assert {"facility_class", "name", "capacity"} <= set(table.c.keys())


def test_source_constraint_matches_core_classification_contract() -> None:
    table = SourceConstraint.__table__
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {"constraint_code", "severity", "scope"} <= set(table.c.keys())
    assert "ck_source_constraints_severity" in check_names
    assert "ck_source_constraints_scope" in check_names
