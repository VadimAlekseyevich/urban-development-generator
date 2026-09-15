from __future__ import annotations

import math

import numpy as np
import pytest

from core.urban_generator.domain import NetworkPoint
from core.urban_generator.roads import (
    CandidateRoadAnchor,
    LeastCostConnectionStatus,
    LeastCostConnector,
    LeastCostConnectorError,
    LeastCostConnectorPolicy,
)
from core.urban_generator.suitability import (
    HardExclusionMask,
    SuitabilityGridSpec,
    WeightedSuitabilityResult,
)
from core.urban_generator.zoning import ZoneClass


_FINGERPRINT = "1" * 64


def make_surface(
    scores: tuple[tuple[float, ...], ...],
    *,
    hard_cells: tuple[tuple[int, int], ...] = (),
    invalid_cells: tuple[tuple[int, int], ...] = (),
    working_srid: int = 32637,
) -> tuple[WeightedSuitabilityResult, HardExclusionMask]:
    values = np.asarray(scores, dtype=np.float64)
    height, width = values.shape
    grid = SuitabilityGridSpec(
        working_srid=working_srid,
        bounds=(0.0, 0.0, float(width * 10), float(height * 10)),
        width=width,
        height=height,
    )
    hard = np.zeros((height, width), dtype=np.bool_)
    for row, col in hard_cells:
        hard[row, col] = True
    valid = np.logical_not(hard)
    for row, col in invalid_cells:
        valid[row, col] = False
    values = values.copy()
    values[~valid] = 0.0

    suitability = WeightedSuitabilityResult(
        grid=grid,
        scores=values,
        valid_mask=valid,
        hard_excluded_mask=hard,
        config_version="least-cost-test-v1",
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
    row: int,
    col: int,
    anchor_id: str | None = None,
    score: float | None = None,
    point: NetworkPoint | None = None,
) -> CandidateRoadAnchor:
    grid = suitability.grid
    min_x, _min_y, _max_x, max_y = grid.bounds
    if point is None:
        point = NetworkPoint(
            x_m=min_x + (col + 0.5) * grid.cell_width_m,
            y_m=max_y - (row + 0.5) * grid.cell_height_m,
        )
    if score is None:
        score = float(suitability.scores[row, col])
    return CandidateRoadAnchor(
        anchor_id=anchor_id or f"anchor:{row}:{col}",
        point=point,
        zone_class=ZoneClass.RESIDENTIAL,
        zoning_cell_index=0,
        raster_row=row,
        raster_col=col,
        suitability_score=score,
    )


