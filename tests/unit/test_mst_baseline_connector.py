from __future__ import annotations

import math

import numpy as np
import pytest

from core.urban_generator.domain import NetworkPoint
from core.urban_generator.roads import (
    AnchorConnectivityError,
    AnchorConnectivityStrategy,
    CandidateRoadAnchor,
    LeastCostConnectionStatus,
    LeastCostConnector,
    LeastCostConnectorPolicy,
    MSTBaselineConnector,
    MSTBaselineConnectorPolicy,
)
from core.urban_generator.suitability import (
    HardExclusionMask,
    SuitabilityGridSpec,
    WeightedSuitabilityResult,
)
from core.urban_generator.zoning import ZoneClass

_FINGERPRINT = "2" * 64


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
        config_version="mst-test-v1",
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
) -> CandidateRoadAnchor:
    grid = suitability.grid
    min_x, _min_y, _max_x, max_y = grid.bounds
    return CandidateRoadAnchor(
        anchor_id=anchor_id,
        point=NetworkPoint(
            x_m=min_x + (col + 0.5) * grid.cell_width_m,
            y_m=max_y - (row + 0.5) * grid.cell_height_m,
        ),
        zone_class=ZoneClass.RESIDENTIAL,
        zoning_cell_index=0,
        raster_row=row,
        raster_col=col,
        suitability_score=float(suitability.scores[row, col]),
    )


def edge_ids(result) -> tuple[tuple[str, str], ...]:
    return tuple((edge.start_anchor_id, edge.target_anchor_id) for edge in result.edges)


def test_mst_connector_satisfies_pluggable_strategy_protocol() -> None:
    assert isinstance(MSTBaselineConnector(), AnchorConnectivityStrategy)


