from __future__ import annotations

import uuid

import pytest
from shapely.geometry import box

from core.urban_generator.buildings import (
    BuildableEnvelopeBuilder,
    BuildingDevelopableMask,
    BuildingEnvelopeError,
    BuildingEnvelopePolicy,
    BuildingEnvelopeSource,
    BuildingEnvelopeSourceKind,
    BuildingEnvelopeStatus,
    EngineBuildingEnvelopeConstraintEvaluator,
)
from core.urban_generator.constraints import (
    ConstraintRegistry,
    GeometryExclusionConstraint,
    GeometryExclusionIndex,
    GeometryExclusionSubject,
    RasterThresholdConstraint,
    RasterThresholdEvaluator,
    RasterThresholdPolicy,
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

WORKING_SRID = 32637


class FakeRasterSource:
    def __init__(self, values: tuple[tuple[float | None, ...], ...]) -> None:
        self.values = values
        self.working_srid = WORKING_SRID
        self.height = len(values)
        self.width = len(values[0])

    def read_window(
        self,
        *,
        window: RasterWindow,
    ) -> tuple[tuple[float | int | None, ...], ...]:
        return tuple(
            tuple(
                self.values[row][col]
                for col in range(window.col_off, window.col_off + window.width)
            )
            for row in range(window.row_off, window.row_off + window.height)
        )


def _snapshot(*, working_srid: int = WORKING_SRID) -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000801"),
        project=ProjectRef(
            project_id=uuid.UUID("00000000-0000-0000-0000-000000000802")
        ),
        settings=ProjectSettings(working_srid=working_srid),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
    )


def _context(*, working_srid: int = WORKING_SRID) -> RunContext:
    return RunContext(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000803"),
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=working_srid,
        config_refs=(
            ConfigRef(name="building", ref="synthetic:building:v1"),
        ),
        correlation=CorrelationMetadata(
            correlation_id="building-envelope-test"
        ),
    )


def _source(
    geometry=box(0, 0, 20, 20),
    *,
    working_srid: int = WORKING_SRID,
) -> BuildingEnvelopeSource:
    return BuildingEnvelopeSource(
        source_id="parcel:test",
        source_kind=BuildingEnvelopeSourceKind.PARCEL,
        geometry=geometry,
        working_srid=working_srid,
    )


def _mask(
    geometry=box(0, 0, 20, 20),
    *,
    working_srid: int = WORKING_SRID,
) -> BuildingDevelopableMask:
    return BuildingDevelopableMask(
        geometry=geometry,
        working_srid=working_srid,
    )


def test_buildable_envelope_applies_developable_mask_then_metric_boundary_setback() -> None:
    builder = BuildableEnvelopeBuilder(
        working_srid=WORKING_SRID,
        policy=BuildingEnvelopePolicy(
            boundary_setback_m=2.0,
            minimum_area_m2=10.0,
        ),
    )

    result = builder.build(
        _source(),
        developable_mask=_mask(box(0, 0, 15, 20)),
        snapshot=_snapshot(),
        context=_context(),
    )

    assert result.status is BuildingEnvelopeStatus.READY
    assert result.is_buildable is True
    assert result.buildable_geometry is not None
    assert result.buildable_geometry.equals(box(2, 2, 13, 18))
    assert result.area_m2 == pytest.approx(176.0)
    assert result.constraint_reports == ()


def test_buildable_envelope_reports_empty_and_minimum_area_outcomes_explicitly() -> None:
    builder = BuildableEnvelopeBuilder(
        working_srid=WORKING_SRID,
        policy=BuildingEnvelopePolicy(
            boundary_setback_m=3.0,
            minimum_area_m2=30.0,
        ),
    )

    masked_out = builder.build(
        _source(box(0, 0, 4, 4)),
        developable_mask=_mask(box(10, 10, 20, 20)),
        snapshot=_snapshot(),
        context=_context(),
    )
    inset_out = builder.build(
        _source(box(0, 0, 4, 4)),
        developable_mask=_mask(box(0, 0, 4, 4)),
        snapshot=_snapshot(),
        context=_context(),
    )
    below = BuildableEnvelopeBuilder(
        working_srid=WORKING_SRID,
        policy=BuildingEnvelopePolicy(
            boundary_setback_m=1.0,
            minimum_area_m2=10.0,
        ),
    ).build(
        _source(box(0, 0, 5, 4)),
        developable_mask=_mask(box(0, 0, 5, 4)),
        snapshot=_snapshot(),
        context=_context(),
    )

    assert masked_out.status is BuildingEnvelopeStatus.EMPTY_AFTER_MASK
    assert masked_out.candidate_geometry is None
    assert inset_out.status is BuildingEnvelopeStatus.EMPTY_AFTER_SETBACK
    assert inset_out.candidate_geometry is None
    assert below.status is BuildingEnvelopeStatus.BELOW_MINIMUM_AREA
    assert below.candidate_geometry is not None
    assert below.is_buildable is False