def test_connector_returns_straight_metric_path_on_uniform_surface() -> None:
    suitability, hard_mask = make_surface(((1.0, 1.0, 1.0),))
    start = make_anchor(suitability, row=0, col=0)
    target = make_anchor(suitability, row=0, col=2)

    result = LeastCostConnector().connect(
        start=start,
        target=target,
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert result.status is LeastCostConnectionStatus.CONNECTED
    assert result.connected
    assert result.raster_path == ((0, 0), (0, 1), (0, 2))
    assert result.length_m == pytest.approx(20.0)
    assert result.total_cost == pytest.approx(20.0)
    assert result.geometry is not None
    assert tuple(result.geometry.coords) == ((5.0, 5.0), (15.0, 5.0), (25.0, 5.0))
    assert result.hard_exclusion_source_codes == ("boundary", "synthetic_hard")


def test_hard_mask_forces_route_around_forbidden_cell_without_corner_cutting() -> None:
    suitability, hard_mask = make_surface(
        (
            (1.0, 1.0, 1.0),
            (1.0, 0.0, 1.0),
            (1.0, 1.0, 1.0),
        ),
        hard_cells=((1, 1),),
    )
    start = make_anchor(suitability, row=1, col=0)
    target = make_anchor(suitability, row=1, col=2)

    result = LeastCostConnector().connect(
        start=start,
        target=target,
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert result.status is LeastCostConnectionStatus.CONNECTED
    assert (1, 1) not in result.raster_path
    assert result.raster_path == ((1, 0), (0, 0), (0, 1), (0, 2), (1, 2))
    assert result.length_m == pytest.approx(40.0)
    assert result.total_cost == pytest.approx(40.0)


def test_soft_suitability_penalty_can_choose_longer_higher_score_route() -> None:
    suitability, hard_mask = make_surface(
        (
            (1.0, 1.0, 1.0),
            (1.0, 0.0, 1.0),
            (1.0, 1.0, 1.0),
        )
    )
    start = make_anchor(suitability, row=1, col=0)
    target = make_anchor(suitability, row=1, col=2)

    result = LeastCostConnector().connect(
        start=start,
        target=target,
        suitability=suitability,
        hard_mask=hard_mask,
        policy=LeastCostConnectorPolicy(suitability_penalty_weight=10.0),
    )

    assert result.status is LeastCostConnectionStatus.CONNECTED
    assert result.raster_path == ((1, 0), (0, 1), (1, 2))
    assert (1, 1) not in result.raster_path
    assert result.length_m == pytest.approx(2.0 * math.sqrt(200.0))
    assert result.total_cost == pytest.approx(result.length_m)


def test_full_hard_barrier_returns_stable_no_path_result() -> None:
    suitability, hard_mask = make_surface(
        (
            (1.0, 0.0, 1.0),
            (1.0, 0.0, 1.0),
            (1.0, 0.0, 1.0),
        ),
        hard_cells=((0, 1), (1, 1), (2, 1)),
    )
    result = LeastCostConnector().connect(
        start=make_anchor(suitability, row=1, col=0),
        target=make_anchor(suitability, row=1, col=2),
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert result.status is LeastCostConnectionStatus.NO_PATH
    assert not result.connected
    assert result.geometry is None
    assert result.raster_path == ()
    assert result.length_m is None
    assert result.total_cost is None
    assert result.visited_cell_count == 3


def test_invalid_data_cells_are_not_traversable_even_when_not_hard_excluded() -> None:
    suitability, hard_mask = make_surface(
        ((1.0, 1.0, 1.0),),
        invalid_cells=((0, 1),),
    )

    result = LeastCostConnector().connect(
        start=make_anchor(suitability, row=0, col=0),
        target=make_anchor(suitability, row=0, col=2),
        suitability=suitability,
        hard_mask=hard_mask,
    )

    assert result.status is LeastCostConnectionStatus.NO_PATH
    assert result.visited_cell_count == 1


def test_search_limit_is_reported_separately_from_no_path() -> None:
    suitability, hard_mask = make_surface(((1.0, 1.0, 1.0, 1.0),))

    result = LeastCostConnector().connect(
        start=make_anchor(suitability, row=0, col=0),
        target=make_anchor(suitability, row=0, col=3),
        suitability=suitability,
        hard_mask=hard_mask,
        policy=LeastCostConnectorPolicy(max_visited_cells=1),
    )

    assert result.status is LeastCostConnectionStatus.SEARCH_LIMIT_REACHED
    assert result.visited_cell_count == 1
    assert result.geometry is None
    assert result.raster_path == ()


def test_default_corner_cutting_policy_does_not_cross_blocked_diagonal_corner() -> None:
    suitability, hard_mask = make_surface(
        (
            (1.0, 0.0),
            (0.0, 1.0),
        ),
        hard_cells=((0, 1), (1, 0)),
    )
    start = make_anchor(suitability, row=0, col=0)
    target = make_anchor(suitability, row=1, col=1)

    blocked = LeastCostConnector().connect(
        start=start,
        target=target,
        suitability=suitability,
        hard_mask=hard_mask,
    )
    allowed = LeastCostConnector().connect(
        start=start,
        target=target,
        suitability=suitability,
        hard_mask=hard_mask,
        policy=LeastCostConnectorPolicy(prevent_corner_cutting=False),
    )

    assert blocked.status is LeastCostConnectionStatus.NO_PATH
    assert allowed.status is LeastCostConnectionStatus.CONNECTED
    assert allowed.raster_path == ((0, 0), (1, 1))
    assert allowed.length_m == pytest.approx(math.sqrt(200.0))


def test_equal_cost_routes_are_deterministic() -> None:
    suitability, hard_mask = make_surface(
        (
            (1.0, 1.0, 1.0),
            (1.0, 0.0, 1.0),
            (1.0, 1.0, 1.0),
        ),
        hard_cells=((1, 1),),
    )
    connector = LeastCostConnector()
    kwargs = dict(
        start=make_anchor(suitability, row=1, col=0),
        target=make_anchor(suitability, row=1, col=2),
        suitability=suitability,
        hard_mask=hard_mask,
    )

    first = connector.connect(**kwargs)
    second = connector.connect(**kwargs)

    assert first == second
    assert first.raster_path[1] == (0, 0)


def test_connector_rejects_hard_mask_that_does_not_match_suitability() -> None:
    suitability, _hard_mask = make_surface(
        ((1.0, 0.0, 1.0),),
        hard_cells=((0, 1),),
    )
    mismatched = HardExclusionMask(
        grid=suitability.grid,
        excluded=np.zeros(suitability.grid.shape, dtype=np.bool_),
        source_codes=("other",),
    )

    with pytest.raises(LeastCostConnectorError, match="match hard_mask exactly"):
        LeastCostConnector().connect(
            start=make_anchor(suitability, row=0, col=0),
            target=make_anchor(suitability, row=0, col=2),
            suitability=suitability,
            hard_mask=mismatched,
        )


def test_connector_rejects_stale_anchor_point_and_score() -> None:
    suitability, hard_mask = make_surface(((0.8, 0.9),))
    connector = LeastCostConnector()
    target = make_anchor(suitability, row=0, col=1)

    with pytest.raises(LeastCostConnectorError, match="cell center"):
        connector.connect(
            start=make_anchor(
                suitability,
                row=0,
                col=0,
                point=NetworkPoint(x_m=6.0, y_m=5.0),
            ),
            target=target,
            suitability=suitability,
            hard_mask=hard_mask,
        )

    with pytest.raises(LeastCostConnectorError, match="suitability_score"):
        connector.connect(
            start=make_anchor(suitability, row=0, col=0, score=0.7),
            target=target,
            suitability=suitability,
            hard_mask=hard_mask,
        )


def test_connector_rejects_same_anchor_or_same_raster_cell() -> None:
    suitability, hard_mask = make_surface(((1.0, 1.0),))
    start = make_anchor(suitability, row=0, col=0, anchor_id="same")

    with pytest.raises(LeastCostConnectorError, match="anchors must differ"):
        LeastCostConnector().connect(
            start=start,
            target=make_anchor(suitability, row=0, col=1, anchor_id="same"),
            suitability=suitability,
            hard_mask=hard_mask,
        )

    with pytest.raises(LeastCostConnectorError, match="different raster cells"):
        LeastCostConnector().connect(
            start=start,
            target=make_anchor(suitability, row=0, col=0, anchor_id="other"),
            suitability=suitability,
            hard_mask=hard_mask,
        )


def test_policy_validates_bounds_penalty_and_boolean_options() -> None:
    with pytest.raises(LeastCostConnectorError, match="max_visited_cells"):
        LeastCostConnectorPolicy(max_visited_cells=0)
    with pytest.raises(LeastCostConnectorError, match="suitability_penalty_weight"):
        LeastCostConnectorPolicy(suitability_penalty_weight=-1.0)
    with pytest.raises(LeastCostConnectorError, match="suitability_penalty_weight"):
        LeastCostConnectorPolicy(suitability_penalty_weight=math.nan)
    with pytest.raises(LeastCostConnectorError, match="allow_diagonal"):
        LeastCostConnectorPolicy(allow_diagonal=1)  # type: ignore[arg-type]
    with pytest.raises(LeastCostConnectorError, match="prevent_corner_cutting"):
        LeastCostConnectorPolicy(prevent_corner_cutting=0)  # type: ignore[arg-type]