def test_square_mst_is_deterministic_and_independent_of_input_order() -> None:
    suitability, hard_mask = make_surface(width=2, height=2)
    anchors = (
        make_anchor(suitability, anchor_id="a", row=0, col=0),
        make_anchor(suitability, anchor_id="b", row=0, col=1),
        make_anchor(suitability, anchor_id="c", row=1, col=0),
        make_anchor(suitability, anchor_id="d", row=1, col=1),
    )

    forward = MSTBaselineConnector().connect(
        anchors=anchors,
        suitability=suitability,
        hard_mask=hard_mask,
    )
    reversed_result = MSTBaselineConnector().connect(
        anchors=tuple(reversed(anchors)),
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert edge_ids(forward) == (("a", "b"), ("a", "c"), ("b", "d"))
    assert edge_ids(reversed_result) == edge_ids(forward)
    assert forward.anchor_ids == ("a", "b", "c", "d")
    assert forward.diagnostics.planned_edge_count == 3
    assert forward.diagnostics.connected_edge_count == 3
    assert forward.diagnostics.complete
    assert forward.diagnostics.total_direct_distance_m == pytest.approx(30.0)


def test_mst_routes_exactly_n_minus_one_pairs() -> None:
    suitability, hard_mask = make_surface(width=5, height=1)
    anchors = tuple(
        make_anchor(suitability, anchor_id=f"a{index}", row=0, col=index)
        for index in range(5)
    )
    pair_connector = CountingPairConnector()

    result = MSTBaselineConnector(pair_connector=pair_connector).connect(
        anchors=anchors,
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert len(pair_connector.calls) == 4
    assert pair_connector.calls == [
        ("a0", "a1"),
        ("a1", "a2"),
        ("a2", "a3"),
        ("a3", "a4"),
    ]
    assert len(result.edges) == 4
    assert result.complete


def test_hard_barrier_is_reported_as_incomplete_instead_of_hidden() -> None:
    suitability, hard_mask = make_surface(
        width=3,
        height=3,
        hard_cells=((0, 1), (1, 1), (2, 1)),
    )
    anchors = (
        make_anchor(suitability, anchor_id="a", row=0, col=0),
        make_anchor(suitability, anchor_id="b", row=2, col=0),
        make_anchor(suitability, anchor_id="c", row=0, col=2),
    )

    result = MSTBaselineConnector().connect(
        anchors=anchors,
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert edge_ids(result) == (("a", "b"), ("a", "c"))
    assert [edge.connection.status for edge in result.edges] == [
        LeastCostConnectionStatus.CONNECTED,
        LeastCostConnectionStatus.NO_PATH,
    ]
    assert result.diagnostics.connected_edge_count == 1
    assert result.diagnostics.no_path_edge_count == 1
    assert result.diagnostics.search_limit_reached_edge_count == 0
    assert not result.complete


def test_pair_search_limit_is_preserved_in_diagnostics() -> None:
    suitability, hard_mask = make_surface(width=4, height=1)
    anchors = (
        make_anchor(suitability, anchor_id="a", row=0, col=0),
        make_anchor(suitability, anchor_id="b", row=0, col=3),
    )

    result = MSTBaselineConnector().connect(
        anchors=anchors,
        suitability=suitability,
        hard_mask=hard_mask,
        pair_policy=LeastCostConnectorPolicy(max_visited_cells=1),
    )

    assert result.edges[0].connection.status is LeastCostConnectionStatus.SEARCH_LIMIT_REACHED
    assert result.diagnostics.search_limit_reached_edge_count == 1
    assert result.diagnostics.connected_edge_count == 0
    assert not result.complete


def test_empty_and_single_anchor_sets_are_trivially_complete() -> None:
    suitability, hard_mask = make_surface(width=1, height=1)
    connector = CountingPairConnector()
    strategy = MSTBaselineConnector(pair_connector=connector)

    empty = strategy.connect(
        anchors=(),
        suitability=suitability,
        hard_mask=hard_mask,
    )
    single = strategy.connect(
        anchors=(make_anchor(suitability, anchor_id="a", row=0, col=0),),
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert empty.complete
    assert empty.edges == ()
    assert empty.diagnostics.anchor_count == 0
    assert single.complete
    assert single.edges == ()
    assert single.diagnostics.anchor_count == 1
    assert connector.calls == []


def test_strategy_enforces_explicit_anchor_bound() -> None:
    suitability, hard_mask = make_surface(width=3, height=1)
    anchors = tuple(
        make_anchor(suitability, anchor_id=f"a{index}", row=0, col=index)
        for index in range(3)
    )
    strategy = MSTBaselineConnector(
        policy=MSTBaselineConnectorPolicy(max_anchors=2)
    )

    with pytest.raises(AnchorConnectivityError, match="MST anchor limit exceeded"):
        strategy.connect(
            anchors=anchors,
            suitability=suitability,
            hard_mask=hard_mask,
        )


def test_strategy_rejects_duplicate_ids_and_raster_cells() -> None:
    suitability, hard_mask = make_surface(width=2, height=1)
    a = make_anchor(suitability, anchor_id="same", row=0, col=0)
    duplicate_id = make_anchor(suitability, anchor_id="same", row=0, col=1)
    duplicate_cell = CandidateRoadAnchor(
        anchor_id="other",
        point=a.point,
        zone_class=a.zone_class,
        zoning_cell_index=a.zoning_cell_index,
        raster_row=a.raster_row,
        raster_col=a.raster_col,
        suitability_score=a.suitability_score,
    )
    strategy = MSTBaselineConnector()

    with pytest.raises(AnchorConnectivityError, match="anchor_id values must be unique"):
        strategy.connect(
            anchors=(a, duplicate_id),
            suitability=suitability,
            hard_mask=hard_mask,
        )
    with pytest.raises(AnchorConnectivityError, match="unique raster cells"):
        strategy.connect(
            anchors=(a, duplicate_cell),
            suitability=suitability,
            hard_mask=hard_mask,
        )


def test_strategy_rejects_hard_mask_mismatch() -> None:
    suitability, hard_mask = make_surface(width=2, height=1)
    anchors = (
        make_anchor(suitability, anchor_id="a", row=0, col=0),
        make_anchor(suitability, anchor_id="b", row=0, col=1),
    )
    mismatched = HardExclusionMask(
        grid=hard_mask.grid,
        excluded=np.asarray(((False, True),), dtype=np.bool_),
        source_codes=("boundary", "different"),
    )

    with pytest.raises(AnchorConnectivityError, match="must match hard_mask exactly"):
        MSTBaselineConnector().connect(
            anchors=anchors,
            suitability=suitability,
            hard_mask=mismatched,
        )


def test_direct_distance_uses_metric_anchor_coordinates() -> None:
    suitability, hard_mask = make_surface(width=2, height=2)
    start = make_anchor(suitability, anchor_id="a", row=0, col=0)
    target = make_anchor(suitability, anchor_id="b", row=1, col=1)

    result = MSTBaselineConnector().connect(
        anchors=(start, target),
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert result.edges[0].direct_distance_m == pytest.approx(math.sqrt(200.0))
