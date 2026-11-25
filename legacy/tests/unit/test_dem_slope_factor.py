import uuid

import numpy as np
import pytest

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
    DEMSlopeError,
    DEMSlopeFactor,
    DEMSlopeNoDataPolicy,
    DEMSlopeWindow,
    SuitabilityFactor,
    SuitabilityGridSpec,
)


class FakeDEMSource:
    def __init__(
        self,
        grid: SuitabilityGridSpec,
        values: tuple[tuple[float | int | None, ...], ...],
    ) -> None:
        self.grid = grid
        self.values = values
        self.reads: list[DEMSlopeWindow] = []

    def read_window(
        self,
        *,
        window: DEMSlopeWindow,
    ) -> tuple[tuple[float | int | None, ...], ...]:
        self.reads.append(window)
        return tuple(
            tuple(
                self.values[row][col]
                for col in range(window.col_off, window.col_off + window.width)
            )
            for row in range(window.row_off, window.row_off + window.height)
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
        bounds=(
            500_000.0,
            6_000_000.0,
            500_000.0 + width * cell_size_m,
            6_000_000.0 + height * cell_size_m,
        ),
        width=width,
        height=height,
    )


def make_snapshot(*, working_srid: int = 32637) -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000801"),
        project=ProjectRef(project_id=uuid.UUID("00000000-0000-0000-0000-000000000802")),
        settings=ProjectSettings(working_srid=working_srid),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
        dem=(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.DEM,
                source_ref="synthetic:dem:v1",
            ),
        ),
    )


def make_context(*, working_srid: int = 32637) -> RunContext:
    return RunContext(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000803"),
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=working_srid,
        config_refs=(ConfigRef(name="generation", ref="synthetic:generation:v1"),),
        correlation=CorrelationMetadata(correlation_id="dem-slope-test"),
    )


def plane_values(
    *,
    width: int,
    height: int,
    rise_per_col: float = 1.0,
    rise_per_row: float = 0.0,
) -> tuple[tuple[float, ...], ...]:
    return tuple(
        tuple(col * rise_per_col + row * rise_per_row for col in range(width))
        for row in range(height)
    )


def test_planar_dem_produces_expected_slope_in_degrees_including_edges() -> None:
    grid = make_grid()
    source = FakeDEMSource(grid, plane_values(width=4, height=4, rise_per_col=1.0))
    factor = DEMSlopeFactor(source=source, tile_size=2)

    result = factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    expected = np.degrees(np.arctan(0.1))
    assert np.all(result.valid_mask)
    assert np.allclose(result.values, expected)
    assert result.code == "slope"
    assert result.version == "v1"
    assert result.values.flags.writeable is False
    assert result.valid_mask.flags.writeable is False


def test_vertical_scale_converts_source_elevation_units_to_metres() -> None:
    grid = make_grid()
    source = FakeDEMSource(grid, plane_values(width=4, height=4, rise_per_col=100.0))
    factor = DEMSlopeFactor(
        source=source,
        vertical_scale_to_m=0.01,
        tile_size=4,
    )

    result = factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    assert np.allclose(result.values, np.degrees(np.arctan(0.1)))


def test_nodata_policy_invalidates_or_uses_one_sided_difference() -> None:
    grid = make_grid(width=3, height=3)
    rows = [list(row) for row in plane_values(width=3, height=3, rise_per_col=1.0)]
    rows[1][0] = None
    values = tuple(tuple(row) for row in rows)

    strict = DEMSlopeFactor(
        source=FakeDEMSource(grid, values),
        nodata_policy=DEMSlopeNoDataPolicy.INVALIDATE,
        tile_size=3,
    ).evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())
    one_sided = DEMSlopeFactor(
        source=FakeDEMSource(grid, values),
        nodata_policy=DEMSlopeNoDataPolicy.ONE_SIDED,
        tile_size=3,
    ).evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    assert bool(strict.valid_mask[1, 1]) is False
    assert strict.values[1, 1] == 0.0
    assert bool(one_sided.valid_mask[1, 1]) is True
    assert one_sided.values[1, 1] == pytest.approx(np.degrees(np.arctan(0.1)))
    assert bool(one_sided.valid_mask[1, 0]) is False


