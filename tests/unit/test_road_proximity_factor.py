import uuid

import numpy as np
import pytest
from shapely.geometry import LineString, Point

from core.urban_generator.domain import (
    ConfigRef,
    CorrelationMetadata,
    ProjectRef,
    ProjectSettings,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)
from core.urban_generator.suitability import (
    RoadProximityError,
    RoadProximityFactor,
    RoadProximityIndex,
    SuitabilityFactor,
    SuitabilityGridSpec,
)


def make_grid(
    *,
    width: int = 4,
    height: int = 4,
    working_srid: int = 32637,
    cell_size_m: float = 10.0,
) -> SuitabilityGridSpec:
    return SuitabilityGridSpec(
        working_srid=working_srid,
        bounds=(0.0, 0.0, width * cell_size_m, height * cell_size_m),
        width=width,
        height=height,
    )


def make_snapshot(*, working_srid: int = 32637) -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000901"),
        project=ProjectRef(project_id=uuid.UUID("00000000-0000-0000-0000-000000000902")),
        settings=ProjectSettings(working_srid=working_srid),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
        roads=(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.ROADS,
                source_ref="synthetic:roads:v1",
            ),
        ),
    )


def make_context(*, working_srid: int = 32637) -> RunContext:
    return RunContext(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000903"),
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=working_srid,
        config_refs=(ConfigRef(name="generation", ref="synthetic:generation:v1"),),
        correlation=CorrelationMetadata(correlation_id="road-proximity-test"),
    )


class RecordingRoadIndex(RoadProximityIndex):
    def __init__(self, **kwargs):  # noqa: ANN003
        super().__init__(**kwargs)
        self.query_sizes: list[int] = []

    def nearest_distances(self, *, x, y):  # noqa: ANN001, ANN201
        self.query_sizes.append(int(x.size))
        return super().nearest_distances(x=x, y=y)


def test_vertical_road_produces_exact_cell_center_distances() -> None:
    grid = make_grid()
    index = RoadProximityIndex(
        roads=(LineString([(15.0, 0.0), (15.0, 40.0)]),),
        working_srid=32637,
    )
    factor = RoadProximityFactor(index=index, tile_size=2)

    result = factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    expected_row = np.array([10.0, 0.0, 10.0, 20.0])
    assert np.all(result.valid_mask)
    assert np.allclose(result.values, np.tile(expected_row, (4, 1)))
    assert result.code == "road_proximity"
    assert result.version == "v1"
    assert result.values.flags.writeable is False
    assert result.valid_mask.flags.writeable is False


def test_grid_rows_use_top_to_bottom_cell_center_coordinates() -> None:
    grid = make_grid()
    index = RoadProximityIndex(
        roads=(LineString([(0.0, 35.0), (40.0, 35.0)]),),
        working_srid=32637,
    )

    result = RoadProximityFactor(index=index).evaluate(
        grid=grid,
        snapshot=make_snapshot(),
        context=make_context(),
    )

    expected = np.array([0.0, 10.0, 20.0, 30.0])[:, None]
    assert np.allclose(result.values, np.broadcast_to(expected, grid.shape))


def test_multiple_roads_use_exact_nearest_geometry() -> None:
    grid = make_grid()
    index = RoadProximityIndex(
        roads=(
            LineString([(5.0, 0.0), (5.0, 40.0)]),
            LineString([(35.0, 0.0), (35.0, 40.0)]),
        ),
        working_srid=32637,
    )

    result = RoadProximityFactor(index=index).evaluate(
        grid=grid,
        snapshot=make_snapshot(),
        context=make_context(),
    )

    assert np.allclose(result.values[0], [0.0, 10.0, 10.0, 0.0])


def test_empty_road_index_returns_explicitly_invalid_factor() -> None:
    grid = make_grid()
    result = RoadProximityFactor(
        index=RoadProximityIndex(roads=(), working_srid=32637),
    ).evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    assert not np.any(result.valid_mask)
    assert np.all(result.values == 0.0)
    assert "roads=0" in result.diagnostics


def test_factor_queries_bounded_tiles_instead_of_point_by_road_matrix() -> None:
    grid = make_grid(width=5, height=4)
    index = RecordingRoadIndex(
        roads=(LineString([(0.0, 0.0), (0.0, 40.0)]),),
        working_srid=32637,
    )
    factor = RoadProximityFactor(index=index, tile_size=2)

    factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    assert index.query_sizes == [4, 4, 2, 4, 4, 2]
    assert sum(index.query_sizes) == grid.cell_count
    assert max(index.query_sizes) == 4


def test_cell_limit_is_checked_before_any_nearest_query() -> None:
    grid = make_grid(width=5, height=5)
    index = RecordingRoadIndex(
        roads=(LineString([(0.0, 0.0), (0.0, 50.0)]),),
        working_srid=32637,
    )
    factor = RoadProximityFactor(index=index, max_cells=24)

    with pytest.raises(RoadProximityError, match="grid cell limit exceeded"):
        factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    assert index.query_sizes == []


def test_metric_crs_must_match_index_grid_snapshot_and_context() -> None:
    grid = make_grid()
    index = RoadProximityIndex(
        roads=(LineString([(0.0, 0.0), (0.0, 40.0)]),),
        working_srid=32637,
    )
    factor = RoadProximityFactor(index=index)
    other_grid = make_grid(working_srid=3857)

    with pytest.raises(RoadProximityError, match="road index working_srid"):
        factor.evaluate(grid=other_grid, snapshot=make_snapshot(3857), context=make_context(3857))
    with pytest.raises(RoadProximityError, match="snapshot working_srid"):
        factor.evaluate(
            grid=grid,
            snapshot=make_snapshot(working_srid=3857),
            context=make_context(),
        )
    with pytest.raises(RoadProximityError, match="run context working_srid"):
        factor.evaluate(
            grid=grid,
            snapshot=make_snapshot(),
            context=make_context(working_srid=3857),
        )


def test_index_and_factor_reject_invalid_configuration() -> None:
    road = LineString([(0.0, 0.0), (0.0, 10.0)])

    with pytest.raises(RoadProximityError, match="LineString or MultiLineString"):
        RoadProximityIndex(roads=(Point(0.0, 0.0),), working_srid=32637)
    with pytest.raises(RoadProximityError, match="feature limit exceeded"):
        RoadProximityIndex(roads=(road, road), working_srid=32637, max_roads=1)

    index = RoadProximityIndex(roads=(road,), working_srid=32637)
    with pytest.raises(RoadProximityError, match="positive integer"):
        RoadProximityFactor(index=index, tile_size=0)
    with pytest.raises(RoadProximityError, match="at most 512"):
        RoadProximityFactor(index=index, tile_size=513)


def test_index_validates_query_coordinates() -> None:
    index = RoadProximityIndex(
        roads=(LineString([(0.0, 0.0), (0.0, 10.0)]),),
        working_srid=32637,
    )

    with pytest.raises(RoadProximityError, match="shapes must match"):
        index.nearest_distances(
            x=np.array([1.0, 2.0]),
            y=np.array([1.0]),
        )
    with pytest.raises(RoadProximityError, match="finite"):
        index.nearest_distances(
            x=np.array([float("nan")]),
            y=np.array([1.0]),
        )


def test_factor_implements_suitability_protocol() -> None:
    factor = RoadProximityFactor(
        index=RoadProximityIndex(
            roads=(LineString([(0.0, 0.0), (0.0, 40.0)]),),
            working_srid=32637,
        )
    )

    assert isinstance(factor, SuitabilityFactor)
