import uuid
from dataclasses import replace

import pytest
from shapely.geometry import LineString

from core.urban_generator.domain import (
    CANONICAL_METRIC_REGISTRY,
    MetricSource,
    RawMetricId,
    WorldStateContract,
)
from core.urban_generator.metrics import (
    ROAD_RAW_METRIC_IDS,
    RoadMetricAdapter,
    RoadMetricAdapterError,
)
from core.urban_generator.roads import (
    NodedRoad,
    RoadGraphBuilder,
    RoadGraphInput,
    RoadMetricsCalculator,
    RoadNetworkValidator,
)
from core.urban_generator.stages.roads import RoadStageOutput

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
        road=NodedRoad(
            road_id=road_id,
            parts=(LineString(coordinates),),
        ),
        state=state,
    )


def _stage_output(*, empty: bool = False) -> RoadStageOutput:
    graph = RoadGraphBuilder(working_srid=WORKING_SRID).build(
        ()
        if empty
        else (
            _road("west", ((0, 0), (100, 0))),
            _road("east", ((100, 0), (200, 0))),
            _road(
                "north",
                ((100, 0), (100, 100)),
                generated=True,
            ),
        )
    )
    metrics = RoadMetricsCalculator().calculate(
        graph=graph,
        analysis_area_m2=1_000_000.0,
    )
    validation = RoadNetworkValidator().validate(graph=graph)
    return RoadStageOutput(
        anchors=object(),
        baseline=object(),
        growth=object(),
        fixed_attachment=None,
        graph=graph,
        classification=object(),
        validation=validation,
        metrics=metrics,
    )


def _scalar(result, metric_id: RawMetricId) -> float:
    value = result.require(metric_id).scalar_value
    assert value is not None
    return value


def test_adapter_uses_canonical_road_registry_order() -> None:
    expected = tuple(
        definition.metric_id
        for definition in CANONICAL_METRIC_REGISTRY.definitions_for(
            source=MetricSource.ROADS
        )
    )

    assert ROAD_RAW_METRIC_IDS == expected
    assert ROAD_RAW_METRIC_IDS == (
        RawMetricId.ROADS_LENGTH_DENSITY_KM_PER_KM2,
        RawMetricId.ROADS_CONNECTED_COMPONENTS,
        RawMetricId.ROADS_AVERAGE_DEGREE,
        RawMetricId.ROADS_INTERSECTION_DENSITY_PER_KM2,
        RawMetricId.ROADS_CIRCUITY,
        RawMetricId.ROADS_DEAD_END_RATIO,
    )


def test_adapter_projects_existing_metrics_and_validation_values() -> None:
    result = RoadMetricAdapter().adapt(roads=_stage_output())

    assert tuple(item.metric_id for item in result.raw_metrics) == (
        ROAD_RAW_METRIC_IDS
    )
    assert _scalar(
        result,
        RawMetricId.ROADS_LENGTH_DENSITY_KM_PER_KM2,
    ) == pytest.approx(0.3)
    assert _scalar(
        result,
        RawMetricId.ROADS_CONNECTED_COMPONENTS,
    ) == 1.0
    assert _scalar(
        result,
        RawMetricId.ROADS_AVERAGE_DEGREE,
    ) == pytest.approx(1.5)
    assert _scalar(
        result,
        RawMetricId.ROADS_INTERSECTION_DENSITY_PER_KM2,
    ) == pytest.approx(1.0)
    assert _scalar(
        result,
        RawMetricId.ROADS_CIRCUITY,
    ) == pytest.approx(1.0)
    assert _scalar(
        result,
        RawMetricId.ROADS_DEAD_END_RATIO,
    ) == pytest.approx(0.75)

    assert result.diagnostics.node_count == 4
    assert result.diagnostics.edge_count == 3
    assert result.diagnostics.component_count == 1
    assert result.diagnostics.intersection_count == 1
    assert result.diagnostics.circuity_edge_count == 3
    assert result.diagnostics.dead_end_node_count == 3


def test_adapter_does_not_rebuild_or_recalculate_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_output = _stage_output()

    def _unexpected_build(*args: object, **kwargs: object) -> object:
        raise AssertionError("road graph rebuild is forbidden in S11-T06")

    monkeypatch.setattr(RoadGraphBuilder, "build", _unexpected_build)

    result = RoadMetricAdapter().adapt(roads=stage_output)

    assert _scalar(
        result,
        RawMetricId.ROADS_LENGTH_DENSITY_KM_PER_KM2,
    ) == pytest.approx(0.3)


def test_empty_graph_keeps_circuity_missing_and_other_metrics_zero() -> None:
    result = RoadMetricAdapter().adapt(roads=_stage_output(empty=True))

    assert _scalar(
        result,
        RawMetricId.ROADS_LENGTH_DENSITY_KM_PER_KM2,
    ) == 0.0
    assert _scalar(
        result,
        RawMetricId.ROADS_CONNECTED_COMPONENTS,
    ) == 0.0
    assert _scalar(
        result,
        RawMetricId.ROADS_AVERAGE_DEGREE,
    ) == 0.0
    assert _scalar(
        result,
        RawMetricId.ROADS_INTERSECTION_DENSITY_PER_KM2,
    ) == 0.0
    assert result.require(
        RawMetricId.ROADS_CIRCUITY
    ).scalar_value is None
    assert _scalar(
        result,
        RawMetricId.ROADS_DEAD_END_RATIO,
    ) == 0.0


def test_adapter_rejects_stale_metric_validation_alignment() -> None:
    stage_output = _stage_output()
    validation = replace(
        stage_output.validation,
        diagnostics=replace(
            stage_output.validation.diagnostics,
            node_count=stage_output.validation.diagnostics.node_count + 1,
        ),
    )

    with pytest.raises(
        RoadMetricAdapterError,
        match="node_count must match",
    ):
        RoadMetricAdapter().adapt(
            roads=replace(
                stage_output,
                validation=validation,
            )
        )
