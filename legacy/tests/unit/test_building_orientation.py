from __future__ import annotations

import pytest
from shapely.geometry import LineString, Point, box

from core.urban_generator.buildings import (
    BuildingEnvelopeResult,
    BuildingEnvelopeSourceKind,
    BuildingEnvelopeStatus,
    BuildingOrientationAxis,
    BuildingOrientationError,
    BuildingOrientationPolicy,
    BuildingOrientationSource,
    BuildingOrientationStrategy,
    BuildingPlacementCandidate,
    BuildingPlacementCandidateKind,
)

WORKING_SRID = 3857


def _envelope(geometry=None) -> BuildingEnvelopeResult:
    resolved = geometry if geometry is not None else box(0, 0, 40, 20)
    return BuildingEnvelopeResult(
        source_id="parcel:test",
        source_kind=BuildingEnvelopeSourceKind.PARCEL,
        working_srid=WORKING_SRID,
        status=BuildingEnvelopeStatus.READY,
        candidate_geometry=resolved,
        constraint_reports=(),
    )


def _candidate(
    *,
    x: float = 20.0,
    y: float = 10.0,
    kind: BuildingPlacementCandidateKind = BuildingPlacementCandidateKind.GRID,
    frontage_road_id: str | None = None,
) -> BuildingPlacementCandidate:
    return BuildingPlacementCandidate(
        candidate_id="candidate:00000001",
        source_id="parcel:test",
        kind=kind,
        point=Point(x, y),
        frontage_road_id=frontage_road_id,
    )


def _axis(
    source: BuildingOrientationSource,
    source_ref: str,
    coordinates: tuple[tuple[float, float], tuple[float, float]],
) -> BuildingOrientationAxis:
    return BuildingOrientationAxis(
        source=source,
        source_ref=source_ref,
        geometry=LineString(coordinates),
        working_srid=WORKING_SRID,
    )


def test_frontage_candidate_prefers_matching_frontage_axis() -> None:
    strategy = BuildingOrientationStrategy(working_srid=WORKING_SRID)
    candidate = _candidate(
        kind=BuildingPlacementCandidateKind.FRONTAGE,
        frontage_road_id="road:front",
        y=0.0,
    )
    matching = _axis(
        BuildingOrientationSource.FRONTAGE,
        "road:front",
        ((0, 0), (40, 0)),
    )
    other = _axis(
        BuildingOrientationSource.FRONTAGE,
        "road:other",
        ((0, 20), (40, 20)),
    )
    road = _axis(
        BuildingOrientationSource.ROAD,
        "road:near",
        ((20, -10), (20, 30)),
    )

    result = strategy.orient(
        candidate,
        envelope=_envelope(),
        frontage_axes=(other, matching),
        road_axes=(road,),
    )

    assert result.source is BuildingOrientationSource.FRONTAGE
    assert result.source_ref == "road:front"
    assert result.angle_degrees == pytest.approx(0.0)
    assert result.distance_to_anchor_m == pytest.approx(0.0)


def test_grid_candidate_uses_nearest_road_axis_deterministically() -> None:
    strategy = BuildingOrientationStrategy(working_srid=WORKING_SRID)
    near = _axis(
        BuildingOrientationSource.ROAD,
        "road:near",
        ((0, 8), (40, 8)),
    )
    far = _axis(
        BuildingOrientationSource.ROAD,
        "road:far",
        ((0, 0), (40, 0)),
    )

    first = strategy.orient(
        _candidate(),
        envelope=_envelope(),
        road_axes=(far, near),
    )
    second = strategy.orient(
        _candidate(),
        envelope=_envelope(),
        road_axes=(near, far),
    )

    assert first == second
    assert first.source is BuildingOrientationSource.ROAD
    assert first.source_ref == "road:near"
    assert first.distance_to_anchor_m == pytest.approx(2.0)
    assert first.angle_degrees == pytest.approx(0.0)


def test_principal_axis_fallback_uses_longest_rotated_envelope_axis() -> None:
    strategy = BuildingOrientationStrategy(working_srid=WORKING_SRID)

    result = strategy.orient(
        _candidate(),
        envelope=_envelope(box(0, 0, 60, 20)),
    )

    assert result.source is BuildingOrientationSource.PRINCIPAL_AXIS
    assert result.source_ref == "parcel:test"
    assert result.angle_degrees == pytest.approx(0.0)
    assert result.axis.length == pytest.approx(60.0)
    assert result.axis.centroid.equals(Point(30, 10))


