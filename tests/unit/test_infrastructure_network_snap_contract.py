from __future__ import annotations

import pytest
from shapely.geometry import Point, box

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import NetworkNodeRef, NetworkPoint
from core.urban_generator.infrastructure import (
    BlockInfrastructureDemand,
    ExistingInfrastructureFacility,
    ExistingInfrastructureFacilityRef,
    ExistingInfrastructureFacilitySnap,
    ExistingInfrastructureFacilitySnapInput,
    InfrastructureCandidateGeometry,
    InfrastructureCandidateGeometryKind,
    InfrastructureCandidateRef,
    InfrastructureCandidateSnap,
    InfrastructureCandidateSnapInput,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandRef,
    InfrastructureDemandSnap,
    InfrastructureDemandSnapInput,
    InfrastructureNetworkSnapError,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857


def _demand() -> BlockInfrastructureDemand:
    return BlockInfrastructureDemand(
        block_id="block-1",
        zone_id="zone-1",
        zone_class=ZoneClass.RESIDENTIAL,
        infrastructure_type_code="school.general",
        infrastructure_category=InfrastructureCategory.EDUCATION,
        demographic_signal=DemographicDemandCategory.AGE_GROUP,
        demographic_group="child",
        source_signal_value=20.0,
        demand_rate=0.5,
        gross_demand=10.0,
        served_demand=0.0,
        unmet_demand=10.0,
    )


def _facility() -> ExistingInfrastructureFacility:
    return ExistingInfrastructureFacility(
        facility_id="dataset:facilities:v1:school-1",
        source_ref="dataset:facilities:v1",
        source_feature_id="school-1",
        infrastructure_type_code="school.general",
        capacity=250.0,
        geometry=Point(10, 20),
        working_srid=WORKING_SRID,
    )


def _candidate() -> InfrastructureCandidateGeometry:
    site = box(0, 0, 10, 10)
    return InfrastructureCandidateGeometry(
        candidate_id="school.general:parcel:parcel-1",
        infrastructure_type_code="school.general",
        source_kind=InfrastructureCandidateSource.PARCEL,
        source_id="parcel-1",
        zone_class=ZoneClass.PUBLIC,
        working_srid=WORKING_SRID,
        anchor=Point(5, 5),
        kind=InfrastructureCandidateGeometryKind.SITE,
        site_geometry=site,
        site_area_m2=100.0,
        zone_id="zone-1",
        block_id="block-1",
    )


def test_stable_refs_are_derived_from_authoritative_s10_records() -> None:
    demand = InfrastructureDemandRef.from_demand(_demand())
    facility = ExistingInfrastructureFacilityRef.from_facility(_facility())
    candidate = InfrastructureCandidateRef.from_candidate(_candidate())

    assert demand.key == ("block-1", "school.general")
    assert facility.key == (
        "dataset:facilities:v1:school-1",
        "school.general",
    )
    assert candidate.key == (
        "school.general:parcel:parcel-1",
        "school.general",
    )


def test_subject_families_do_not_collapse_to_one_untyped_reference() -> None:
    demand = InfrastructureDemandRef(
        block_id="shared-id",
        infrastructure_type_code="school.general",
    )
    facility = ExistingInfrastructureFacilityRef(
        facility_id="shared-id",
        infrastructure_type_code="school.general",
    )
    candidate = InfrastructureCandidateRef(
        candidate_id="shared-id",
        infrastructure_type_code="school.general",
    )

    assert demand != facility
    assert facility != candidate
    assert demand != candidate


def test_snap_inputs_carry_metric_points_and_working_crs() -> None:
    point = NetworkPoint(x_m=10.0, y_m=20.0)

    demand = InfrastructureDemandSnapInput(
        ref=InfrastructureDemandRef.from_demand(_demand()),
        point=point,
        working_srid=WORKING_SRID,
    )
    facility = ExistingInfrastructureFacilitySnapInput(
        ref=ExistingInfrastructureFacilityRef.from_facility(_facility()),
        point=point,
        working_srid=WORKING_SRID,
    )
    candidate = InfrastructureCandidateSnapInput(
        ref=InfrastructureCandidateRef.from_candidate(_candidate()),
        point=point,
        working_srid=WORKING_SRID,
    )

    assert demand.point == point
    assert facility.point == point
    assert candidate.point == point
    assert demand.working_srid == WORKING_SRID
    assert facility.working_srid == WORKING_SRID
    assert candidate.working_srid == WORKING_SRID


def test_snap_input_rejects_non_metric_working_crs() -> None:
    with pytest.raises(
        InfrastructureNetworkSnapError,
        match="projected",
    ):
        InfrastructureDemandSnapInput(
            ref=InfrastructureDemandRef.from_demand(_demand()),
            point=NetworkPoint(x_m=10.0, y_m=20.0),
            working_srid=4326,
        )


def test_successful_snap_records_preserve_typed_ref_node_and_distance() -> None:
    node = NetworkNodeRef(node_id="road-node-7")

    demand = InfrastructureDemandSnap(
        ref=InfrastructureDemandRef.from_demand(_demand()),
        node=node,
        distance_m=4.5,
    )
    facility = ExistingInfrastructureFacilitySnap(
        ref=ExistingInfrastructureFacilityRef.from_facility(_facility()),
        node=node,
        distance_m=0.0,
    )
    candidate = InfrastructureCandidateSnap(
        ref=InfrastructureCandidateRef.from_candidate(_candidate()),
        node=node,
        distance_m=12.0,
    )

    assert demand.node == node
    assert facility.node == node
    assert candidate.node == node
    assert demand.distance_m == pytest.approx(4.5)
    assert facility.distance_m == 0.0
    assert candidate.distance_m == pytest.approx(12.0)


@pytest.mark.parametrize(
    "distance_m",
    [-1.0, float("inf"), float("nan")],
)
def test_successful_snap_distance_must_be_non_negative_and_finite(
    distance_m: float,
) -> None:
    with pytest.raises(
        InfrastructureNetworkSnapError,
        match="finite non-negative",
    ):
        InfrastructureDemandSnap(
            ref=InfrastructureDemandRef.from_demand(_demand()),
            node=NetworkNodeRef(node_id="road-node-7"),
            distance_m=distance_m,
        )


def test_ref_identifiers_reject_blank_and_line_break_values() -> None:
    with pytest.raises(InfrastructureNetworkSnapError):
        InfrastructureDemandRef(
            block_id="",
            infrastructure_type_code="school.general",
        )
    with pytest.raises(InfrastructureNetworkSnapError):
        InfrastructureCandidateRef(
            candidate_id="candidate\n1",
            infrastructure_type_code="school.general",
        )
