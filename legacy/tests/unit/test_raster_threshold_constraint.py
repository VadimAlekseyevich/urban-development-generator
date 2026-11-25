import uuid

import pytest

from core.urban_generator.constraints import (
    ConstraintRegistry,
    RasterNoDataPolicy,
    RasterThresholdComparison,
    RasterThresholdConstraint,
    RasterThresholdError,
    RasterThresholdEvaluator,
    RasterThresholdHitReason,
    RasterThresholdPolicy,
    RasterThresholdSampleLimitError,
    RasterThresholdSubject,
    RasterWindow,
    RegisteredConstraintEngine,
)
from core.urban_generator.domain import (
    ConfigRef,
    ConstraintScope,
    CorrelationMetadata,
    ProjectRef,
    ProjectSettings,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)


class FakeRasterSource:
    def __init__(
        self,
        values: tuple[tuple[float | int | None, ...], ...],
        *,
        working_srid: int = 32637,
    ) -> None:
        self.values = values
        self.working_srid = working_srid
        self.height = len(values)
        self.width = len(values[0])
        self.reads: list[RasterWindow] = []

    def read_window(
        self,
        *,
        window: RasterWindow,
    ) -> tuple[tuple[float | int | None, ...], ...]:
        self.reads.append(window)
        return tuple(
            tuple(
                self.values[row][col]
                for col in range(window.col_off, window.col_off + window.width)
            )
            for row in range(window.row_off, window.row_off + window.height)
        )


def make_snapshot(*, working_srid: int = 32637) -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000701"),
        project=ProjectRef(project_id=uuid.UUID("00000000-0000-0000-0000-000000000702")),
        settings=ProjectSettings(working_srid=working_srid),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
    )


def make_context(*, working_srid: int = 32637) -> RunContext:
    return RunContext(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000703"),
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=working_srid,
        config_refs=(ConfigRef(name="generation", ref="synthetic:generation:v1"),),
        correlation=CorrelationMetadata(correlation_id="raster-threshold-test"),
    )


def subject(
    *windows: RasterWindow,
    working_srid: int = 32637,
) -> RasterThresholdSubject:
    return RasterThresholdSubject(windows=windows, working_srid=working_srid)


def test_at_most_threshold_passes_and_reports_first_violation_deterministically() -> None:
    source = FakeRasterSource(((1.0, 2.0, 3.0), (4.0, 5.0, 6.0)))
    evaluator = RasterThresholdEvaluator(
        source=source,
        policy=RasterThresholdPolicy(threshold=4.5),
    )

    allowed = evaluator.check(subject(RasterWindow(row_off=0, col_off=0, height=1, width=3)))
    blocked = evaluator.check(subject(RasterWindow(row_off=1, col_off=0, height=1, width=3)))

    assert allowed is None
    assert blocked is not None
    assert blocked.reason is RasterThresholdHitReason.THRESHOLD
    assert blocked.row == 1
    assert blocked.col == 1
    assert blocked.value == 5.0


def test_at_least_threshold_is_supported() -> None:
    source = FakeRasterSource(((10.0, 9.0), (8.0, 7.0)))
    evaluator = RasterThresholdEvaluator(
        source=source,
        policy=RasterThresholdPolicy(
            threshold=8.0,
            comparison=RasterThresholdComparison.AT_LEAST,
        ),
    )

    hit = evaluator.check(subject(RasterWindow(row_off=0, col_off=0, height=2, width=2)))

    assert hit is not None
    assert hit.row == 1
    assert hit.col == 1
    assert hit.value == 7.0


def test_nodata_reject_and_ignore_policies_are_explicit() -> None:
    source = FakeRasterSource(((None, 2.0), (3.0, 4.0)))
    reject = RasterThresholdEvaluator(
        source=source,
        policy=RasterThresholdPolicy(threshold=10.0),
    )
    ignore = RasterThresholdEvaluator(
        source=source,
        policy=RasterThresholdPolicy(
            threshold=10.0,
            nodata_policy=RasterNoDataPolicy.IGNORE,
        ),
    )
    window = RasterWindow(row_off=0, col_off=0, height=2, width=2)

    rejected = reject.check(subject(window))

    assert rejected is not None
    assert rejected.reason is RasterThresholdHitReason.NODATA
    assert ignore.check(subject(window)) is None


def test_all_nodata_fails_closed_even_when_nodata_is_ignored() -> None:
    evaluator = RasterThresholdEvaluator(
        source=FakeRasterSource(((None, None),)),
        policy=RasterThresholdPolicy(
            threshold=10.0,
            nodata_policy=RasterNoDataPolicy.IGNORE,
        ),
    )

    hit = evaluator.check(subject(RasterWindow(row_off=0, col_off=0, height=1, width=2)))

    assert hit is not None
    assert hit.reason is RasterThresholdHitReason.NO_VALID_SAMPLES


