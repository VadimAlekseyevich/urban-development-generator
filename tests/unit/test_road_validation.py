import uuid

import pytest
from shapely.geometry import LineString, Polygon

from core.urban_generator.domain import WorldStateContract
from core.urban_generator.roads import NodedRoad, RoadGraphBuilder, RoadGraphInput
from core.urban_generator.roads.road_validation import (
    RoadNetworkValidator,
    RoadValidationError,
    RoadValidationIssueCode,
    RoadValidationPolicy,
)

WORKING_SRID = 3857
RUN_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _road(
    road_id: str,
    coordinates: tuple[tuple[float, float], ...],
    *,
    generated: bool,
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


def test_validation_passes_connected_loop_without_forbidden_crossings() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (
            _road("a", ((0, 0), (10, 0)), generated=False),
            _road("b", ((10, 0), (10, 10)), generated=True),
            _road("c", ((10, 10), (0, 10)), generated=True),
            _road("d", ((0, 10), (0, 0)), generated=False),
        )
    )

    result = RoadNetworkValidator().validate(graph=graph)

    assert result.passed is True
    assert result.issues == ()
    assert result.diagnostics.component_count == 1
    assert result.diagnostics.dead_end_ratio == 0.0
    assert result.diagnostics.generated_edge_count == 2


def test_validation_reports_connectivity_and_dead_end_ratio() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (
            _road("a", ((0, 0), (10, 0)), generated=False),
            _road("b", ((100, 0), (110, 0)), generated=True),
        )
    )

    result = RoadNetworkValidator().validate(graph=graph)

    codes = tuple(issue.code for issue in result.issues)
    assert result.passed is False
    assert RoadValidationIssueCode.DISCONNECTED_COMPONENTS in codes
    assert RoadValidationIssueCode.DEAD_END_RATIO in codes
    assert result.diagnostics.component_count == 2
    assert result.diagnostics.dead_end_ratio == 1.0


def test_forbidden_crossings_apply_only_to_generated_edges() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (
            _road("source", ((0, 0), (10, 0)), generated=False),
            _road("generated", ((10, 0), (20, 0)), generated=True),
        )
    )
    generated_edge_id = next(
        edge.edge_id for edge in graph.edges if edge.road_id == "generated"
    )
    forbidden = Polygon(((4, -1), (6, -1), (6, 1), (4, 1)))

    result = RoadNetworkValidator(
        policy=RoadValidationPolicy(max_dead_end_ratio=1.0)
    ).validate(graph=graph, forbidden_geometries=(forbidden,))

    assert result.diagnostics.forbidden_crossing_count == 0
    assert all(
        issue.code is not RoadValidationIssueCode.FORBIDDEN_CROSSING
        for issue in result.issues
    )

    generated_forbidden = Polygon(((14, -1), (16, -1), (16, 1), (14, 1)))
    result = RoadNetworkValidator(
        policy=RoadValidationPolicy(max_dead_end_ratio=1.0)
    ).validate(graph=graph, forbidden_geometries=(generated_forbidden,))

    crossing = tuple(
        issue
        for issue in result.issues
        if issue.code is RoadValidationIssueCode.FORBIDDEN_CROSSING
    )
    assert len(crossing) == 1
    assert crossing[0].edge_id == generated_edge_id


def test_policy_can_allow_bounded_forbidden_crossings() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (_road("generated", ((0, 0), (10, 0)), generated=True),)
    )
    forbidden = Polygon(((4, -1), (6, -1), (6, 1), (4, 1)))

    result = RoadNetworkValidator(
        policy=RoadValidationPolicy(
            max_dead_end_ratio=1.0,
            max_forbidden_crossings=1,
        )
    ).validate(graph=graph, forbidden_geometries=(forbidden,))

    assert result.passed is True
    assert result.diagnostics.forbidden_crossing_count == 1


def test_validation_enforces_explicit_work_bounds_and_input_shape() -> None:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        (_road("a", ((0, 0), (10, 0)), generated=False),)
    )

    with pytest.raises(RoadValidationError, match="edge limit exceeded"):
        RoadNetworkValidator(policy=RoadValidationPolicy(max_edges=1)).validate(
            graph=RoadGraphBuilder(working_srid=WORKING_SRID).build(
                (
                    _road("a", ((0, 0), (10, 0)), generated=False),
                    _road("b", ((10, 0), (20, 0)), generated=True),
                )
            )
        )

    with pytest.raises(RoadValidationError, match="immutable tuple"):
        RoadNetworkValidator().validate(
            graph=graph,
            forbidden_geometries=[],  # type: ignore[arg-type]
        )

    with pytest.raises(RoadValidationError, match="max_dead_end_ratio"):
        RoadValidationPolicy(max_dead_end_ratio=1.1)
