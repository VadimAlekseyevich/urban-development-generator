from __future__ import annotations

import pytest
from shapely.geometry import Point, box

from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingArchetype,
    BuildingAreaBaseline,
    BuildingAreaMetricsCalculator,
    BuildingAreaMetricsError,
    BuildingAreaSubject,
    BuildingUse,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857


def _attributes(
    building_id: str,
    *,
    floors: int,
) -> AssignedBuildingAttributes:
    return AssignedBuildingAttributes(
        building_id=building_id,
        source_id="parcel:test",
        zone_class=ZoneClass.RESIDENTIAL,
        archetype=BuildingArchetype.POINT,
        use=BuildingUse.RESIDENTIAL,
        floors=floors,
        config_version="attrs-v1",
    )


def _subject(
    building_id: str,
    geometry,
    *,
    floors: int,
    working_srid: int = WORKING_SRID,
) -> BuildingAreaSubject:
    return BuildingAreaSubject(
        building_id=building_id,
        geometry=geometry,
        attributes=_attributes(building_id, floors=floors),
        working_srid=working_srid,
    )


def test_calculates_per_building_footprint_and_gfa() -> None:
    result = BuildingAreaMetricsCalculator(
        working_srid=WORKING_SRID,
    ).calculate(
        (
            _subject(
                "building:a",
                box(0, 0, 10, 10),
                floors=3,
            ),
        ),
        site_area_m2=1000.0,
    )

    metric = result.buildings[0]
    assert metric.footprint_area_m2 == pytest.approx(100.0)
    assert metric.floors == 3
    assert metric.gfa_m2 == pytest.approx(300.0)
    assert result.summary.coverage_ratio == pytest.approx(0.10)
    assert result.summary.far == pytest.approx(0.30)


def test_aggregate_metrics_include_authoritative_existing_baseline() -> None:
    result = BuildingAreaMetricsCalculator(
        working_srid=WORKING_SRID,
    ).calculate(
        (
            _subject(
                "building:a",
                box(0, 0, 10, 5),
                floors=4,
            ),
            _subject(
                "building:b",
                box(20, 0, 30, 5),
                floors=2,
            ),
        ),
        site_area_m2=1000.0,
        baseline=BuildingAreaBaseline(
            footprint_area_m2=100.0,
            gfa_m2=250.0,
        ),
    )

    summary = result.summary
    assert summary.generated_footprint_area_m2 == pytest.approx(100.0)
    assert summary.generated_gfa_m2 == pytest.approx(300.0)
    assert summary.total_footprint_area_m2 == pytest.approx(200.0)
    assert summary.total_gfa_m2 == pytest.approx(550.0)
    assert summary.coverage_ratio == pytest.approx(0.20)
    assert summary.far == pytest.approx(0.55)
    assert summary.building_count == 2


def test_result_is_deterministic_and_sorted_by_building_id() -> None:
    first_subject = _subject(
        "building:a",
        box(0, 0, 10, 10),
        floors=2,
    )
    second_subject = _subject(
        "building:b",
        box(20, 0, 25, 10),
        floors=5,
    )
    calculator = BuildingAreaMetricsCalculator(working_srid=WORKING_SRID)

    first = calculator.calculate(
        (second_subject, first_subject),
        site_area_m2=1000.0,
    )
    second = calculator.calculate(
        (first_subject, second_subject),
        site_area_m2=1000.0,
    )

    assert first == second
    assert tuple(item.building_id for item in first.buildings) == (
        "building:a",
        "building:b",
    )


def test_empty_generated_set_still_reports_baseline_metrics() -> None:
    result = BuildingAreaMetricsCalculator(
        working_srid=WORKING_SRID,
    ).calculate(
        (),
        site_area_m2=1000.0,
        baseline=BuildingAreaBaseline(
            footprint_area_m2=200.0,
            gfa_m2=600.0,
        ),
    )

    assert result.buildings == ()
    assert result.summary.building_count == 0
    assert result.summary.coverage_ratio == pytest.approx(0.20)
    assert result.summary.far == pytest.approx(0.60)


def test_total_footprint_area_cannot_exceed_site_area() -> None:
    calculator = BuildingAreaMetricsCalculator(working_srid=WORKING_SRID)

    with pytest.raises(
        BuildingAreaMetricsError,
        match="cannot exceed site area",
    ):
        calculator.calculate(
            (
                _subject(
                    "building:a",
                    box(0, 0, 10, 10),
                    floors=1,
                ),
            ),
            site_area_m2=50.0,
        )


def test_subject_contract_validates_geometry_id_and_crs_alignment() -> None:
    with pytest.raises(BuildingAreaMetricsError, match="Polygon"):
        _subject(
            "building:a",
            Point(0, 0),
            floors=2,
        )

    attributes = _attributes("building:other", floors=2)
    with pytest.raises(BuildingAreaMetricsError, match="must match"):
        BuildingAreaSubject(
            building_id="building:a",
            geometry=box(0, 0, 10, 10),
            attributes=attributes,
            working_srid=WORKING_SRID,
        )

    calculator = BuildingAreaMetricsCalculator(working_srid=WORKING_SRID)
    with pytest.raises(BuildingAreaMetricsError, match="working_srid"):
        calculator.calculate(
            (
                _subject(
                    "building:a",
                    box(0, 0, 10, 10),
                    floors=2,
                    working_srid=32637,
                ),
            ),
            site_area_m2=1000.0,
        )


def test_duplicate_ids_and_subject_limit_are_rejected() -> None:
    subject = _subject(
        "building:a",
        box(0, 0, 10, 10),
        floors=2,
    )

    with pytest.raises(BuildingAreaMetricsError, match="unique"):
        BuildingAreaMetricsCalculator(
            working_srid=WORKING_SRID,
        ).calculate(
            (subject, subject),
            site_area_m2=1000.0,
        )

    with pytest.raises(BuildingAreaMetricsError, match="limit exceeded"):
        BuildingAreaMetricsCalculator(
            working_srid=WORKING_SRID,
            max_subjects=1,
        ).calculate(
            (
                subject,
                _subject(
                    "building:b",
                    box(20, 0, 30, 10),
                    floors=2,
                ),
            ),
            site_area_m2=1000.0,
        )


def test_metric_calculator_requires_projected_working_crs() -> None:
    with pytest.raises(ValueError, match="not projected"):
        BuildingAreaMetricsCalculator(working_srid=4326)
