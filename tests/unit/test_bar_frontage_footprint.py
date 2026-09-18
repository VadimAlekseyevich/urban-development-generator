from __future__ import annotations

import pytest
from shapely.geometry import LineString, Point, box

from core.urban_generator.buildings import (
    BarFootprintAxis,
    BarFootprintAxisSource,
    BarFootprintError,
    BarFootprintSpec,
    BarFrontageFootprintStrategy,
    BuildingEnvelopeResult,
    BuildingEnvelopeSourceKind,
    BuildingEnvelopeStatus,
    BuildingFootprintStatus,
    BuildingPlacementCandidate,
    BuildingPlacementCandidateKind,
)

WORKING_SRID = 3857


def _envelope(
    geometry=None,
    *,
    source_id: str = "parcel:test",
    status: BuildingEnvelopeStatus = BuildingEnvelopeStatus.READY,
    working_srid: int = WORKING_SRID,
) -> BuildingEnvelopeResult:
    resolved = geometry if geometry is not None else box(0, 0, 40, 20)
    return BuildingEnvelopeResult(
        source_id=source_id,
        source_kind=BuildingEnvelopeSourceKind.PARCEL,
        working_srid=working_srid,
        status=status,
        candidate_geometry=resolved,
        constraint_reports=(),
    )


def _candidate(
    *,
    x: float = 20.0,
    y: float = 0.0,
    source_id: str = "parcel:test",
    kind: BuildingPlacementCandidateKind = BuildingPlacementCandidateKind.FRONTAGE,
    road_id: str | None = "road:front",
) -> BuildingPlacementCandidate:
    return BuildingPlacementCandidate(
        candidate_id="candidate:00000001",
        source_id=source_id,
        kind=kind,
        point=Point(x, y),
        frontage_road_id=(
            road_id
            if kind is BuildingPlacementCandidateKind.FRONTAGE
            else None
        ),
    )


def _axis(
    coordinates=((0.0, 0.0), (40.0, 0.0)),
    *,
    source: BarFootprintAxisSource = BarFootprintAxisSource.FRONTAGE,
    source_ref: str = "road:front",
    working_srid: int = WORKING_SRID,
) -> BarFootprintAxis:
    return BarFootprintAxis(
        source=source,
        source_ref=source_ref,
        geometry=LineString(coordinates),
        working_srid=working_srid,
    )


def test_frontage_bar_selects_inward_side_and_preserves_axis_provenance() -> None:
    result = BarFrontageFootprintStrategy(
        working_srid=WORKING_SRID,
    ).create(
        _candidate(),
        envelope=_envelope(),
        axis=_axis(),
        spec=BarFootprintSpec(length_m=20.0, depth_m=6.0),
    )

    assert result.status is BuildingFootprintStatus.READY
    assert result.is_ready is True
    assert result.footprint_geometry is not None
    assert result.center.x == pytest.approx(20.0)
    assert result.center.y == pytest.approx(3.0)
    assert result.axis_angle_degrees == pytest.approx(0.0)
    assert result.proposed_geometry.equals(box(10, 0, 30, 6))
    assert result.area_m2 == pytest.approx(120.0)
    assert result.axis_source is BarFootprintAxisSource.FRONTAGE
    assert result.axis_source_ref == "road:front"


def test_reversing_axis_direction_keeps_same_bar_geometry_and_angle() -> None:
    strategy = BarFrontageFootprintStrategy(working_srid=WORKING_SRID)
    spec = BarFootprintSpec(length_m=20.0, depth_m=6.0)

    forward = strategy.create(
        _candidate(),
        envelope=_envelope(),
        axis=_axis(),
        spec=spec,
    )
    reverse = strategy.create(
        _candidate(),
        envelope=_envelope(),
        axis=_axis(((40.0, 0.0), (0.0, 0.0))),
        spec=spec,
    )

    assert forward.axis_angle_degrees == pytest.approx(0.0)
    assert reverse.axis_angle_degrees == pytest.approx(0.0)
    assert forward.center.equals(reverse.center)
    assert forward.proposed_geometry.equals(reverse.proposed_geometry)


