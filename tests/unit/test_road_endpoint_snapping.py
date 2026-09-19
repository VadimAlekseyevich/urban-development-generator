import pytest
from shapely.geometry import LineString

from core.urban_generator.roads.endpoint_snapping import (
    EndpointRoadSnapper,
    EndpointRoadSnappingError,
    EndpointRoadSnappingPolicy,
)
from core.urban_generator.roads.semantic_noding import SemanticRoad

WORKING_SRID = 32637


def _road(road_id: str, start: tuple[float, float], end: tuple[float, float]) -> SemanticRoad:
    return SemanticRoad(road_id=road_id, geometry=LineString((start, end)))


def test_endpoint_snapping_clusters_to_stable_representative_independent_of_input_order() -> None:
    first = _road("a", (0.0, 0.0), (10.0, 0.0))
    second = _road("b", (10.4, 0.0), (20.0, 0.0))
    snapper = EndpointRoadSnapper(
        working_srid=WORKING_SRID,
        policy=EndpointRoadSnappingPolicy(tolerance_m=0.5),
    )

    forward = snapper.snap((first, second))
    reverse = snapper.snap((second, first))

    assert forward.roads == reverse.roads
    by_id = {road.road_id: road for road in forward.roads}
    assert tuple(by_id["a"].geometry.coords)[-1] == (10.0, 0.0)
    assert tuple(by_id["b"].geometry.coords)[0] == (10.0, 0.0)
    assert forward.diagnostics.snapped_endpoint_count == 1


def test_endpoint_snapping_uses_transitive_tolerance_clusters_deterministically() -> None:
    roads = (
        _road("a", (0.0, 0.0), (10.0, 0.0)),
        _road("b", (10.4, 0.0), (20.0, 0.0)),
        _road("c", (10.8, 0.0), (30.0, 0.0)),
    )
    result = EndpointRoadSnapper(
        working_srid=WORKING_SRID,
        policy=EndpointRoadSnappingPolicy(tolerance_m=0.5),
    ).snap(roads)

    starts = {
        road.road_id: tuple(road.geometry.coords)[0]
        for road in result.roads
    }
    assert tuple(result.roads[0].geometry.coords)[-1] == (10.0, 0.0)
    assert starts["b"] == (10.0, 0.0)
    assert starts["c"] == (10.0, 0.0)


def test_endpoint_snapping_rejects_candidate_pair_budget_overflow() -> None:
    roads = tuple(
        _road(f"road-{index}", (0.0, 0.0), (10.0 + index, 0.0))
        for index in range(3)
    )
    snapper = EndpointRoadSnapper(
        working_srid=WORKING_SRID,
        policy=EndpointRoadSnappingPolicy(
            tolerance_m=0.0,
            max_candidate_pairs=1,
        ),
    )

    with pytest.raises(EndpointRoadSnappingError, match="candidate-pair limit"):
        snapper.snap(roads)


def test_endpoint_snapping_rejects_collapsed_two_point_road() -> None:
    road = _road("short", (0.0, 0.0), (0.2, 0.0))
    snapper = EndpointRoadSnapper(
        working_srid=WORKING_SRID,
        policy=EndpointRoadSnappingPolicy(tolerance_m=0.5),
    )

    with pytest.raises(EndpointRoadSnappingError, match="collapsed"):
        snapper.snap((road,))
