from __future__ import annotations

import uuid

import pytest
from shapely.geometry import Point, Polygon

from core.urban_generator.demography import DemographicDemandCategory
from core.urban_generator.domain import (
    ProjectRef,
    ProjectSettings,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)
from core.urban_generator.infrastructure import (
    ExistingFacilityMappingRule,
    ExistingFacilitySourceRecord,
    ExistingInfrastructureAdapter,
    ExistingInfrastructureError,
    InfrastructureCandidatePolicy,
    InfrastructureCandidateSource,
    InfrastructureCategory,
    InfrastructureDemandModel,
    InfrastructureType,
)
from core.urban_generator.zoning import ZoneClass


def _snapshot() -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        project=ProjectRef(
            project_id=uuid.UUID("22222222-2222-2222-2222-222222222222")
        ),
        settings=ProjectSettings(working_srid=3857),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="dataset:boundary:v1",
        ),
        facilities=(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.FACILITIES,
                source_ref="dataset:facilities:v3",
            ),
        ),
    )


def _type(
    code: str,
    *,
    category: InfrastructureCategory = InfrastructureCategory.EDUCATION,
    capacity: float = 500.0,
) -> InfrastructureType:
    return InfrastructureType(
        version="infrastructure-v1",
        code=code,
        category=category,
        demand_model=InfrastructureDemandModel(
            signal=DemographicDemandCategory.POPULATION,
            demand_rate=0.1,
        ),
        capacity=capacity,
        max_network_distance_m=1500.0,
        allowed_zones=(ZoneClass.PUBLIC, ZoneClass.MIXED),
        minimum_site_area_m2=500.0,
        target_site_area_m2=1000.0,
        candidate_policy=InfrastructureCandidatePolicy(
            sources=(InfrastructureCandidateSource.PARCEL,)
        ),
    )


def _record(
    feature_id: str,
    *,
    facility_use: str = "school",
    capacity: float | None = None,
    source_ref: str = "dataset:facilities:v3",
    working_srid: int = 3857,
):
    return ExistingFacilitySourceRecord(
        source_ref=source_ref,
        source_feature_id=feature_id,
        facility_use=facility_use,
        geometry=Point(30.31, 59.94),
        working_srid=working_srid,
        capacity=capacity,
        name="Existing facility " + feature_id,
    )


def test_adapter_maps_use_and_prefers_explicit_source_capacity() -> None:
    result = ExistingInfrastructureAdapter().adapt(
        _snapshot(),
        records=(_record("school-1", capacity=320.0),),
        infrastructure_types=(_type("school.general", capacity=600.0),),
        mapping_rules=(
            ExistingFacilityMappingRule(
                facility_use="school",
                infrastructure_type_code="school.general",
                default_capacity=450.0,
            ),
        ),
    )

    assert result.snapshot_id == "11111111-1111-1111-1111-111111111111"
    assert len(result.facilities) == 1
    facility = result.facilities[0]
    assert facility.infrastructure_type_code == "school.general"
    assert facility.capacity == pytest.approx(320.0)
    assert facility.source_ref == "dataset:facilities:v3"
    assert facility.source_feature_id == "school-1"
    assert facility.geometry.equals(Point(30.31, 59.94))
    assert facility.working_srid == 3857
    assert result.diagnostics.input_count == 1
    assert result.diagnostics.mapped_count == 1
    assert result.diagnostics.skipped_unmapped_count == 0


def test_capacity_fallback_prefers_mapping_default_then_type_capacity() -> None:
    school = _type("school.general", capacity=600.0)

    with_mapping_default = ExistingInfrastructureAdapter().adapt(
        _snapshot(),
        records=(_record("school-default"),),
        infrastructure_types=(school,),
        mapping_rules=(
            ExistingFacilityMappingRule(
                facility_use="school",
                infrastructure_type_code=school.code,
                default_capacity=450.0,
            ),
        ),
    )
    assert with_mapping_default.facilities[0].capacity == pytest.approx(450.0)

    with_type_default = ExistingInfrastructureAdapter().adapt(
        _snapshot(),
        records=(_record("school-type"),),
        infrastructure_types=(school,),
        mapping_rules=(
            ExistingFacilityMappingRule(
                facility_use="school",
                infrastructure_type_code=school.code,
            ),
        ),
    )
    assert with_type_default.facilities[0].capacity == pytest.approx(600.0)