def test_vertical_frontage_bar_uses_matching_inward_normal() -> None:
    result = BarFrontageFootprintStrategy(
        working_srid=WORKING_SRID,
    ).create(
        _candidate(x=0.0, y=10.0),
        envelope=_envelope(),
        axis=_axis(
            ((0.0, 0.0), (0.0, 20.0)),
        ),
        spec=BarFootprintSpec(length_m=12.0, depth_m=6.0),
    )

    assert result.status is BuildingFootprintStatus.READY
    assert result.axis_angle_degrees == pytest.approx(90.0)
    assert result.center.x == pytest.approx(3.0)
    assert result.center.y == pytest.approx(10.0)
    assert result.proposed_geometry.bounds == pytest.approx(
        (0.0, 4.0, 6.0, 16.0)
    )


def test_road_or_block_axis_uses_candidate_as_center_without_side_selection() -> None:
    candidate = _candidate(
        x=20.0,
        y=10.0,
        kind=BuildingPlacementCandidateKind.GRID,
        road_id=None,
    )
    axis = _axis(
        ((0.0, 10.0), (40.0, 10.0)),
        source=BarFootprintAxisSource.BLOCK,
        source_ref="block:test",
    )
    result = BarFrontageFootprintStrategy(
        working_srid=WORKING_SRID,
    ).create(
        candidate,
        envelope=_envelope(),
        axis=axis,
        spec=BarFootprintSpec(length_m=20.0, depth_m=6.0),
    )

    assert result.status is BuildingFootprintStatus.READY
    assert result.center.equals(candidate.point)
    assert result.proposed_geometry.equals(box(10, 7, 30, 13))
    assert result.axis_source is BarFootprintAxisSource.BLOCK


def test_frontage_bar_reports_outside_when_neither_inward_side_fits() -> None:
    result = BarFrontageFootprintStrategy(
        working_srid=WORKING_SRID,
    ).create(
        _candidate(x=2.0, y=0.0),
        envelope=_envelope(),
        axis=_axis(),
        spec=BarFootprintSpec(length_m=20.0, depth_m=6.0),
    )

    assert result.status is BuildingFootprintStatus.OUTSIDE_ENVELOPE
    assert result.is_ready is False
    assert result.footprint_geometry is None


def test_bar_strategy_rejects_invalid_scope_crs_axis_and_frontage_provenance() -> None:
    strategy = BarFrontageFootprintStrategy(working_srid=WORKING_SRID)
    spec = BarFootprintSpec(length_m=20.0, depth_m=6.0)

    with pytest.raises(BarFootprintError, match="READY"):
        strategy.create(
            _candidate(),
            envelope=_envelope(
                status=BuildingEnvelopeStatus.BELOW_MINIMUM_AREA
            ),
            axis=_axis(),
            spec=spec,
        )

    with pytest.raises(BarFootprintError, match="source_id"):
        strategy.create(
            _candidate(source_id="parcel:other"),
            envelope=_envelope(),
            axis=_axis(),
            spec=spec,
        )

    with pytest.raises(BarFootprintError, match="axis working_srid"):
        strategy.create(
            _candidate(),
            envelope=_envelope(),
            axis=_axis(working_srid=32637),
            spec=spec,
        )

    with pytest.raises(BarFootprintError, match="must lie"):
        strategy.create(
            _candidate(x=20.0, y=1.0),
            envelope=_envelope(),
            axis=_axis(),
            spec=spec,
        )

    with pytest.raises(BarFootprintError, match="FRONTAGE"):
        strategy.create(
            _candidate(
                x=20.0,
                y=0.0,
                kind=BuildingPlacementCandidateKind.GRID,
                road_id=None,
            ),
            envelope=_envelope(),
            axis=_axis(),
            spec=spec,
        )

    with pytest.raises(BarFootprintError, match="road id"):
        strategy.create(
            _candidate(road_id="road:other"),
            envelope=_envelope(),
            axis=_axis(),
            spec=spec,
        )


def test_bar_axis_and_spec_validate_bounded_metric_shape_contract() -> None:
    with pytest.raises(BarFootprintError, match="greater than"):
        BarFootprintSpec(length_m=6.0, depth_m=6.0)
    with pytest.raises(BarFootprintError, match="positive finite"):
        BarFootprintSpec(length_m=float("inf"), depth_m=6.0)
    with pytest.raises(BarFootprintError, match="exactly two"):
        _axis(((0.0, 0.0), (10.0, 0.0), (20.0, 0.0)))
    with pytest.raises(ValueError, match="not projected"):
        BarFrontageFootprintStrategy(working_srid=4326)
