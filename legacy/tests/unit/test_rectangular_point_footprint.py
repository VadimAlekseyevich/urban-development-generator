from __future__ import annotations

import pytest
from shapely.geometry import Point, box

from core.urban_generator.buildings import (
    BuildingEnvelopeResult,
    BuildingEnvelopeSourceKind,
    BuildingEnvelopeStatus,
    BuildingFootprintError,
    BuildingFootprintStatus,
    BuildingPlacementCandidate,
    BuildingPlacementCandidateKind,
    RectangularPointFootprintKind,
    RectangularPointFootprintSpec,
    RectangularPointFootprintStrategy,
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
    y: float = 10.0,
    source_id: str = "parcel:test",
    kind: BuildingPlacementCandidateKind = BuildingPlacementCandidateKind.GRID,
) -> BuildingPlacementCandidate:
    return BuildingPlacementCandidate(
        candidate_id="candidate:00000001",
        source_id=source_id,
        kind=kind,
        point=Point(x, y),
        frontage_road_id=(
            "road:front"
            if kind is BuildingPlacementCandidateKind.FRONTAGE
            else None
        ),
    )


def test_rectangle_footprint_is_axis_aligned_centered_and_deterministic() -> None:
    strategy = RectangularPointFootprintStrategy(working_srid=WORKING_SRID)
    spec = RectangularPointFootprintSpec.rectangle(
        width_m=12.0,
        depth_m=8.0,
    )

    first = strategy.create(
        _candidate(),
        envelope=_envelope(),
        spec=spec,
    )
    second = strategy.create(
        _candidate(),
        envelope=_envelope(),
        spec=spec,
    )

    assert first == second
    assert first.status is BuildingFootprintStatus.READY
    assert first.is_ready is True
    assert first.footprint_geometry is not None
    assert first.proposed_geometry.equals(box(14, 6, 26, 14))
    assert first.area_m2 == pytest.approx(96.0)
    assert spec.area_m2 == pytest.approx(96.0)
    assert first.kind is RectangularPointFootprintKind.RECTANGLE
    assert first.placement_candidate_id == "candidate:00000001"


def test_point_footprint_is_square_polygon_not_zero_area_point() -> None:
    spec = RectangularPointFootprintSpec.point(size_m=6.0)
    result = RectangularPointFootprintStrategy(
        working_srid=WORKING_SRID,
    ).create(
        _candidate(),
        envelope=_envelope(),
        spec=spec,
    )

    assert spec.kind is RectangularPointFootprintKind.POINT
    assert spec.width_m == pytest.approx(6.0)
    assert spec.depth_m == pytest.approx(6.0)
    assert result.status is BuildingFootprintStatus.READY
    assert result.proposed_geometry.equals(box(17, 7, 23, 13))
    assert result.area_m2 == pytest.approx(36.0)


def test_outside_envelope_is_explicit_reject_without_clipping_or_shifting() -> None:
    candidate = _candidate(
        x=0.0,
        y=0.0,
        kind=BuildingPlacementCandidateKind.FRONTAGE,
    )
    spec = RectangularPointFootprintSpec.rectangle(
        width_m=10.0,
        depth_m=8.0,
    )

    result = RectangularPointFootprintStrategy(
        working_srid=WORKING_SRID,
    ).create(
        candidate,
        envelope=_envelope(),
        spec=spec,
    )

    assert result.status is BuildingFootprintStatus.OUTSIDE_ENVELOPE
    assert result.is_ready is False
    assert result.footprint_geometry is None
    assert result.proposed_geometry.equals(box(-5, -4, 5, 4))


def test_irregular_envelope_requires_full_coverage_not_centroid_only() -> None:
    envelope = _envelope(
        box(0, 0, 20, 20).difference(box(10, 10, 20, 20))
    )
    candidate = _candidate(x=9.0, y=9.0)
    result = RectangularPointFootprintStrategy(
        working_srid=WORKING_SRID,
    ).create(
        candidate,
        envelope=envelope,
        spec=RectangularPointFootprintSpec.rectangle(
            width_m=4.0,
            depth_m=4.0,
        ),
    )

    assert envelope.buildable_geometry is not None
    assert envelope.buildable_geometry.covers(candidate.point)
    assert result.status is BuildingFootprintStatus.OUTSIDE_ENVELOPE


def test_strategy_rejects_non_ready_mismatched_source_and_crs() -> None:
    strategy = RectangularPointFootprintStrategy(working_srid=WORKING_SRID)
    spec = RectangularPointFootprintSpec.point(size_m=4.0)

    with pytest.raises(BuildingFootprintError, match="READY"):
        strategy.create(
            _candidate(),
            envelope=_envelope(
                status=BuildingEnvelopeStatus.BELOW_MINIMUM_AREA
            ),
            spec=spec,
        )

    with pytest.raises(BuildingFootprintError, match="source_id"):
        strategy.create(
            _candidate(source_id="parcel:other"),
            envelope=_envelope(),
            spec=spec,
        )

    with pytest.raises(BuildingFootprintError, match="working_srid"):
        strategy.create(
            _candidate(),
            envelope=_envelope(working_srid=32637),
            spec=spec,
        )


def test_footprint_specs_validate_metric_dimensions_and_point_shape() -> None:
    with pytest.raises(BuildingFootprintError, match="positive finite"):
        RectangularPointFootprintSpec.rectangle(
            width_m=0.0,
            depth_m=4.0,
        )
    with pytest.raises(BuildingFootprintError, match="equal"):
        RectangularPointFootprintSpec(
            kind=RectangularPointFootprintKind.POINT,
            width_m=6.0,
            depth_m=4.0,
        )
    with pytest.raises(ValueError, match="not projected"):
        RectangularPointFootprintStrategy(working_srid=4326)