def test_buildable_envelope_composes_geometry_and_raster_rules_through_engines() -> None:
    geometry_registry = ConstraintRegistry()
    geometry_registry.register(
        stage="buildings",
        constraint=GeometryExclusionConstraint(
            scope=ConstraintScope.BUILDING,
            index=GeometryExclusionIndex(
                boundary=box(-100, -100, 100, 100),
                working_srid=WORKING_SRID,
            ),
        ),
    )
    geometry_evaluator = EngineBuildingEnvelopeConstraintEvaluator(
        evaluation_id="geometry",
        engine=RegisteredConstraintEngine(geometry_registry),
        subject_factory=lambda geometry, srid: GeometryExclusionSubject(
            geometry=geometry,
            working_srid=srid,
        ),
    )

    raster_registry = ConstraintRegistry()
    raster_registry.register(
        stage="buildings",
        constraint=RasterThresholdConstraint(
            scope=ConstraintScope.BUILDING,
            evaluator=RasterThresholdEvaluator(
                source=FakeRasterSource(((12.0,),)),
                policy=RasterThresholdPolicy(threshold=10.0),
            ),
            code="slope.maximum",
        ),
    )
    raster_evaluator = EngineBuildingEnvelopeConstraintEvaluator(
        evaluation_id="slope",
        engine=RegisteredConstraintEngine(raster_registry),
        subject_factory=lambda _geometry, srid: RasterThresholdSubject(
            windows=(RasterWindow(0, 0, 1, 1),),
            working_srid=srid,
        ),
    )

    result = BuildableEnvelopeBuilder(
        working_srid=WORKING_SRID,
    ).build(
        _source(box(0, 0, 10, 10)),
        developable_mask=_mask(box(0, 0, 10, 10)),
        snapshot=_snapshot(),
        context=_context(),
        constraint_evaluators=(
            raster_evaluator,
            geometry_evaluator,
        ),
    )

    assert result.status is BuildingEnvelopeStatus.HARD_CONSTRAINT_FAILED
    assert result.is_buildable is False
    assert result.candidate_geometry is not None
    assert tuple(
        report.evaluation_id for report in result.constraint_reports
    ) == ("geometry", "slope")
    assert result.constraint_reports[0].report.is_valid is True
    assert result.constraint_reports[1].report.is_valid is False
    assert result.constraint_reports[1].report.hard_failures[0].code == (
        "slope.maximum"
    )


def test_buildable_envelope_enforces_crs_part_and_evaluation_bounds() -> None:
    builder = BuildableEnvelopeBuilder(
        working_srid=WORKING_SRID,
        max_output_parts=1,
        max_constraint_evaluations=1,
    )

    with pytest.raises(BuildingEnvelopeError, match="source working_srid"):
        builder.build(
            _source(working_srid=3857),
            developable_mask=_mask(),
            snapshot=_snapshot(),
            context=_context(),
        )

    split_mask = _mask(
        box(0, 0, 4, 4).union(box(10, 0, 14, 4))
    )
    with pytest.raises(BuildingEnvelopeError, match="output part limit exceeded"):
        builder.build(
            _source(box(0, 0, 20, 10)),
            developable_mask=split_mask,
            snapshot=_snapshot(),
            context=_context(),
        )

    empty_registry = ConstraintRegistry()
    evaluator = EngineBuildingEnvelopeConstraintEvaluator(
        evaluation_id="empty",
        engine=RegisteredConstraintEngine(empty_registry),
        subject_factory=lambda geometry, srid: GeometryExclusionSubject(
            geometry=geometry,
            working_srid=srid,
        ),
    )
    with pytest.raises(
        BuildingEnvelopeError,
        match="constraint evaluation limit exceeded",
    ):
        builder.build(
            _source(),
            developable_mask=_mask(),
            snapshot=_snapshot(),
            context=_context(),
            constraint_evaluators=(evaluator, evaluator),
        )


def test_buildable_envelope_policy_rejects_invalid_metric_values() -> None:
    with pytest.raises(BuildingEnvelopeError, match="finite non-negative"):
        BuildingEnvelopePolicy(boundary_setback_m=-1.0)
    with pytest.raises(BuildingEnvelopeError, match="greater than zero"):
        BuildingEnvelopePolicy(minimum_area_m2=0.0)
    with pytest.raises(ValueError, match="not projected"):
        BuildableEnvelopeBuilder(working_srid=4326)
