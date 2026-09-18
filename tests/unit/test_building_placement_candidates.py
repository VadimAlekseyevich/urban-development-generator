from __future__ import annotations

import pytest
from shapely.geometry import LineString, MultiPolygon, box

from core.urban_generator.buildings import (
    BuildingEnvelopeResult,
    BuildingEnvelopeSourceKind,
    BuildingEnvelopeStatus,
    BuildingPlacementCandidateError,
    BuildingPlacementCandidateGenerator,
    BuildingPlacementCandidateKind,
    BuildingPlacementCandidatePolicy,
    BuildingPlacementFrontage,
)


WORKING_SRID = 3857


def _ready_envelope(geometry=None) -> BuildingEnvelopeResult:
    resolved_geometry = geometry if geometry is not None else box(0, 0, 40, 20)
    return BuildingEnvelopeResult(
        source_id="parcel:test",
        source_kind=BuildingEnvelopeSourceKind.PARCEL,
        working_srid=WORKING_SRID,
        status=BuildingEnvelopeStatus.READY,
        candidate_geometry=resolved_geometry,
        constraint_reports=(),
    )


def test_grid_candidates_are_deterministic_and_inside_ready_envelope() -> None:
    generator = BuildingPlacementCandidateGenerator(
        working_srid=WORKING_SRID,
        policy=BuildingPlacementCandidatePolicy(
            grid_spacing_m=10.0,
            frontage_spacing_m=10.0,
            include_frontage=False,
        ),
    )

    first = generator.generate(_ready_envelope())
    second = generator.generate(_ready_envelope())

    assert first == second
    assert first.diagnostics.grid_scan_cell_count == 8
    assert first.diagnostics.grid_candidate_count == 8
    assert first.diagnostics.frontage_candidate_count == 0
    assert tuple(candidate.candidate_id for candidate in first.candidates) == tuple(
        f"candidate:{index:08d}" for index in range(8)
    )
    assert all(
        candidate.kind is BuildingPlacementCandidateKind.GRID
        for candidate in first.candidates
    )
    assert all(
        first.candidates[0].source_id == candidate.source_id
        for candidate in first.candidates
    )
    assert all(
        _ready_envelope().candidate_geometry.covers(candidate.point)
        for candidate in first.candidates
    )


def test_frontage_candidates_sample_road_backed_boundary_deterministically() -> None:
    envelope = _ready_envelope()
    frontage = BuildingPlacementFrontage(
        road_id="road:front",
        geometry=LineString(((0, 0), (40, 0))),
    )
    generator = BuildingPlacementCandidateGenerator(
        working_srid=WORKING_SRID,
        policy=BuildingPlacementCandidatePolicy(
            grid_spacing_m=10.0,
            frontage_spacing_m=10.0,
            include_grid=False,
        ),
    )

    result = generator.generate(envelope, frontages=(frontage,))

    assert result.diagnostics.frontage_sample_count == 4
    assert result.diagnostics.frontage_candidate_count == 4
    assert [candidate.point.x for candidate in result.candidates] == pytest.approx(
        [5.0, 15.0, 25.0, 35.0]
    )
    assert [candidate.point.y for candidate in result.candidates] == pytest.approx(
        [0.0, 0.0, 0.0, 0.0]
    )
    assert all(
        candidate.kind is BuildingPlacementCandidateKind.FRONTAGE
        for candidate in result.candidates
    )
    assert all(
        candidate.frontage_road_id == "road:front"
        for candidate in result.candidates
    )


def test_combined_candidates_have_stable_strategy_then_coordinate_order() -> None:
    envelope = _ready_envelope(box(0, 0, 20, 20))
    frontages = (
        BuildingPlacementFrontage(
            road_id="road:right",
            geometry=LineString(((20, 0), (20, 20))),
        ),
        BuildingPlacementFrontage(
            road_id="road:bottom",
            geometry=LineString(((0, 0), (20, 0))),
        ),
    )
    generator = BuildingPlacementCandidateGenerator(
        working_srid=WORKING_SRID,
        policy=BuildingPlacementCandidatePolicy(
            grid_spacing_m=10.0,
            frontage_spacing_m=10.0,
        ),
    )

    first = generator.generate(envelope, frontages=frontages)
    second = generator.generate(envelope, frontages=tuple(reversed(frontages)))

    assert first == second
    assert [candidate.kind for candidate in first.candidates[:4]] == [
        BuildingPlacementCandidateKind.GRID,
    ] * 4
    assert [candidate.kind for candidate in first.candidates[4:]] == [
        BuildingPlacementCandidateKind.FRONTAGE,
    ] * 4


