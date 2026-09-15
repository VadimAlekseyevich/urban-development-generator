from __future__ import annotations

import math

import numpy as np
import pytest

from core.urban_generator.domain import NetworkPoint
from core.urban_generator.roads import (
    CandidateRoadAnchor,
    LeastCostConnector,
    LeastCostConnectorPolicy,
    MSTBaselineConnector,
    RoadGrowthAttemptStatus,
    RoadGrowthIntent,
    RoadGrowthStopReason,
    RuleBasedRoadGrower,
    RuleBasedRoadGrowthError,
    RuleBasedRoadGrowthPolicy,
    RuleBasedRoadGrowthStrategy,
)
from core.urban_generator.suitability import (
    HardExclusionMask,
    SuitabilityGridSpec,
    WeightedSuitabilityResult,
)
from core.urban_generator.zoning import ZoneClass

_FINGERPRINT = "3" * 64


class CountingPairConnector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._delegate = LeastCostConnector()

    def connect(
        self,
        *,
        start: CandidateRoadAnchor,
        target: CandidateRoadAnchor,
        suitability: WeightedSuitabilityResult,
        hard_mask: HardExclusionMask,
        policy: LeastCostConnectorPolicy | None = None,
    ):
        self.calls.append((start.anchor_id, target.anchor_id))
        return self._delegate.connect(
            start=start,
            target=target,
            suitability=suitability,
            hard_mask=hard_mask,
            policy=policy,
        )


def make_surface(
    *,
    width: int,
    height: int,
    hard_cells: tuple[tuple[int, int], ...] = (),
) -> tuple[WeightedSuitabilityResult, HardExclusionMask]:
    grid = SuitabilityGridSpec(
        working_srid=32637,
        bounds=(0.0, 0.0, float(width * 10), float(height * 10)),
        width=width,
        height=height,
    )
    hard = np.zeros((height, width), dtype=np.bool_)
    for row, col in hard_cells:
        hard[row, col] = True
    valid = np.logical_not(hard)
    scores = np.ones((height, width), dtype=np.float64)
    scores[hard] = 0.0
    suitability = WeightedSuitabilityResult(
        grid=grid,
        scores=scores,
        valid_mask=valid,
        hard_excluded_mask=hard,
        config_version="growth-test-v1",
        config_fingerprint=_FINGERPRINT,
        factor_versions=(("synthetic", "1"),),
    )
    hard_mask = HardExclusionMask(
        grid=grid,
        excluded=hard,
        source_codes=("boundary", "synthetic_hard"),
    )
    return suitability, hard_mask


def make_anchor(
    suitability: WeightedSuitabilityResult,
    *,
    anchor_id: str,
    row: int,
    col: int,
    zone_class: ZoneClass = ZoneClass.RESIDENTIAL,
) -> CandidateRoadAnchor:
    grid = suitability.grid
    min_x, _min_y, _max_x, max_y = grid.bounds
    return CandidateRoadAnchor(
        anchor_id=anchor_id,
        point=NetworkPoint(
            x_m=min_x + (col + 0.5) * grid.cell_width_m,
            y_m=max_y - (row + 0.5) * grid.cell_height_m,
        ),
        zone_class=zone_class,
        zoning_cell_index=0,
        raster_row=row,
        raster_col=col,
        suitability_score=float(suitability.scores[row, col]),
    )


def make_baseline(
    *,
    anchors: tuple[CandidateRoadAnchor, ...],
    suitability: WeightedSuitabilityResult,
    hard_mask: HardExclusionMask,
):
    return MSTBaselineConnector().connect(
        anchors=anchors,
        suitability=suitability,
        hard_mask=hard_mask,
    )


def attempt_ids(result) -> tuple[tuple[str, str], ...]:
    return tuple(
        (attempt.start_anchor_id, attempt.target_anchor_id)
        for attempt in result.attempts
    )


def make_mixed_square():
    suitability, hard_mask = make_surface(width=2, height=2)
    anchors = (
        make_anchor(
            suitability,
            anchor_id="a",
            row=0,
            col=0,
            zone_class=ZoneClass.RESIDENTIAL,
        ),
        make_anchor(
            suitability,
            anchor_id="b",
            row=0,
            col=1,
            zone_class=ZoneClass.RESIDENTIAL,
        ),
        make_anchor(
            suitability,
            anchor_id="c",
            row=1,
            col=0,
            zone_class=ZoneClass.MIXED,
        ),
        make_anchor(
            suitability,
            anchor_id="d",
            row=1,
            col=1,
            zone_class=ZoneClass.MIXED,
        ),
    )
    baseline = make_baseline(
        anchors=anchors,
        suitability=suitability,
        hard_mask=hard_mask,
    )
    return suitability, hard_mask, anchors, baseline