def test_axis_distance_policy_falls_back_to_principal_axis() -> None:
    strategy = BuildingOrientationStrategy(
        working_srid=WORKING_SRID,
        policy=BuildingOrientationPolicy(max_axis_distance_m=3.0),
    )
    road = _axis(
        BuildingOrientationSource.ROAD,
        "road:far",
        ((0, 0), (40, 0)),
    )

    result = strategy.orient(
        _candidate(y=10.0),
        envelope=_envelope(),
        road_axes=(road,),
    )

    assert result.source is BuildingOrientationSource.PRINCIPAL_AXIS


def test_orientation_normalizes_reverse_and_diagonal_angles() -> None:
    strategy = BuildingOrientationStrategy(working_srid=WORKING_SRID)
    reverse = _axis(
        BuildingOrientationSource.ROAD,
        "road:reverse",
        ((40, 10), (0, 10)),
    )
    diagonal = _axis(
        BuildingOrientationSource.ROAD,
        "road:diag",
        ((0, 0), (20, 20)),
    )

    reverse_result = strategy.orient(
        _candidate(),
        envelope=_envelope(),
        road_axes=(reverse,),
    )
    diagonal_result = strategy.orient(
        _candidate(x=10, y=10),
        envelope=_envelope(),
        road_axes=(diagonal,),
    )

    assert reverse_result.angle_degrees == pytest.approx(0.0)
    assert diagonal_result.angle_degrees == pytest.approx(45.0)


def test_orientation_rejects_non_ready_mismatched_source_crs_and_axis_overflow() -> None:
    strategy = BuildingOrientationStrategy(
        working_srid=WORKING_SRID,
        max_axes=1,
    )
    blocked = BuildingEnvelopeResult(
        source_id="parcel:test",
        source_kind=BuildingEnvelopeSourceKind.PARCEL,
        working_srid=WORKING_SRID,
        status=BuildingEnvelopeStatus.BELOW_MINIMUM_AREA,
        candidate_geometry=box(0, 0, 10, 10),
        constraint_reports=(),
    )

    with pytest.raises(BuildingOrientationError, match="READY"):
        strategy.orient(_candidate(), envelope=blocked)

    other_candidate = BuildingPlacementCandidate(
        candidate_id="candidate:other",
        source_id="parcel:other",
        kind=BuildingPlacementCandidateKind.GRID,
        point=Point(5, 5),
    )
    with pytest.raises(BuildingOrientationError, match="source_id"):
        strategy.orient(other_candidate, envelope=_envelope())

    wrong_crs = BuildingOrientationAxis(
        source=BuildingOrientationSource.ROAD,
        source_ref="road:wrong",
        geometry=LineString(((0, 0), (10, 0))),
        working_srid=32637,
    )
    with pytest.raises(BuildingOrientationError, match="working_srid"):
        strategy.orient(
            _candidate(),
            envelope=_envelope(),
            road_axes=(wrong_crs,),
        )

    axes = (
        _axis(
            BuildingOrientationSource.ROAD,
            "road:1",
            ((0, 0), (10, 0)),
        ),
        _axis(
            BuildingOrientationSource.ROAD,
            "road:2",
            ((0, 1), (10, 1)),
        ),
    )
    with pytest.raises(BuildingOrientationError, match="axis limit exceeded"):
        strategy.orient(
            _candidate(),
            envelope=_envelope(),
            road_axes=axes,
        )


def test_orientation_policy_and_axis_contract_validate_metric_inputs() -> None:
    with pytest.raises(BuildingOrientationError, match="finite non-negative"):
        BuildingOrientationPolicy(max_axis_distance_m=-1.0)
    with pytest.raises(BuildingOrientationError, match="positive integer"):
        BuildingOrientationStrategy(
            working_srid=WORKING_SRID,
            max_axes=0,
        )
    with pytest.raises(ValueError, match="not projected"):
        BuildingOrientationStrategy(working_srid=4326)
