import math
import uuid

import pytest
from shapely.geometry import LineString

from core.urban_generator.domain import WorldStateContract
from core.urban_generator.roads import NodedRoad, RoadGraphBuilder, RoadGraphInput
from core.urban_generator.roads.road_metrics import (
    RoadMetricsCalculator,
    RoadMetricsError,
    RoadMetricsPolicy,
)

WORKING_SRID = 3857
RUN_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _road(
    road_id: str,
    coordinates: tuple[tuple[float, float], ...],
    *,
    generated: bool = False,
) -> RoadGraphInput:
    state = (
        WorldStateContract.generated_for(RUN_ID)
        if generated
        else WorldStateContract.fixed_source()
    )
    return RoadGraphInput(
        road=NodedRoad(road_id=road_id, parts=(LineString(coordinates),)),
        state=state,
    )


def test_metrics_cover_density_components_degree_and_intersections() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (
            _road("west", ((0, 0), (100, 0))),
            _road("east", ((100, 0), (200, 0))),
            _road("north", ((100, 0), (100, 100)), generated=True),
        )
    )

    metrics = RoadMetricsCalculator().calculate(
        graph=graph,
        analysis_area_m2=1_000_000.0,
    )

    assert metrics.node_count == 4
    assert metrics.edge_count == 3
    assert metrics.component_count == 1
    assert metrics.total_length_m == pytest.approx(300.0)
    assert metrics.generated_length_m == pytest.approx(100.0)
    assert metrics.length_density_km_per_km2 == pytest.approx(0.3)
    assert metrics.mean_degree == pytest.approx(1.5)
    assert metrics.max_degree == 3
    assert metrics.intersection_count == 1
    assert metrics.intersection_density_per_km2 == pytest.approx(1.0)
    assert metrics.edge_weighted_circuity == pytest.approx(1.0)
    assert metrics.circuity_edge_count == 3


def test_edge_weighted_circuity_uses_metric_geometry_over_endpoint_chord() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (_road("curved", ((0, 0), (50, 50), (100, 0))),)
    )

    metrics = RoadMetricsCalculator().calculate(
        graph=graph,
        analysis_area_m2=1_000_000.0,
    )

    assert metrics.edge_weighted_circuity == pytest.approx(math.sqrt(2.0))
    assert metrics.circuity_edge_count == 1


def test_intersection_degree_threshold_is_configurable() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (
            _road("a", ((0, 0), (10, 0))),
            _road("b", ((10, 0), (20, 0))),
        )
    )

    default = RoadMetricsCalculator().calculate(
        graph=graph,
        analysis_area_m2=1_000_000.0,
    )
    degree_two = RoadMetricsCalculator(
        policy=RoadMetricsPolicy(intersection_min_degree=2)
    ).calculate(graph=graph, analysis_area_m2=1_000_000.0)

    assert default.intersection_count == 0
    assert degree_two.intersection_count == 1


def test_empty_graph_has_zero_topology_metrics_and_no_circuity() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(())

    metrics = RoadMetricsCalculator().calculate(
        graph=graph,
        analysis_area_m2=500_000.0,
    )

    assert metrics.total_length_m == 0.0
    assert metrics.mean_degree == 0.0
    assert metrics.max_degree == 0
    assert metrics.intersection_count == 0
    assert metrics.edge_weighted_circuity is None
    assert metrics.circuity_edge_count == 0


def test_metrics_reject_invalid_area_and_enforce_work_bounds() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (_road("a", ((0, 0), (10, 0))),)
    )

    with pytest.raises(RoadMetricsError, match="analysis_area_m2"):
        RoadMetricsCalculator().calculate(graph=graph, analysis_area_m2=0.0)

    with pytest.raises(RoadMetricsError, match="edge limit exceeded"):
        RoadMetricsCalculator(policy=RoadMetricsPolicy(max_edges=1)).calculate(
            graph=RoadGraphBuilder(working_srid=WORKING_SRID).build(
                (
                    _road("a", ((0, 0), (10, 0))),
                    _road("b", ((10, 0), (20, 0))),
                )
            ),
            analysis_area_m2=1_000_000.0,
        )
