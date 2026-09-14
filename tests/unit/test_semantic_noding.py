import pytest
from shapely.geometry import LineString, MultiLineString, Point

from core.urban_generator.domain import NetworkPoint
from core.urban_generator.roads import (
    SemanticJunction,
    SemanticNoder,
    SemanticNodingError,
    SemanticRoad,
)

WORKING_SRID = 32637


def _road(
    road_id: str,
    coordinates: list[tuple[float, float]],
    *,
    layer: int = 0,
    bridge: bool = False,
    tunnel: bool = False,
) -> SemanticRoad:
    return SemanticRoad(
        road_id=road_id,
        geometry=LineString(coordinates),
        layer=layer,
        bridge=bridge,
        tunnel=tunnel,
    )


def _coords(line: LineString) -> tuple[tuple[float, float], ...]:
    return tuple((float(x), float(y)) for x, y in line.coords)


def test_same_grade_crossing_creates_junction_and_splits_both_roads() -> None:
    result = SemanticNoder(working_srid=WORKING_SRID).node(
        (
            _road("east-west", [(0.0, 0.0), (10.0, 0.0)]),
            _road("north-south", [(5.0, -5.0), (5.0, 5.0)]),
        )
    )

    assert result.junctions == (
        SemanticJunction(
            point=NetworkPoint(x_m=5.0, y_m=0.0),
            road_ids=("east-west", "north-south"),
        ),
    )
    assert tuple(_coords(part) for part in result.roads[0].parts) == (
        ((0.0, 0.0), (5.0, 0.0)),
        ((5.0, 0.0), (10.0, 0.0)),
    )
    assert tuple(_coords(part) for part in result.roads[1].parts) == (
        ((5.0, -5.0), (5.0, 0.0)),
        ((5.0, 0.0), (5.0, 5.0)),
    )
    assert result.diagnostics.candidate_pair_count == 1
    assert result.diagnostics.junction_count == 1
    assert result.diagnostics.suppressed_intersection_count == 0
    assert result.diagnostics.output_part_count == 4


def test_different_layers_suppress_interior_crossing() -> None:
    result = SemanticNoder(working_srid=WORKING_SRID).node(
        (
            _road("ground", [(0.0, 0.0), (10.0, 0.0)], layer=0),
            _road("upper", [(5.0, -5.0), (5.0, 5.0)], layer=1),
        )
    )

    assert result.junctions == ()
    assert tuple(len(road.parts) for road in result.roads) == (1, 1)
    assert result.diagnostics.suppressed_intersection_count == 1


def test_bridge_or_tunnel_mismatch_suppresses_same_layer_crossing() -> None:
    bridge_result = SemanticNoder(working_srid=WORKING_SRID).node(
        (
            _road("ground", [(0.0, 0.0), (10.0, 0.0)]),
            _road("bridge", [(5.0, -5.0), (5.0, 5.0)], bridge=True),
        )
    )
    tunnel_result = SemanticNoder(working_srid=WORKING_SRID).node(
        (
            _road("ground", [(0.0, 0.0), (10.0, 0.0)]),
            _road("tunnel", [(5.0, -5.0), (5.0, 5.0)], tunnel=True),
        )
    )

    assert bridge_result.junctions == ()
    assert bridge_result.diagnostics.suppressed_intersection_count == 1
    assert tunnel_result.junctions == ()
    assert tunnel_result.diagnostics.suppressed_intersection_count == 1


def test_shared_endpoint_remains_connected_across_bridge_layer_transition() -> None:
    result = SemanticNoder(working_srid=WORKING_SRID).node(
        (
            _road("approach", [(0.0, 0.0), (5.0, 0.0)]),
            _road(
                "bridge",
                [(5.0, 0.0), (10.0, 0.0)],
                layer=1,
                bridge=True,
            ),
        )
    )

    assert result.junctions == (
        SemanticJunction(
            point=NetworkPoint(x_m=5.0, y_m=0.0),
            road_ids=("approach", "bridge"),
        ),
    )
    assert tuple(len(road.parts) for road in result.roads) == (1, 1)


def test_t_junction_splits_only_the_road_crossed_in_its_interior() -> None:
    result = SemanticNoder(working_srid=WORKING_SRID).node(
        (
            _road("through", [(0.0, 0.0), (10.0, 0.0)]),
            _road("spur", [(5.0, 5.0), (5.0, 0.0)]),
        )
    )

    assert tuple(_coords(part) for part in result.roads[0].parts) == (
        ((0.0, 0.0), (5.0, 0.0)),
        ((5.0, 0.0), (10.0, 0.0)),
    )
    assert tuple(_coords(part) for part in result.roads[1].parts) == (
        ((5.0, 5.0), (5.0, 0.0)),
    )