def test_grower_satisfies_pluggable_strategy_protocol() -> None:
    assert isinstance(RuleBasedRoadGrower(), RuleBasedRoadGrowthStrategy)


def test_growth_is_deterministic_excludes_mst_and_alternates_intents() -> None:
    suitability, hard_mask, anchors, baseline = make_mixed_square()

    forward = RuleBasedRoadGrower().grow(
        anchors=anchors,
        baseline=baseline,
        suitability=suitability,
        hard_mask=hard_mask,
    )
    reversed_result = RuleBasedRoadGrower().grow(
        anchors=tuple(reversed(anchors)),
        baseline=baseline,
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert attempt_ids(forward) == (("a", "d"), ("c", "d"), ("b", "c"))
    assert attempt_ids(reversed_result) == attempt_ids(forward)
    assert tuple(item.intent for item in forward.attempts) == (
        RoadGrowthIntent.COLLECTOR,
        RoadGrowthIntent.LOCAL,
        RoadGrowthIntent.COLLECTOR,
    )
    baseline_pairs = {
        tuple(sorted((edge.start_anchor_id, edge.target_anchor_id)))
        for edge in baseline.edges
    }
    assert baseline_pairs.isdisjoint(set(attempt_ids(forward)))
    assert forward.diagnostics.candidate_pair_evaluation_count == 6
    assert forward.diagnostics.baseline_pair_count == 3
    assert forward.diagnostics.eligible_candidate_count == 3
    assert forward.diagnostics.added_edge_count == 3
    assert forward.diagnostics.added_local_count == 1
    assert forward.diagnostics.added_collector_count == 2
    assert forward.diagnostics.stop_reason is RoadGrowthStopReason.CANDIDATES_EXHAUSTED


def test_iteration_limit_bounds_expensive_pair_routing() -> None:
    suitability, hard_mask, anchors, baseline = make_mixed_square()
    pair_connector = CountingPairConnector()
    grower = RuleBasedRoadGrower(
        policy=RuleBasedRoadGrowthPolicy(max_iterations=1),
        pair_connector=pair_connector,
    )

    result = grower.grow(
        anchors=anchors,
        baseline=baseline,
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert pair_connector.calls == [("a", "d")]
    assert result.diagnostics.attempted_connection_count == 1
    assert result.diagnostics.stop_reason is RoadGrowthStopReason.ITERATION_LIMIT


def test_added_length_budget_stops_before_pair_that_cannot_fit() -> None:
    suitability, hard_mask, anchors, baseline = make_mixed_square()
    result = RuleBasedRoadGrower(
        policy=RuleBasedRoadGrowthPolicy(max_added_length_m=15.0)
    ).grow(
        anchors=anchors,
        baseline=baseline,
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert attempt_ids(result) == (("a", "d"),)
    assert result.attempts[0].status is RoadGrowthAttemptStatus.ADDED
    assert result.diagnostics.added_length_m == pytest.approx(math.sqrt(200.0))
    assert result.diagnostics.stop_reason is RoadGrowthStopReason.LENGTH_BUDGET


def test_connected_detour_can_be_rejected_by_remaining_length_budget() -> None:
    suitability, hard_mask = make_surface(
        width=3,
        height=3,
        hard_cells=((1, 1),),
    )
    anchors = (
        make_anchor(suitability, anchor_id="a", row=0, col=0),
        make_anchor(suitability, anchor_id="b", row=0, col=2),
        make_anchor(
            suitability,
            anchor_id="c",
            row=2,
            col=0,
            zone_class=ZoneClass.MIXED,
        ),
        make_anchor(
            suitability,
            anchor_id="d",
            row=2,
            col=2,
            zone_class=ZoneClass.MIXED,
        ),
    )
    baseline = make_baseline(
        anchors=anchors,
        suitability=suitability,
        hard_mask=hard_mask,
    )

    result = RuleBasedRoadGrower(
        policy=RuleBasedRoadGrowthPolicy(max_iterations=1, max_added_length_m=30.0)
    ).grow(
        anchors=anchors,
        baseline=baseline,
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert attempt_ids(result) == (("a", "d"),)
    assert result.attempts[0].direct_distance_m == pytest.approx(math.sqrt(800.0))
    assert result.attempts[0].connection.length_m == pytest.approx(40.0)
    assert result.attempts[0].status is RoadGrowthAttemptStatus.LENGTH_BUDGET_REJECTED
    assert result.diagnostics.added_length_m == 0.0
    assert result.diagnostics.length_budget_rejected_count == 1


def test_no_path_and_search_limit_are_preserved() -> None:
    suitability, hard_mask = make_surface(
        width=3,
        height=3,
        hard_cells=((0, 1), (1, 1), (2, 1)),
    )
    anchors = (
        make_anchor(suitability, anchor_id="a", row=0, col=0),
        make_anchor(suitability, anchor_id="b", row=0, col=2),
        make_anchor(suitability, anchor_id="c", row=2, col=0),
        make_anchor(suitability, anchor_id="d", row=2, col=2),
    )
    baseline = make_baseline(
        anchors=anchors,
        suitability=suitability,
        hard_mask=hard_mask,
    )

    no_path = RuleBasedRoadGrower(
        policy=RuleBasedRoadGrowthPolicy(max_iterations=1)
    ).grow(
        anchors=anchors,
        baseline=baseline,
        suitability=suitability,
        hard_mask=hard_mask,
    )
    assert attempt_ids(no_path) == (("c", "d"),)
    assert no_path.attempts[0].status is RoadGrowthAttemptStatus.NO_PATH
    assert no_path.diagnostics.no_path_count == 1

    open_surface, open_mask, open_anchors, open_baseline = make_mixed_square()
    limited = RuleBasedRoadGrower(
        policy=RuleBasedRoadGrowthPolicy(max_iterations=1)
    ).grow(
        anchors=open_anchors,
        baseline=open_baseline,
        suitability=open_surface,
        hard_mask=open_mask,
        pair_policy=LeastCostConnectorPolicy(max_visited_cells=1),
    )
    assert limited.attempts[0].status is RoadGrowthAttemptStatus.SEARCH_LIMIT_REACHED
    assert limited.diagnostics.search_limit_reached_count == 1


def test_empty_and_single_anchor_inputs_are_trivially_exhausted() -> None:
    suitability, hard_mask = make_surface(width=1, height=1)
    grower = RuleBasedRoadGrower()
    empty_baseline = make_baseline(
        anchors=(),
        suitability=suitability,
        hard_mask=hard_mask,
    )
    anchor = make_anchor(suitability, anchor_id="a", row=0, col=0)
    single_baseline = make_baseline(
        anchors=(anchor,),
        suitability=suitability,
        hard_mask=hard_mask,
    )

    empty = grower.grow(
        anchors=(),
        baseline=empty_baseline,
        suitability=suitability,
        hard_mask=hard_mask,
    )
    single = grower.grow(
        anchors=(anchor,),
        baseline=single_baseline,
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert empty.attempts == ()
    assert single.attempts == ()
    assert empty.diagnostics.stop_reason is RoadGrowthStopReason.CANDIDATES_EXHAUSTED
    assert single.diagnostics.stop_reason is RoadGrowthStopReason.CANDIDATES_EXHAUSTED


def test_candidate_pair_bound_is_checked_before_growth() -> None:
    suitability, hard_mask = make_surface(width=3, height=1)
    anchors = tuple(
        make_anchor(suitability, anchor_id=f"a{index}", row=0, col=index)
        for index in range(3)
    )
    baseline = make_baseline(
        anchors=anchors,
        suitability=suitability,
        hard_mask=hard_mask,
    )
    grower = RuleBasedRoadGrower(
        policy=RuleBasedRoadGrowthPolicy(max_candidate_pairs=2)
    )

    with pytest.raises(RuleBasedRoadGrowthError, match="candidate-pair limit exceeded"):
        grower.grow(
            anchors=anchors,
            baseline=baseline,
            suitability=suitability,
            hard_mask=hard_mask,
        )


def test_baseline_anchor_set_and_hard_mask_must_match() -> None:
    suitability, hard_mask = make_surface(width=3, height=1)
    anchors = tuple(
        make_anchor(suitability, anchor_id=f"a{index}", row=0, col=index)
        for index in range(3)
    )
    baseline = make_baseline(
        anchors=anchors[:2],
        suitability=suitability,
        hard_mask=hard_mask,
    )

    with pytest.raises(RuleBasedRoadGrowthError, match="baseline anchor_ids"):
        RuleBasedRoadGrower().grow(
            anchors=anchors,
            baseline=baseline,
            suitability=suitability,
            hard_mask=hard_mask,
        )

    valid_baseline = make_baseline(
        anchors=anchors,
        suitability=suitability,
        hard_mask=hard_mask,
    )
    mismatched = HardExclusionMask(
        grid=hard_mask.grid,
        excluded=np.asarray(((False, True, False),), dtype=np.bool_),
        source_codes=("boundary", "different"),
    )
    with pytest.raises(RuleBasedRoadGrowthError, match="must match hard_mask exactly"):
        RuleBasedRoadGrower().grow(
            anchors=anchors,
            baseline=valid_baseline,
            suitability=suitability,
            hard_mask=mismatched,
        )
