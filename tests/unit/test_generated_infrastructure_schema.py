from sqlalchemy import CheckConstraint

from backend.app.models.generated_entity import GeneratedInfrastructure

TYPED_COLUMNS = {
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
}


def test_generated_infrastructure_exposes_typed_t10_persistence_columns() -> None:
    table = GeneratedInfrastructure.__table__

    assert TYPED_COLUMNS <= set(table.c.keys())
    assert all(table.c[name].nullable is True for name in TYPED_COLUMNS)

    host_fk = next(iter(table.c.host_building_id.foreign_keys))
    assert host_fk.target_fullname == "generated_buildings.id"
    assert host_fk.ondelete == "RESTRICT"


def test_generated_infrastructure_has_typed_integrity_constraints() -> None:
    constraint_names = {
        constraint.name
        for constraint in GeneratedInfrastructure.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "ck_generated_infrastructure_candidate_id_nonempty",
        "ck_generated_infrastructure_type_code_nonempty",
        "ck_generated_infrastructure_category",
        "ck_generated_infrastructure_capacity_positive",
        "ck_generated_infrastructure_acceptance_index_nonnegative",
        "ck_generated_infrastructure_geometry_kind",
        "ck_generated_infrastructure_site_area_positive",
        "ck_generated_infrastructure_network_snapshot_nonempty",
        "ck_generated_infrastructure_network_node_nonempty",
        "ck_generated_infrastructure_snap_distance_nonnegative",
        "ck_generated_infrastructure_geometry_shape",
        "ck_generated_infrastructure_typed_shape",
    } <= constraint_names


def test_generated_infrastructure_has_deterministic_identity_indexes() -> None:
    indexes = {
        index.name: index for index in GeneratedInfrastructure.__table__.indexes
    }

    candidate_index = indexes[
        "uq_generated_infrastructure_run_candidate_type"
    ]
    assert candidate_index.unique is True
    assert tuple(column.name for column in candidate_index.columns) == (
        "run_id",
        "candidate_id",
        "infrastructure_type_code",
    )

    acceptance_index = indexes[
        "uq_generated_infrastructure_run_type_acceptance"
    ]
    assert acceptance_index.unique is True
    assert tuple(column.name for column in acceptance_index.columns) == (
        "run_id",
        "infrastructure_type_code",
        "acceptance_index",
    )
