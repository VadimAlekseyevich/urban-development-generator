from __future__ import annotations

import pytest
from shapely.geometry import MultiPolygon, Point, box

from core.urban_generator.buildings import (
    BuildingEnvelopeResult,
    BuildingEnvelopeSourceKind,
    BuildingEnvelopeStatus,
    BuildingFootprintStrategy,
    PerimeterCourtyardFootprintError,
    PerimeterCourtyardFootprintSpec,
    PerimeterCourtyardFootprintStatus,
    PerimeterCourtyardFootprintStrategy,
    PerimeterCourtyardKind,
    PerimeterOpeningSide,
)

WORKING_SRID = 3857


def _block_envelope(
    geometry=None,
    *,
    source_kind: BuildingEnvelopeSourceKind = BuildingEnvelopeSourceKind.BLOCK,
    status: BuildingEnvelopeStatus = BuildingEnvelopeStatus.READY,
    working_srid: int = WORKING_SRID,
) -> BuildingEnvelopeResult:
    resolved = geometry if geometry is not None else box(0, 0, 40, 30)
    return BuildingEnvelopeResult(
        source_id="block:test",
        source_kind=source_kind,
        working_srid=working_srid,
        status=status,
        candidate_geometry=resolved,
        constraint_reports=(),
    )


def test_courtyard_strategy_creates_closed_ring_with_one_inner_hole() -> None:
    spec = PerimeterCourtyardFootprintSpec.courtyard(
        edge_setback_m=2.0,
        wing_depth_m=5.0,
    )
    result = PerimeterCourtyardFootprintStrategy(
        working_srid=WORKING_SRID,
    ).create(
        _block_envelope(),
        spec=spec,
    )

    assert result.status is PerimeterCourtyardFootprintStatus.READY
    assert result.is_ready is True
    assert result.strategy is BuildingFootprintStrategy.COURTYARD
    assert result.kind is PerimeterCourtyardKind.COURTYARD
    assert result.footprint_geometry is not None
    assert result.proposed_geometry is not None
    assert result.proposed_geometry.bounds == pytest.approx((2, 2, 38, 28))
    assert len(result.proposed_geometry.interiors) == 1
    assert result.area_m2 == pytest.approx(520.0)


def test_perimeter_strategy_opens_ring_to_outside_without_interior_hole() -> None:
    spec = PerimeterCourtyardFootprintSpec.perimeter(
        edge_setback_m=2.0,
        wing_depth_m=5.0,
        opening_width_m=6.0,
        opening_side=PerimeterOpeningSide.SOUTH,
    )
    result = PerimeterCourtyardFootprintStrategy(
        working_srid=WORKING_SRID,
    ).create(
        _block_envelope(),
        spec=spec,
    )

    assert result.status is PerimeterCourtyardFootprintStatus.READY
    assert result.strategy is BuildingFootprintStrategy.PERIMETER
    assert result.proposed_geometry is not None
    assert len(result.proposed_geometry.interiors) == 0
    assert result.area_m2 == pytest.approx(490.0, abs=1e-4)
    assert result.proposed_geometry.covers(Point(3, 3))
    assert not result.proposed_geometry.covers(Point(20, 4))


def test_perimeter_opening_side_is_explicit_and_changes_gap_location() -> None:
    strategy = PerimeterCourtyardFootprintStrategy(
        working_srid=WORKING_SRID,
    )
    south = strategy.create(
        _block_envelope(),
        spec=PerimeterCourtyardFootprintSpec.perimeter(
            edge_setback_m=2.0,
            wing_depth_m=5.0,
            opening_width_m=6.0,
            opening_side=PerimeterOpeningSide.SOUTH,
        ),
    )
    east = strategy.create(
        _block_envelope(),
        spec=PerimeterCourtyardFootprintSpec.perimeter(
            edge_setback_m=2.0,
            wing_depth_m=5.0,
            opening_width_m=6.0,
            opening_side=PerimeterOpeningSide.EAST,
        ),
    )

    assert south.proposed_geometry is not None
    assert east.proposed_geometry is not None
    assert not south.proposed_geometry.equals(east.proposed_geometry)
    assert not east.proposed_geometry.covers(Point(36, 15))
    assert east.proposed_geometry.covers(Point(20, 4))


