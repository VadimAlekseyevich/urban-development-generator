from __future__ import annotations

import pytest
from shapely.geometry import Point, box

from core.urban_generator.buildings import (
    BuildingSpacingCandidateLimitError,
    BuildingSpacingError,
    BuildingSpacingIndex,
    BuildingSpacingPolicy,
    BuildingSpacingSubject,
    PlacedBuildingFootprint,
)

WORKING_SRID = 3857


def _placed(
    building_id: str,
    geometry,
    *,
    working_srid: int = WORKING_SRID,
) -> PlacedBuildingFootprint:
    return PlacedBuildingFootprint(
        building_id=building_id,
        geometry=geometry,
        working_srid=working_srid,
    )


def _subject(
    geometry,
    *,
    working_srid: int = WORKING_SRID,
) -> BuildingSpacingSubject:
    return BuildingSpacingSubject(
        geometry=geometry,
        working_srid=working_srid,
    )


def test_overlap_is_rejected_even_when_minimum_gap_is_zero() -> None:
    index = BuildingSpacingIndex(
        footprints=(
            _placed("building:a", box(0, 0, 10, 10)),
        ),
        working_srid=WORKING_SRID,
    )

    hit = index.check(_subject(box(8, 0, 18, 10)))

    assert hit is not None
    assert hit.building_id == "building:a"
    assert hit.overlaps is True
    assert hit.overlap_area_m2 == pytest.approx(20.0)
    assert hit.actual_distance_m == pytest.approx(0.0)
    assert hit.required_gap_m == pytest.approx(0.0)
    assert index.allows(_subject(box(20, 0, 30, 10))) is True


def test_positive_minimum_gap_rejects_nearby_non_overlapping_footprint() -> None:
    index = BuildingSpacingIndex(
        footprints=(
            _placed("building:a", box(0, 0, 10, 10)),
        ),
        working_srid=WORKING_SRID,
        policy=BuildingSpacingPolicy(minimum_gap_m=5.0),
    )

    hit = index.check(_subject(box(13, 0, 23, 10)))

    assert hit is not None
    assert hit.overlaps is False
    assert hit.actual_distance_m == pytest.approx(3.0)
    assert hit.required_gap_m == pytest.approx(5.0)

    assert index.check(_subject(box(15, 0, 25, 10))) is None


def test_touching_is_allowed_at_zero_gap_and_rejected_for_positive_gap() -> None:
    footprints = (
        _placed("building:a", box(0, 0, 10, 10)),
    )
    subject = _subject(box(10, 0, 20, 10))

    zero_gap = BuildingSpacingIndex(
        footprints=footprints,
        working_srid=WORKING_SRID,
    )
    positive_gap = BuildingSpacingIndex(
        footprints=footprints,
        working_srid=WORKING_SRID,
        policy=BuildingSpacingPolicy(minimum_gap_m=0.5),
    )

    assert zero_gap.check(subject) is None
    positive_hit = positive_gap.check(subject)
    assert positive_hit is not None
    assert positive_hit.overlaps is False
    assert positive_hit.actual_distance_m == pytest.approx(0.0)


def test_hit_selection_is_deterministic_and_prioritizes_overlap() -> None:
    index = BuildingSpacingIndex(
        footprints=(
            _placed("building:z-near", box(12, 0, 22, 10)),
            _placed("building:b-overlap", box(8, 0, 11, 10)),
            _placed("building:a-overlap", box(9, 0, 12, 10)),
        ),
        working_srid=WORKING_SRID,
        policy=BuildingSpacingPolicy(minimum_gap_m=5.0),
    )

    hit = index.check(_subject(box(0, 0, 10, 10)))

    assert hit is not None
    assert hit.overlaps is True
    assert hit.building_id == "building:a-overlap"


def test_spatial_prefilter_avoids_scanning_far_footprints() -> None:
    far = tuple(
        _placed(
            f"building:far:{index:03d}",
            box(1000 + index * 20, 1000, 1010 + index * 20, 1010),
        )
        for index in range(100)
    )
    near = _placed("building:near", box(12, 0, 22, 10))
    index = BuildingSpacingIndex(
        footprints=far + (near,),
        working_srid=WORKING_SRID,
        policy=BuildingSpacingPolicy(minimum_gap_m=5.0),
        max_candidates=1,
    )

    hit = index.check(_subject(box(0, 0, 10, 10)))

    assert hit is not None
    assert hit.building_id == "building:near"


def test_candidate_scan_and_total_index_size_are_bounded() -> None:
    overlapping = tuple(
        _placed(
            f"building:{index:03d}",
            box(0, 0, 10, 10),
        )
        for index in range(3)
    )
    index = BuildingSpacingIndex(
        footprints=overlapping,
        working_srid=WORKING_SRID,
        max_candidates=2,
    )

    with pytest.raises(
        BuildingSpacingCandidateLimitError,
        match="candidate limit exceeded",
    ):
        index.check(_subject(box(0, 0, 10, 10)))

    with pytest.raises(BuildingSpacingError, match="footprint limit exceeded"):
        BuildingSpacingIndex(
            footprints=overlapping,
            working_srid=WORKING_SRID,
            max_footprints=2,
        )


def test_spacing_contract_validates_crs_ids_geometry_and_policy() -> None:
    with pytest.raises(BuildingSpacingError, match="working_srid"):
        BuildingSpacingIndex(
            footprints=(
                _placed(
                    "building:wrong",
                    box(0, 0, 10, 10),
                    working_srid=32637,
                ),
            ),
            working_srid=WORKING_SRID,
        )

    index = BuildingSpacingIndex(
        footprints=(),
        working_srid=WORKING_SRID,
    )
    with pytest.raises(BuildingSpacingError, match="working_srid"):
        index.check(
            _subject(
                box(0, 0, 10, 10),
                working_srid=32637,
            )
        )

    with pytest.raises(BuildingSpacingError, match="finite non-negative"):
        BuildingSpacingPolicy(minimum_gap_m=-1.0)

    with pytest.raises(BuildingSpacingError, match="Polygon"):
        _placed("building:point", Point(0, 0))

    with pytest.raises(ValueError, match="not projected"):
        BuildingSpacingIndex(
            footprints=(),
            working_srid=4326,
        )
