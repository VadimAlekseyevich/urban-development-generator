import pytest
from shapely.geometry import LineString, MultiPolygon, box

from core.urban_generator.blocks import (
    MAX_PLANNING_PARCEL_FRONTAGES,
    PLANNING_PARCEL_SEMANTICS,
    ParcelDomainError,
    ParcelFrontageSegment,
    PlanningParcel,
)
from core.urban_generator.domain.crs import CRSContractError
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857


def _frontage(road_id: str, coordinates) -> ParcelFrontageSegment:
    return ParcelFrontageSegment(
        road_id=road_id,
        geometry=LineString(coordinates),
    )


def test_planning_parcel_exposes_non_cadastral_frontage_and_buildable_metrics() -> None:
    parcel = PlanningParcel(
        parcel_id="parcel:0001",
        block_id="block:0001",
        working_srid=WORKING_SRID,
        geometry=box(0, 0, 10, 10),
        buildable_envelope=box(1, 1, 9, 9),
        frontages=(
            _frontage("road:a", [(0, 0), (10, 0)]),
            _frontage("road:b", [(0, 0), (0, 5)]),
        ),
        zone_id="zone:residential",
        zone_class=ZoneClass.RESIDENTIAL,
    )

    assert parcel.semantics == PLANNING_PARCEL_SEMANTICS
    assert parcel.semantics == "planning_lot_non_cadastral"
    assert parcel.area_m2 == pytest.approx(100.0)
    assert parcel.buildable_area_m2 == pytest.approx(64.0)
    assert parcel.buildable_ratio == pytest.approx(0.64)
    assert parcel.frontage_length_m == pytest.approx(15.0)
    assert parcel.frontage_road_ids == ("road:a", "road:b")
    assert parcel.has_frontage is True
    assert parcel.is_zone_associated is True


def test_planning_parcel_can_exist_without_frontage_or_zone_relation() -> None:
    parcel = PlanningParcel(
        parcel_id="parcel:no-frontage",
        block_id="block:a",
        working_srid=WORKING_SRID,
        geometry=box(0, 0, 10, 10),
        buildable_envelope=box(0, 0, 10, 10),
    )

    assert parcel.frontages == ()
    assert parcel.frontage_length_m == 0.0
    assert parcel.frontage_road_ids == ()
    assert parcel.has_frontage is False
    assert parcel.zone_id is None
    assert parcel.zone_class is None
    assert parcel.is_zone_associated is False
    assert parcel.buildable_ratio == pytest.approx(1.0)


def test_multi_polygon_buildable_envelope_is_allowed_inside_parcel() -> None:
    envelope = MultiPolygon(
        [
            box(1, 1, 4, 9),
            box(6, 1, 9, 9),
        ]
    )
    parcel = PlanningParcel(
        parcel_id="parcel:split-envelope",
        block_id="block:a",
        working_srid=WORKING_SRID,
        geometry=box(0, 0, 10, 10),
        buildable_envelope=envelope,
    )

    assert parcel.buildable_area_m2 == pytest.approx(48.0)
    assert parcel.buildable_ratio == pytest.approx(0.48)


def test_buildable_envelope_must_stay_inside_parcel() -> None:
    with pytest.raises(ParcelDomainError, match="buildable envelope must stay inside"):
        PlanningParcel(
            parcel_id="parcel:a",
            block_id="block:a",
            working_srid=WORKING_SRID,
            geometry=box(0, 0, 10, 10),
            buildable_envelope=box(1, 1, 11, 9),
        )


def test_frontage_must_lie_on_parcel_boundary() -> None:
    interior = _frontage("road:a", [(1, 1), (9, 1)])

    with pytest.raises(ParcelDomainError, match="must lie on parcel boundary"):
        PlanningParcel(
            parcel_id="parcel:a",
            block_id="block:a",
            working_srid=WORKING_SRID,
            geometry=box(0, 0, 10, 10),
            buildable_envelope=box(1, 1, 9, 9),
            frontages=(interior,),
        )