def test_too_small_envelopes_return_explicit_non_ready_statuses() -> None:
    strategy = PerimeterCourtyardFootprintStrategy(
        working_srid=WORKING_SRID,
    )

    empty_after_setback = strategy.create(
        _block_envelope(box(0, 0, 10, 10)),
        spec=PerimeterCourtyardFootprintSpec.courtyard(
            edge_setback_m=6.0,
            wing_depth_m=1.0,
        ),
    )
    no_court = strategy.create(
        _block_envelope(box(0, 0, 10, 10)),
        spec=PerimeterCourtyardFootprintSpec.courtyard(
            edge_setback_m=1.0,
            wing_depth_m=4.0,
        ),
    )

    assert (
        empty_after_setback.status
        is PerimeterCourtyardFootprintStatus.EMPTY_AFTER_SETBACK
    )
    assert empty_after_setback.proposed_geometry is None
    assert (
        no_court.status
        is PerimeterCourtyardFootprintStatus.NO_COURT_SPACE
    )
    assert no_court.proposed_geometry is None


def test_multipart_envelope_is_rejected_instead_of_silently_dropping_parts() -> None:
    geometry = MultiPolygon(
        (
            box(0, 0, 20, 20),
            box(30, 0, 50, 20),
        )
    )
    result = PerimeterCourtyardFootprintStrategy(
        working_srid=WORKING_SRID,
    ).create(
        _block_envelope(geometry),
        spec=PerimeterCourtyardFootprintSpec.courtyard(
            edge_setback_m=1.0,
            wing_depth_m=4.0,
        ),
    )

    assert (
        result.status
        is PerimeterCourtyardFootprintStatus.MULTIPART_UNSUPPORTED
    )
    assert result.proposed_geometry is None


def test_strategy_requires_ready_block_envelope_and_matching_crs() -> None:
    strategy = PerimeterCourtyardFootprintStrategy(
        working_srid=WORKING_SRID,
    )
    spec = PerimeterCourtyardFootprintSpec.courtyard(
        edge_setback_m=1.0,
        wing_depth_m=4.0,
    )

    with pytest.raises(PerimeterCourtyardFootprintError, match="READY"):
        strategy.create(
            _block_envelope(
                status=BuildingEnvelopeStatus.BELOW_MINIMUM_AREA
            ),
            spec=spec,
        )

    with pytest.raises(PerimeterCourtyardFootprintError, match="BLOCK"):
        strategy.create(
            _block_envelope(
                source_kind=BuildingEnvelopeSourceKind.PARCEL
            ),
            spec=spec,
        )

    with pytest.raises(
        PerimeterCourtyardFootprintError,
        match="working_srid",
    ):
        strategy.create(
            _block_envelope(working_srid=32637),
            spec=spec,
        )


def test_perimeter_courtyard_specs_validate_metric_and_opening_contracts() -> None:
    with pytest.raises(PerimeterCourtyardFootprintError, match="greater than"):
        PerimeterCourtyardFootprintSpec.courtyard(
            edge_setback_m=1.0,
            wing_depth_m=0.0,
        )
    with pytest.raises(PerimeterCourtyardFootprintError, match="must not define"):
        PerimeterCourtyardFootprintSpec(
            kind=PerimeterCourtyardKind.COURTYARD,
            edge_setback_m=1.0,
            wing_depth_m=4.0,
            opening_width_m=4.0,
        )
    with pytest.raises(PerimeterCourtyardFootprintError, match="opening_width"):
        PerimeterCourtyardFootprintSpec(
            kind=PerimeterCourtyardKind.PERIMETER,
            edge_setback_m=1.0,
            wing_depth_m=4.0,
            opening_side=PerimeterOpeningSide.SOUTH,
        )
    with pytest.raises(ValueError, match="not projected"):
        PerimeterCourtyardFootprintStrategy(working_srid=4326)
