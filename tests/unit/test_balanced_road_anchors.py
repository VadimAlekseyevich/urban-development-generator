"""Balanced road sampling spreads equal-score candidates without exceeding limits."""

import pytest

from core.urban_generator.domain import NetworkPoint
from core.urban_generator.roads.candidate_anchors import (
    CandidateRoadAnchorError,
    CandidateRoadAnchorPolicy,
    _EligibleAnchor,
    _spatially_balanced,
)
from core.urban_generator.zoning import ZoneClass


def test_balanced_sampling_reaches_all_corners_of_flat_grid() -> None:
    candidates = [
        _EligibleAnchor(r, c, NetworkPoint(x_m=c * 10, y_m=r * 10),
                        0, ZoneClass.RESIDENTIAL, 1.0)
        for r in range(10) for c in range(10)
    ]
    selected = _spatially_balanced(candidates, 4)
    assert {(item.row, item.col) for item in selected} == {
        (0, 0), (0, 9), (9, 0), (9, 9),
    }
    assert selected == _spatially_balanced(candidates, 4)
    assert len(_spatially_balanced(candidates, 100)) == 100


def test_balanced_policy_has_explicit_work_limit() -> None:
    with pytest.raises(CandidateRoadAnchorError, match="work budget"):
        CandidateRoadAnchorPolicy(spatially_balanced=True, max_candidates=129)
    with pytest.raises(CandidateRoadAnchorError, match="work budget"):
        CandidateRoadAnchorPolicy(spatially_balanced=True, max_sampled_cells=250001)
    assert CandidateRoadAnchorPolicy().spatially_balanced is False