def test_sampling_is_windowed_and_bounded_before_source_reads() -> None:
    source = FakeRasterSource(tuple(tuple(float(col) for col in range(10)) for _ in range(10)))
    evaluator = RasterThresholdEvaluator(
        source=source,
        policy=RasterThresholdPolicy(
            threshold=100.0,
            max_windows=2,
            max_sample_cells=4,
        ),
    )

    with pytest.raises(RasterThresholdSampleLimitError, match="window limit exceeded"):
        evaluator.check(
            subject(
                RasterWindow(0, 0, 1, 1),
                RasterWindow(1, 1, 1, 1),
                RasterWindow(2, 2, 1, 1),
            )
        )
    assert source.reads == []

    with pytest.raises(RasterThresholdSampleLimitError, match="sample-cell limit exceeded"):
        evaluator.check(subject(RasterWindow(0, 0, 1, 5)))
    assert source.reads == []


def test_window_bounds_and_source_shape_are_validated() -> None:
    source = FakeRasterSource(((1.0, 2.0), (3.0, 4.0)))
    evaluator = RasterThresholdEvaluator(
        source=source,
        policy=RasterThresholdPolicy(threshold=10.0),
    )

    with pytest.raises(RasterThresholdError, match="exceeds source width"):
        evaluator.check(subject(RasterWindow(0, 1, 1, 2)))

    class BrokenSource(FakeRasterSource):
        def read_window(
            self,
            *,
            window: RasterWindow,
        ) -> tuple[tuple[float | int | None, ...], ...]:
            return ((1.0,),)

    broken = RasterThresholdEvaluator(
        source=BrokenSource(((1.0, 2.0), (3.0, 4.0))),
        policy=RasterThresholdPolicy(threshold=10.0),
    )
    with pytest.raises(RasterThresholdError, match="unexpected raster window width"):
        broken.check(subject(RasterWindow(0, 0, 1, 2)))


def test_metric_crs_must_match_source_subject_snapshot_and_context() -> None:
    evaluator = RasterThresholdEvaluator(
        source=FakeRasterSource(((1.0,),)),
        policy=RasterThresholdPolicy(threshold=2.0),
    )

    with pytest.raises(RasterThresholdError, match="subject working_srid"):
        evaluator.check(
            subject(RasterWindow(0, 0, 1, 1), working_srid=3857)
        )

    rule = RasterThresholdConstraint(scope=ConstraintScope.BUILDING, evaluator=evaluator)
    with pytest.raises(RasterThresholdError, match="snapshot working_srid"):
        rule.evaluate(
            subject=subject(RasterWindow(0, 0, 1, 1)),
            snapshot=make_snapshot(working_srid=3857),
            context=make_context(),
        )
    with pytest.raises(RasterThresholdError, match="run context working_srid"):
        rule.evaluate(
            subject=subject(RasterWindow(0, 0, 1, 1)),
            snapshot=make_snapshot(),
            context=make_context(working_srid=3857),
        )


def test_constraint_composes_with_registry_engine() -> None:
    evaluator = RasterThresholdEvaluator(
        source=FakeRasterSource(((2.0, 12.0),)),
        policy=RasterThresholdPolicy(threshold=10.0),
    )
    rule = RasterThresholdConstraint(
        scope=ConstraintScope.BUILDING,
        evaluator=evaluator,
        code="slope.maximum",
    )
    registry = ConstraintRegistry()
    registry.register(stage="buildings", constraint=rule)
    engine = RegisteredConstraintEngine(registry)

    allowed = engine.evaluate(
        subject=subject(RasterWindow(0, 0, 1, 1)),
        stage="buildings",
        scope=ConstraintScope.BUILDING,
        snapshot=make_snapshot(),
        context=make_context(),
    )
    blocked = engine.evaluate(
        subject=subject(RasterWindow(0, 1, 1, 1)),
        stage="buildings",
        scope=ConstraintScope.BUILDING,
        snapshot=make_snapshot(),
        context=make_context(),
    )

    assert allowed.is_valid is True
    assert blocked.is_valid is False
    assert "raster value 12" in blocked.results[0].message
    assert "at most 10" in blocked.results[0].message


def test_invalid_policy_and_subject_inputs_are_rejected() -> None:
    with pytest.raises(RasterThresholdError, match="finite number"):
        RasterThresholdPolicy(threshold=float("nan"))
    with pytest.raises(RasterThresholdError, match="positive integer"):
        RasterThresholdPolicy(threshold=1.0, max_sample_cells=0)
    with pytest.raises(RasterThresholdError, match="dimensions must be positive"):
        RasterWindow(row_off=0, col_off=0, height=0, width=1)
    with pytest.raises(RasterThresholdError, match="at least one window"):
        RasterThresholdSubject(windows=(), working_srid=32637)