def test_grade_mismatched_t_junction_is_suppressed() -> None:
    result = SemanticNoder(working_srid=WORKING_SRID).node(
        (
            _road("upper", [(0.0, 0.0), (10.0, 0.0)], layer=1, bridge=True),
            _road("ground-spur", [(5.0, 5.0), (5.0, 0.0)]),
        )
    )

    assert result.junctions == ()
    assert tuple(len(road.parts) for road in result.roads) == (1, 1)
    assert result.diagnostics.suppressed_intersection_count == 1


def test_partial_overlap_nodes_only_overlap_boundaries() -> None:
    result = SemanticNoder(working_srid=WORKING_SRID).node(
        (
            _road("a", [(0.0, 0.0), (10.0, 0.0)]),
            _road("b", [(5.0, 0.0), (15.0, 0.0)]),
        )
    )

    assert tuple(junction.point for junction in result.junctions) == (
        NetworkPoint(x_m=5.0, y_m=0.0),
        NetworkPoint(x_m=10.0, y_m=0.0),
    )
    assert tuple(len(road.parts) for road in result.roads) == (2, 2)
    assert result.diagnostics.overlap_pair_count == 1
    assert result.diagnostics.junction_count == 2


def test_split_parts_preserve_original_coordinate_direction() -> None:
    result = SemanticNoder(working_srid=WORKING_SRID).node(
        (
            _road("reverse", [(10.0, 0.0), (0.0, 0.0)]),
            _road("cross", [(5.0, -2.0), (5.0, 2.0)]),
        )
    )

    assert tuple(_coords(part) for part in result.roads[0].parts) == (
        ((10.0, 0.0), (5.0, 0.0)),
        ((5.0, 0.0), (0.0, 0.0)),
    )


def test_multiline_input_is_flattened_in_component_order() -> None:
    multiline = SemanticRoad(
        road_id="multi",
        geometry=MultiLineString(
            [
                [(0.0, 0.0), (4.0, 0.0)],
                [(6.0, 0.0), (10.0, 0.0)],
            ]
        ),
    )
    result = SemanticNoder(working_srid=WORKING_SRID).node(
        (
            multiline,
            _road("cross", [(8.0, -2.0), (8.0, 2.0)]),
        )
    )

    assert tuple(_coords(part) for part in result.roads[0].parts) == (
        ((0.0, 0.0), (4.0, 0.0)),
        ((6.0, 0.0), (8.0, 0.0)),
        ((8.0, 0.0), (10.0, 0.0)),
    )
    assert result.diagnostics.road_part_count == 3


def test_candidate_pair_and_part_limits_bound_pathological_inputs() -> None:
    noder = SemanticNoder(
        working_srid=WORKING_SRID,
        max_candidate_pairs=1,
    )
    roads = (
        _road("horizontal", [(-2.0, 0.0), (2.0, 0.0)]),
        _road("vertical", [(0.0, -2.0), (0.0, 2.0)]),
        _road("diagonal", [(-2.0, -2.0), (2.0, 2.0)]),
    )

    with pytest.raises(SemanticNodingError, match="candidate-pair limit exceeded"):
        noder.node(roads)

    with pytest.raises(SemanticNodingError, match="road part limit exceeded"):
        SemanticNoder(working_srid=WORKING_SRID, max_road_parts=1).node(
            (
                SemanticRoad(
                    road_id="multi",
                    geometry=MultiLineString(
                        [
                            [(0.0, 0.0), (1.0, 0.0)],
                            [(2.0, 0.0), (3.0, 0.0)],
                        ]
                    ),
                ),
            )
        )


def test_noder_validates_collection_identity_and_2d_line_contract() -> None:
    noder = SemanticNoder(working_srid=WORKING_SRID)
    road = _road("a", [(0.0, 0.0), (1.0, 0.0)])

    with pytest.raises(SemanticNodingError, match="immutable tuple"):
        noder.node([road])  # type: ignore[arg-type]

    with pytest.raises(SemanticNodingError, match="duplicate road_id"):
        noder.node((road, road))

    with pytest.raises(SemanticNodingError, match="LineString or MultiLineString"):
        SemanticRoad(road_id="point", geometry=Point(0.0, 0.0))

    with pytest.raises(SemanticNodingError, match="must be 2D"):
        SemanticRoad(
            road_id="3d",
            geometry=LineString([(0.0, 0.0, 1.0), (1.0, 0.0, 1.0)]),
        )


def test_empty_input_returns_empty_bounded_result() -> None:
    result = SemanticNoder(working_srid=WORKING_SRID).node(())

    assert result.roads == ()
    assert result.junctions == ()
    assert result.diagnostics.road_count == 0
    assert result.diagnostics.candidate_pair_count == 0