def test_unmapped_use_can_be_skipped_or_rejected() -> None:
    school = _type("school.general")
    record = _record("clinic-1", facility_use="clinic")

    skipped = ExistingInfrastructureAdapter().adapt(
        _snapshot(),
        records=(record,),
        infrastructure_types=(school,),
        mapping_rules=(
            ExistingFacilityMappingRule(
                facility_use="school",
                infrastructure_type_code=school.code,
            ),
        ),
    )
    assert skipped.facilities == ()
    assert skipped.diagnostics.skipped_unmapped_count == 1

    with pytest.raises(ExistingInfrastructureError, match="no facility mapping rule"):
        ExistingInfrastructureAdapter().adapt(
            _snapshot(),
            records=(record,),
            infrastructure_types=(school,),
            mapping_rules=(
                ExistingFacilityMappingRule(
                    facility_use="school",
                    infrastructure_type_code=school.code,
                ),
            ),
            skip_unmapped=False,
        )


def test_record_must_reference_facility_layer_from_snapshot() -> None:
    with pytest.raises(ExistingInfrastructureError, match="not part of snapshot"):
        ExistingInfrastructureAdapter().adapt(
            _snapshot(),
            records=(
                _record(
                    "school-1",
                    source_ref="dataset:other-facilities:v1",
                ),
            ),
            infrastructure_types=(_type("school.general"),),
            mapping_rules=(
                ExistingFacilityMappingRule(
                    facility_use="school",
                    infrastructure_type_code="school.general",
                ),
            ),
        )


def test_record_working_srid_must_match_snapshot() -> None:
    with pytest.raises(ExistingInfrastructureError, match="working_srid"):
        ExistingInfrastructureAdapter().adapt(
            _snapshot(),
            records=(_record("school-1", working_srid=32637),),
            infrastructure_types=(_type("school.general"),),
            mapping_rules=(
                ExistingFacilityMappingRule(
                    facility_use="school",
                    infrastructure_type_code="school.general",
                ),
            ),
        )


def test_mapping_must_reference_known_infrastructure_type() -> None:
    with pytest.raises(ExistingInfrastructureError, match="unknown infrastructure type"):
        ExistingInfrastructureAdapter().adapt(
            _snapshot(),
            records=(_record("school-1"),),
            infrastructure_types=(_type("school.general"),),
            mapping_rules=(
                ExistingFacilityMappingRule(
                    facility_use="school",
                    infrastructure_type_code="school.unknown",
                ),
            ),
        )


def test_duplicate_type_code_and_mapping_use_are_rejected() -> None:
    school = _type("school.general")

    with pytest.raises(ExistingInfrastructureError, match="duplicate infrastructure type"):
        ExistingInfrastructureAdapter().adapt(
            _snapshot(),
            records=(),
            infrastructure_types=(school, school),
            mapping_rules=(),
        )

    with pytest.raises(ExistingInfrastructureError, match="duplicate facility mapping"):
        ExistingInfrastructureAdapter().adapt(
            _snapshot(),
            records=(),
            infrastructure_types=(school,),
            mapping_rules=(
                ExistingFacilityMappingRule(
                    facility_use="school",
                    infrastructure_type_code=school.code,
                ),
                ExistingFacilityMappingRule(
                    facility_use="school",
                    infrastructure_type_code=school.code,
                ),
            ),
        )


def test_duplicate_source_feature_is_rejected() -> None:
    school = _type("school.general")
    record = _record("school-1")

    with pytest.raises(ExistingInfrastructureError, match="duplicate facility source"):
        ExistingInfrastructureAdapter().adapt(
            _snapshot(),
            records=(record, record),
            infrastructure_types=(school,),
            mapping_rules=(
                ExistingFacilityMappingRule(
                    facility_use="school",
                    infrastructure_type_code=school.code,
                ),
            ),
        )


def test_output_order_is_deterministic_and_geometry_shape_is_preserved() -> None:
    school = _type("school.general")
    polygon = Polygon(((0, 0), (10, 0), (10, 10), (0, 10), (0, 0)))
    first = ExistingFacilitySourceRecord(
        source_ref="dataset:facilities:v3",
        source_feature_id="z-school",
        facility_use="school",
        geometry=polygon,
        working_srid=3857,
    )
    second = _record("a-school")

    result = ExistingInfrastructureAdapter().adapt(
        _snapshot(),
        records=(first, second),
        infrastructure_types=(school,),
        mapping_rules=(
            ExistingFacilityMappingRule(
                facility_use="school",
                infrastructure_type_code=school.code,
            ),
        ),
    )

    assert [item.source_feature_id for item in result.facilities] == [
        "a-school",
        "z-school",
    ]
    assert result.facilities[1].geometry.equals(polygon)
    assert result.facilities[1].geometry.geom_type == "Polygon"


@pytest.mark.parametrize("capacity", [0.0, -1.0, float("inf"), float("nan")])
def test_source_capacity_must_be_positive_finite(capacity: float) -> None:
    with pytest.raises(ExistingInfrastructureError):
        _record("school-1", capacity=capacity)