def test_small_envelope_uses_representative_grid_fallback() -> None:
    result = BuildingPlacementCandidateGenerator(
        working_srid=WORKING_SRID,
        policy=BuildingPlacementCandidatePolicy(
            grid_spacing_m=100.0,
            frontage_spacing_m=10.0,
            include_frontage=False,
        ),
    ).generate(_ready_envelope(box(0, 0, 5, 4)))

    assert len(result.candidates) == 1
    assert result.candidates[0].point.x == pytest.approx(2.5)
    assert result.candidates[0].point.y == pytest.approx(2.0)


def test_non_ready_envelope_and_off_boundary_frontage_are_rejected() -> None:
    blocked = BuildingEnvelopeResult(
        source_id="parcel:test",
        source_kind=BuildingEnvelopeSourceKind.PARCEL,
        working_srid=WORKING_SRID,
        status=BuildingEnvelopeStatus.BELOW_MINIMUM_AREA,
        candidate_geometry=box(0, 0, 20, 20),
        constraint_reports=(),
    )
    generator = BuildingPlacementCandidateGenerator(
        working_srid=WORKING_SRID,
    )

    with pytest.raises(BuildingPlacementCandidateError, match="READY"):
        generator.generate(blocked)

    with pytest.raises(BuildingPlacementCandidateError, match="must lie on envelope boundary"):
        generator.generate(
            _ready_envelope(),
            frontages=(
                BuildingPlacementFrontage(
                    road_id="road:inside",
                    geometry=LineString(((5, 5), (15, 5))),
                ),
            ),
        )


def test_grid_frontage_and_total_candidate_work_is_bounded() -> None:
    with pytest.raises(BuildingPlacementCandidateError, match="grid scan cell limit"):
        BuildingPlacementCandidateGenerator(
            working_srid=WORKING_SRID,
            policy=BuildingPlacementCandidatePolicy(
                grid_spacing_m=1.0,
                frontage_spacing_m=10.0,
                include_frontage=False,
            ),
            max_grid_scan_cells=10,
        ).generate(_ready_envelope(box(0, 0, 20, 20)))

    frontage = BuildingPlacementFrontage(
        road_id="road:front",
        geometry=LineString(((0, 0), (40, 0))),
    )
    with pytest.raises(BuildingPlacementCandidateError, match="frontage sample limit"):
        BuildingPlacementCandidateGenerator(
            working_srid=WORKING_SRID,
            policy=BuildingPlacementCandidatePolicy(
                grid_spacing_m=10.0,
                frontage_spacing_m=1.0,
                include_grid=False,
            ),
            max_frontage_samples=10,
        ).generate(_ready_envelope(), frontages=(frontage,))

    with pytest.raises(BuildingPlacementCandidateError, match="candidate limit exceeded"):
        BuildingPlacementCandidateGenerator(
            working_srid=WORKING_SRID,
            policy=BuildingPlacementCandidatePolicy(
                grid_spacing_m=10.0,
                frontage_spacing_m=10.0,
            ),
            max_candidates=4,
        ).generate(_ready_envelope(), frontages=(frontage,))


def test_candidate_policy_and_geometry_contracts_reject_invalid_inputs() -> None:
    with pytest.raises(BuildingPlacementCandidateError, match="positive finite"):
        BuildingPlacementCandidatePolicy(grid_spacing_m=0.0)
    with pytest.raises(BuildingPlacementCandidateError, match="at least one"):
        BuildingPlacementCandidatePolicy(
            include_grid=False,
            include_frontage=False,
        )
    with pytest.raises(BuildingPlacementCandidateError, match="LineString"):
        BuildingPlacementFrontage(
            road_id="road:test",
            geometry=box(0, 0, 1, 1),
        )

    multipolygon = MultiPolygon((box(0, 0, 2, 2), box(10, 0, 12, 2)))
    result = BuildingPlacementCandidateGenerator(
        working_srid=WORKING_SRID,
        policy=BuildingPlacementCandidatePolicy(
            grid_spacing_m=10.0,
            frontage_spacing_m=10.0,
            include_frontage=False,
        ),
    ).generate(_ready_envelope(multipolygon))
    assert len(result.candidates) >= 1