def test_frontages_require_canonical_order_and_unique_segments() -> None:
    first = _frontage("road:a", [(0, 0), (10, 0)])
    second = _frontage("road:b", [(0, 0), (0, 10)])

    with pytest.raises(ParcelDomainError, match="canonical deterministic order"):
        PlanningParcel(
            parcel_id="parcel:unordered",
            block_id="block:a",
            working_srid=WORKING_SRID,
            geometry=box(0, 0, 10, 10),
            buildable_envelope=box(1, 1, 9, 9),
            frontages=(second, first),
        )

    with pytest.raises(ParcelDomainError, match="frontages must be unique"):
        PlanningParcel(
            parcel_id="parcel:duplicate",
            block_id="block:a",
            working_srid=WORKING_SRID,
            geometry=box(0, 0, 10, 10),
            buildable_envelope=box(1, 1, 9, 9),
            frontages=(first, first),
        )


def test_frontage_count_is_bounded() -> None:
    frontages = tuple(
        _frontage(f"road:{index:03d}", [(0, 0), (10, 0)])
        for index in range(MAX_PLANNING_PARCEL_FRONTAGES + 1)
    )

    with pytest.raises(ParcelDomainError, match="frontage limit exceeded"):
        PlanningParcel(
            parcel_id="parcel:too-many-frontages",
            block_id="block:a",
            working_srid=WORKING_SRID,
            geometry=box(0, 0, 10, 10),
            buildable_envelope=box(1, 1, 9, 9),
            frontages=frontages,
        )


def test_zone_relation_must_be_complete_and_typed() -> None:
    with pytest.raises(ParcelDomainError, match="both be set or both be omitted"):
        PlanningParcel(
            parcel_id="parcel:zone-id-only",
            block_id="block:a",
            working_srid=WORKING_SRID,
            geometry=box(0, 0, 10, 10),
            buildable_envelope=box(1, 1, 9, 9),
            zone_id="zone:a",
        )

    with pytest.raises(ParcelDomainError, match="zone_class must be a ZoneClass"):
        PlanningParcel(
            parcel_id="parcel:bad-zone-class",
            block_id="block:a",
            working_srid=WORKING_SRID,
            geometry=box(0, 0, 10, 10),
            buildable_envelope=box(1, 1, 9, 9),
            zone_id="zone:a",
            zone_class="residential",  # type: ignore[arg-type]
        )


def test_parcel_and_frontage_geometry_types_are_strict() -> None:
    with pytest.raises(ParcelDomainError, match="parcel geometry must be a Polygon"):
        PlanningParcel(
            parcel_id="parcel:line",
            block_id="block:a",
            working_srid=WORKING_SRID,
            geometry=LineString([(0, 0), (10, 0)]),  # type: ignore[arg-type]
            buildable_envelope=box(1, 1, 9, 9),
        )

    with pytest.raises(ParcelDomainError, match="LineString or MultiLineString"):
        ParcelFrontageSegment(
            road_id="road:a",
            geometry=box(0, 0, 1, 1),
        )


def test_metric_working_crs_is_required() -> None:
    with pytest.raises(CRSContractError, match="not projected"):
        PlanningParcel(
            parcel_id="parcel:wgs84",
            block_id="block:a",
            working_srid=4326,
            geometry=box(0, 0, 10, 10),
            buildable_envelope=box(1, 1, 9, 9),
        )


def test_frontages_must_be_immutable_tuple() -> None:
    with pytest.raises(ParcelDomainError, match="immutable tuple"):
        PlanningParcel(
            parcel_id="parcel:list",
            block_id="block:a",
            working_srid=WORKING_SRID,
            geometry=box(0, 0, 10, 10),
            buildable_envelope=box(1, 1, 9, 9),
            frontages=[],  # type: ignore[arg-type]
        )
