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
from core.urban_generator.suitability import SuitabilityFactor, SuitabilityGridSpec
from core.urban_generator.suitability.landuse import (
    LanduseClassWeight,
    LanduseClassWeights,
    LanduseFactor,
    LanduseFactorError,
    LanduseUnknownClassPolicy,
    LanduseWindow,
)


class FakeLanduseSource:
    def __init__(
        self,
        grid: SuitabilityGridSpec,
        values: tuple[tuple[str | None, ...], ...],
    ) -> None:
        self.grid = grid
        self.values = values
        self.reads: list[LanduseWindow] = []

    def read_window(
        self,
        *,
        window: LanduseWindow,
    ) -> tuple[tuple[str | None, ...], ...]:
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
    height: int = 3,
    working_srid: int = 32637,
) -> SuitabilityGridSpec:
    return SuitabilityGridSpec(
        working_srid=working_srid,
        bounds=(500_000.0, 6_000_000.0, 500_000.0 + width * 10.0, 6_000_000.0 + height * 10.0),
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
        landuse=(
            SnapshotLayerRef(
                kind=SnapshotLayerKind.LANDUSE,
                source_ref="synthetic:landuse:v1",
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
        correlation=CorrelationMetadata(correlation_id="landuse-factor-test"),
    )


def make_weights(
    *,
    unknown_policy: LanduseUnknownClassPolicy = LanduseUnknownClassPolicy.INVALIDATE,
    default_score: float | None = None,
) -> LanduseClassWeights:
    return LanduseClassWeights(
        version="ryazan-demo-v1",
        classes=(
            LanduseClassWeight("residential", 0.9),
            LanduseClassWeight("commercial", 0.8),
            LanduseClassWeight("industrial", 0.3),
            LanduseClassWeight("forest", 0.1),
        ),
        unknown_policy=unknown_policy,
        default_score=default_score,
    )


def test_known_classes_map_to_normalized_scores_exactly() -> None:
    grid = make_grid(width=4, height=2)
    source = FakeLanduseSource(
        grid,
        (
            ("residential", "commercial", "industrial", "forest"),
            ("forest", "industrial", "commercial", "residential"),
        ),
    )
    factor = LanduseFactor(source=source, weights=make_weights(), tile_size=2)

    result = factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    assert np.all(result.valid_mask)
    assert np.allclose(
        result.values,
        np.array(
            [
                [0.9, 0.8, 0.3, 0.1],
                [0.1, 0.3, 0.8, 0.9],
            ],
            dtype=np.float64,
        ),
    )
    assert result.code == "landuse"
    assert result.version == "v1"
    assert result.values.flags.writeable is False
    assert result.valid_mask.flags.writeable is False


def test_unknown_and_nodata_are_invalid_by_default() -> None:
    grid = make_grid(width=3, height=2)
    source = FakeLanduseSource(
        grid,
        (
            ("residential", "unknown", None),
            ("forest", "industrial", "commercial"),
        ),
    )
    factor = LanduseFactor(source=source, weights=make_weights(), tile_size=3)

    result = factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    assert bool(result.valid_mask[0, 0]) is True
    assert bool(result.valid_mask[0, 1]) is False
    assert bool(result.valid_mask[0, 2]) is False
    assert result.values[0, 1] == 0.0
    assert result.values[0, 2] == 0.0
    assert "unknown_cells=1" in result.diagnostics
    assert "nodata_cells=1" in result.diagnostics


def test_use_default_scores_unknown_classes_but_not_nodata() -> None:
    grid = make_grid(width=3, height=1)
    source = FakeLanduseSource(grid, (("residential", "other", None),))
    factor = LanduseFactor(
        source=source,
        weights=make_weights(
            unknown_policy=LanduseUnknownClassPolicy.USE_DEFAULT,
            default_score=0.45,
        ),
    )

    result = factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    assert result.values[0, 0] == pytest.approx(0.9)
    assert result.values[0, 1] == pytest.approx(0.45)
    assert bool(result.valid_mask[0, 1]) is True
    assert bool(result.valid_mask[0, 2]) is False
    assert "defaulted_cells=1" in result.diagnostics


def test_class_weights_are_versioned_and_fingerprint_is_stable() -> None:
    first = make_weights()
    same = make_weights()
    changed = LanduseClassWeights(
        version="ryazan-demo-v2",
        classes=first.classes,
    )

    assert first.score_for("residential") == pytest.approx(0.9)
    assert first.score_for("unknown") is None
    assert first.fingerprint == same.fingerprint
    assert first.fingerprint != changed.fingerprint
    assert len(first.fingerprint) == 64


def test_windowing_is_bounded_and_row_major() -> None:
    grid = make_grid(width=5, height=4)
    source = FakeLanduseSource(
        grid,
        tuple(tuple("residential" for _ in range(5)) for _ in range(4)),
    )
    factor = LanduseFactor(source=source, weights=make_weights(), tile_size=2)

    factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    assert len(source.reads) == 6
    assert source.reads[0] == LanduseWindow(row_off=0, col_off=0, height=2, width=2)
    assert source.reads[1] == LanduseWindow(row_off=0, col_off=2, height=2, width=2)
    assert source.reads[-1] == LanduseWindow(row_off=2, col_off=4, height=2, width=1)
    assert max(window.cell_count for window in source.reads) <= 4


def test_cell_limit_is_checked_before_source_reads() -> None:
    grid = make_grid(width=5, height=5)
    source = FakeLanduseSource(
        grid,
        tuple(tuple("residential" for _ in range(5)) for _ in range(5)),
    )
    factor = LanduseFactor(source=source, weights=make_weights(), max_cells=24)

    with pytest.raises(LanduseFactorError, match="grid cell limit exceeded"):
        factor.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    assert source.reads == []


def test_source_shape_and_class_contract_are_validated() -> None:
    grid = make_grid(width=2, height=2)

    class WrongShapeSource(FakeLanduseSource):
        def read_window(
            self,
            *,
            window: LanduseWindow,
        ) -> tuple[tuple[str | None, ...], ...]:
            self.reads.append(window)
            return (("residential",),)

    wrong = LanduseFactor(
        source=WrongShapeSource(grid, (("residential", "forest"),) * 2),
        weights=make_weights(),
        tile_size=2,
    )
    with pytest.raises(LanduseFactorError, match="unexpected window"):
        wrong.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())

    bad_class = LanduseFactor(
        source=FakeLanduseSource(grid, ((" residential", "forest"),) * 2),
        weights=make_weights(),
        tile_size=2,
    )
    with pytest.raises(LanduseFactorError, match="non-empty, trimmed"):
        bad_class.evaluate(grid=grid, snapshot=make_snapshot(), context=make_context())


def test_source_grid_snapshot_and_context_must_match() -> None:
    grid = make_grid()
    source = FakeLanduseSource(
        grid,
        tuple(tuple("residential" for _ in range(grid.width)) for _ in range(grid.height)),
    )
    factor = LanduseFactor(source=source, weights=make_weights())
    other_grid = SuitabilityGridSpec(
        working_srid=32637,
        bounds=(500_010.0, 6_000_000.0, 500_050.0, 6_000_030.0),
        width=4,
        height=3,
    )

    with pytest.raises(LanduseFactorError, match="exactly the requested"):
        factor.evaluate(grid=other_grid, snapshot=make_snapshot(), context=make_context())
    with pytest.raises(LanduseFactorError, match="snapshot working_srid"):
        factor.evaluate(
            grid=grid,
            snapshot=make_snapshot(working_srid=3857),
            context=make_context(),
        )
    with pytest.raises(LanduseFactorError, match="run context working_srid"):
        factor.evaluate(
            grid=grid,
            snapshot=make_snapshot(),
            context=make_context(working_srid=3857),
        )


def test_factor_implements_protocol_and_invalid_configuration_is_rejected() -> None:
    grid = make_grid()
    source = FakeLanduseSource(
        grid,
        tuple(tuple("residential" for _ in range(grid.width)) for _ in range(grid.height)),
    )
    factor = LanduseFactor(source=source, weights=make_weights())

    assert isinstance(factor, SuitabilityFactor)

    with pytest.raises(LanduseFactorError, match="inside 0..1"):
        LanduseClassWeight("residential", 1.1)
    with pytest.raises(LanduseFactorError, match="unique"):
        LanduseClassWeights(
            version="v1",
            classes=(
                LanduseClassWeight("residential", 0.8),
                LanduseClassWeight("residential", 0.7),
            ),
        )
    with pytest.raises(LanduseFactorError, match="USE_DEFAULT requires"):
        LanduseClassWeights(
            version="v1",
            classes=(LanduseClassWeight("residential", 0.8),),
            unknown_policy=LanduseUnknownClassPolicy.USE_DEFAULT,
        )
    with pytest.raises(LanduseFactorError, match="must not define"):
        LanduseClassWeights(
            version="v1",
            classes=(LanduseClassWeight("residential", 0.8),),
            default_score=0.5,
        )
    with pytest.raises(LanduseFactorError, match="positive integer"):
        LanduseFactor(source=source, weights=make_weights(), tile_size=0)
    with pytest.raises(LanduseFactorError, match="at most 4096"):
        LanduseFactor(source=source, weights=make_weights(), tile_size=4097)
