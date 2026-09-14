import math

import pytest

from core.urban_generator.domain import NetworkPoint
from core.urban_generator.roads import (
    SpatialSnapIndex,
    SpatialSnapMatch,
    SpatialSnapTarget,
    SpatialSnappingError,
)

WORKING_SRID = 32637


def _target(target_id: str, x_m: float, y_m: float) -> SpatialSnapTarget:
    return SpatialSnapTarget(
        target_id=target_id,
        point=NetworkPoint(x_m=x_m, y_m=y_m),
    )


def test_index_returns_only_tolerance_candidates_ordered_by_distance_then_id() -> None:
    index = SpatialSnapIndex(
        targets=(
            _target("endpoint-a", 0.0, 0.0),
            _target("intersection-b", 5.0, 0.0),
            _target("endpoint-c", 10.0, 0.0),
        ),
        working_srid=WORKING_SRID,
    )

    matches = index.candidates(
        NetworkPoint(x_m=4.0, y_m=0.0),
        tolerance_m=4.0,
    )

    assert matches == (
        SpatialSnapMatch(target=_target("intersection-b", 5.0, 0.0), distance_m=1.0),
        SpatialSnapMatch(target=_target("endpoint-a", 0.0, 0.0), distance_m=4.0),
    )
    assert index.target_count == 3
    assert index.working_srid == WORKING_SRID


def test_snap_is_deterministic_for_equal_distance_targets_regardless_of_input_order() -> None:
    targets = (
        _target("z-endpoint", -1.0, 0.0),
        _target("a-intersection", 1.0, 0.0),
    )
    forward = SpatialSnapIndex(targets=targets, working_srid=WORKING_SRID)
    reverse = SpatialSnapIndex(targets=tuple(reversed(targets)), working_srid=WORKING_SRID)
    point = NetworkPoint(x_m=0.0, y_m=0.0)

    assert forward.snap(point, tolerance_m=1.0) == SpatialSnapMatch(
        target=_target("a-intersection", 1.0, 0.0),
        distance_m=1.0,
    )
    assert reverse.snap(point, tolerance_m=1.0) == forward.snap(point, tolerance_m=1.0)


def test_snap_target_excludes_itself_for_endpoint_to_endpoint_snapping() -> None:
    index = SpatialSnapIndex(
        targets=(
            _target("endpoint-a", 0.0, 0.0),
            _target("endpoint-b", 0.75, 0.0),
            _target("endpoint-c", 5.0, 0.0),
        ),
        working_srid=WORKING_SRID,
    )

    assert index.snap_target("endpoint-a", tolerance_m=0.8) == SpatialSnapMatch(
        target=_target("endpoint-b", 0.75, 0.0),
        distance_m=0.75,
    )
    assert index.snap_target("endpoint-a", tolerance_m=0.5) is None


def test_zero_tolerance_supports_exact_duplicate_coordinate_snapping() -> None:
    index = SpatialSnapIndex(
        targets=(
            _target("z-node", 12.0, 7.0),
            _target("a-node", 12.0, 7.0),
        ),
        working_srid=WORKING_SRID,
    )

    match = index.snap(NetworkPoint(x_m=12.0, y_m=7.0), tolerance_m=0.0)

    assert match == SpatialSnapMatch(
        target=_target("a-node", 12.0, 7.0),
        distance_m=0.0,
    )


def test_empty_index_returns_no_candidates_or_snap() -> None:
    index = SpatialSnapIndex(targets=(), working_srid=WORKING_SRID)
    point = NetworkPoint(x_m=1.0, y_m=2.0)

    assert index.candidates(point, tolerance_m=10.0) == ()
    assert index.snap(point, tolerance_m=10.0) is None


def test_index_enforces_immutable_unique_bounded_targets() -> None:
    with pytest.raises(SpatialSnappingError, match="immutable tuple"):
        SpatialSnapIndex(  # type: ignore[arg-type]
            targets=[_target("a", 0.0, 0.0)],
            working_srid=WORKING_SRID,
        )

    with pytest.raises(SpatialSnappingError, match="duplicate snap target_id"):
        SpatialSnapIndex(
            targets=(_target("a", 0.0, 0.0), _target("a", 1.0, 0.0)),
            working_srid=WORKING_SRID,
        )

    with pytest.raises(SpatialSnappingError, match="snap target limit exceeded: 2 > 1"):
        SpatialSnapIndex(
            targets=(_target("a", 0.0, 0.0), _target("b", 1.0, 0.0)),
            working_srid=WORKING_SRID,
            max_targets=1,
        )


@pytest.mark.parametrize("tolerance_m", [-1.0, math.inf, math.nan, True])
def test_snap_rejects_invalid_metric_tolerance(tolerance_m: float) -> None:
    index = SpatialSnapIndex(
        targets=(_target("a", 0.0, 0.0),),
        working_srid=WORKING_SRID,
    )

    with pytest.raises(SpatialSnappingError, match="tolerance_m"):
        index.snap(NetworkPoint(x_m=0.0, y_m=0.0), tolerance_m=tolerance_m)


def test_snap_validates_exclusions_and_unknown_target_ids() -> None:
    index = SpatialSnapIndex(
        targets=(_target("a", 0.0, 0.0), _target("b", 1.0, 0.0)),
        working_srid=WORKING_SRID,
    )

    with pytest.raises(SpatialSnappingError, match="frozenset"):
        index.snap(  # type: ignore[arg-type]
            NetworkPoint(x_m=0.0, y_m=0.0),
            tolerance_m=2.0,
            exclude_target_ids={"a"},
        )

    with pytest.raises(SpatialSnappingError, match="unknown snap target_id"):
        index.snap_target("missing", tolerance_m=2.0)