def test_factor_reads_bounded_row_major_tiles_with_one_cell_halo() -> None:
    grid = make_grid(width=5, height=4)
    source = FakeDEMSource(grid, plane_values(width=5, height=4))
    factor = DEMSlopeFactor(source=source, tile_size=2)

    factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    assert len(source.reads) == 6
    assert source.reads[0] == DEMSlopeWindow(row_off=0, col_off=0, height=3, width=3)
    assert source.reads[1] == DEMSlopeWindow(row_off=0, col_off=1, height=3, width=4)
    assert source.reads[-1] == DEMSlopeWindow(row_off=1, col_off=3, height=3, width=2)
    assert max(window.cell_count for window in source.reads) <= 16
    assert all(window.cell_count < grid.cell_count for window in source.reads)


def test_cell_limit_is_checked_before_any_dem_read() -> None:
    grid = make_grid(width=5, height=5)
    source = FakeDEMSource(grid, plane_values(width=5, height=5))
    factor = DEMSlopeFactor(source=source, max_cells=24)

    with pytest.raises(DEMSlopeError, match="grid cell limit exceeded"):
        factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    assert source.reads == []


def test_source_window_shape_and_values_are_validated() -> None:
    grid = make_grid(width=3, height=3)

    class WrongShapeSource(FakeDEMSource):
        def read_window(
            self,
            *,
            window: DEMSlopeWindow,
        ) -> tuple[tuple[float | int | None, ...], ...]:
            self.reads.append(window)
            return ((1.0,),)

    wrong_shape = DEMSlopeFactor(
        source=WrongShapeSource(grid, plane_values(width=3, height=3)),
        tile_size=3,
    )
    with pytest.raises(DEMSlopeError, match="unexpected window"):
        wrong_shape.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    bad_values = [list(row) for row in plane_values(width=3, height=3)]
    bad_values[1][1] = float("nan")
    nonfinite = DEMSlopeFactor(
        source=FakeDEMSource(grid, tuple(tuple(row) for row in bad_values)),
        tile_size=3,
    )
    with pytest.raises(DEMSlopeError, match="finite or None"):
        nonfinite.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())


def test_grid_snapshot_and_context_must_match_source_crs_contract() -> None:
    grid = make_grid()
    source = FakeDEMSource(grid, plane_values(width=4, height=4))
    factor = DEMSlopeFactor(source=source)
    other_grid = SuitabilityGridSpec(
        working_srid=32637,
        bounds=(500_010.0, 6_000_000.0, 500_050.0, 6_000_040.0),
        width=4,
        height=4,
    )

    with pytest.raises(DEMSlopeError, match="exactly the requested"):
        factor.evaluate(grid=other_grid, snapshot=make_snapshot(), context=make_context())
    with pytest.raises(DEMSlopeError, match="snapshot working_srid"):
        factor.evaluate(
            grid=grid,
            snapshot=make_snapshot(working_srid=3857),
            context=make_context(),
        )
    with pytest.raises(DEMSlopeError, match="run context working_srid"):
        factor.evaluate(
            grid=grid,
            snapshot=make_snapshot(),
            context=make_context(working_srid=3857),
        )


def test_factor_implements_protocol_and_rejects_invalid_configuration() -> None:
    grid = make_grid()
    factor = DEMSlopeFactor(
        source=FakeDEMSource(grid, plane_values(width=4, height=4)),
    )

    assert isinstance(factor, SuitabilityFactor)

    with pytest.raises(DEMSlopeError, match="positive finite"):
        DEMSlopeFactor(source=factor.source, vertical_scale_to_m=0.0)
    with pytest.raises(DEMSlopeError, match="positive integer"):
        DEMSlopeFactor(source=factor.source, tile_size=0)
    with pytest.raises(DEMSlopeError, match="at most 4096"):
        DEMSlopeFactor(source=factor.source, tile_size=4097)


def test_slope_requires_two_dimensions() -> None:
    grid = make_grid(width=1, height=2)
    source = FakeDEMSource(grid, ((0.0,), (1.0,)))
    factor = DEMSlopeFactor(source=source)

    with pytest.raises(DEMSlopeError, match="at least 2 x 2"):
        factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())
