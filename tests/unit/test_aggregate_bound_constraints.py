import uuid

import pytest

from core.urban_generator.constraints import (
    CANONICAL_AGGREGATE_BOUND_METRIC_IDS,
    AggregateBoundError,
    AggregateConstraintSubject,
    AggregateMetricBound,
    AggregateMetricValue,
    ConstraintRegistry,
    RegisteredConstraintEngine,
    build_aggregate_bound_registrations,
)
from core.urban_generator.domain import (
    ConstraintScope,
    ConstraintSeverity,
    RawMetricId,
)
from core.urban_generator.domain.project import ProjectRef, ProjectSettings
from core.urban_generator.domain.run_context import CorrelationMetadata, RunContext
from core.urban_generator.domain.semantics import RunMode
from core.urban_generator.domain.territory import (
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)
from core.urban_generator.stages.catalog import FINAL_VALIDATION_STAGE

WORKING_SRID = 32637


def _bounds() -> tuple[AggregateMetricBound, ...]:
    return (
        AggregateMetricBound(
            metric_id=RawMetricId.BUILDINGS_COVERAGE_RATIO,
            minimum=0.25,
            maximum=0.75,
        ),
        AggregateMetricBound(
            metric_id=RawMetricId.BUILDINGS_FAR,
            minimum=0.5,
            maximum=3.0,
        ),
        AggregateMetricBound(
            metric_id=RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
            minimum=1_000.0,
            maximum=12_000.0,
        ),
        AggregateMetricBound(
            metric_id=RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
            minimum=0.3,
            maximum=1.0,
        ),
    )


def _subject(
    *,
    coverage: float = 0.5,
    far: float = 1.5,
    density: float = 5_000.0,
    capacity_utilization: float = 0.8,
) -> AggregateConstraintSubject:
    return AggregateConstraintSubject(
        values=(
            AggregateMetricValue(
                RawMetricId.BUILDINGS_COVERAGE_RATIO,
                coverage,
            ),
            AggregateMetricValue(RawMetricId.BUILDINGS_FAR, far),
            AggregateMetricValue(
                RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
                density,
            ),
            AggregateMetricValue(
                RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
                capacity_utilization,
            ),
        )
    )


def _snapshot(*, working_srid: int = WORKING_SRID) -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.uuid4(),
        project=ProjectRef(project_id=uuid.uuid4()),
        settings=ProjectSettings(working_srid=working_srid),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="boundary:aggregate-constraint-test",
        ),
    )


def _context(*, working_srid: int = WORKING_SRID) -> RunContext:
    return RunContext(
        run_id=uuid.uuid4(),
        mode=RunMode.FROM_SCRATCH,
        seed=42,
        working_srid=working_srid,
        config_refs=(),
        correlation=CorrelationMetadata(
            correlation_id="aggregate-constraint-test",
        ),
    )


def _engine() -> RegisteredConstraintEngine:
    return RegisteredConstraintEngine(
        ConstraintRegistry(build_aggregate_bound_registrations(_bounds()))
    )


def test_canonical_aggregate_bound_metrics_reuse_raw_metric_vocabulary() -> None:
    assert CANONICAL_AGGREGATE_BOUND_METRIC_IDS == (
        RawMetricId.BUILDINGS_COVERAGE_RATIO,
        RawMetricId.BUILDINGS_FAR,
        RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
        RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
    )


def test_registrations_bind_all_aggregate_rules_to_final_validation() -> None:
    registrations = build_aggregate_bound_registrations(tuple(reversed(_bounds())))

    assert tuple(item.stage for item in registrations) == (
        FINAL_VALIDATION_STAGE,
    ) * 4
    assert tuple(item.constraint.scope for item in registrations) == (
        ConstraintScope.TERRITORY,
    ) * 4
    assert tuple(item.constraint.severity for item in registrations) == (
        ConstraintSeverity.HARD,
    ) * 4
    assert tuple(item.constraint.code for item in registrations) == tuple(
        sorted(
            f"aggregate.{metric_id.value}"
            for metric_id in CANONICAL_AGGREGATE_BOUND_METRIC_IDS
        )
    )


def test_engine_accepts_values_on_inclusive_aggregate_boundaries() -> None:
    report = _engine().evaluate(
        subject=_subject(
            coverage=0.25,
            far=3.0,
            density=1_000.0,
            capacity_utilization=1.0,
        ),
        stage=FINAL_VALIDATION_STAGE,
        scope=ConstraintScope.TERRITORY,
        snapshot=_snapshot(),
        context=_context(),
    )

    assert len(report.results) == 4
    assert all(result.passed for result in report.results)
    assert report.is_valid is True


@pytest.mark.parametrize(
    ("field", "value", "metric_id", "message"),
    (
        (
            "coverage",
            0.24,
            RawMetricId.BUILDINGS_COVERAGE_RATIO,
            "below minimum",
        ),
        (
            "far",
            3.01,
            RawMetricId.BUILDINGS_FAR,
            "exceeds maximum",
        ),
        (
            "density",
            999.0,
            RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
            "below minimum",
        ),
        (
            "capacity_utilization",
            1.01,
            RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
            "exceeds maximum",
        ),
    ),
)
def test_engine_reports_hard_aggregate_bound_failures(
    field: str,
    value: float,
    metric_id: RawMetricId,
    message: str,
) -> None:
    subject_values = {
        "coverage": 0.5,
        "far": 1.5,
        "density": 5_000.0,
        "capacity_utilization": 0.8,
    }
    subject_values[field] = value

    report = _engine().evaluate(
        subject=_subject(**subject_values),
        stage=FINAL_VALIDATION_STAGE,
        scope=ConstraintScope.TERRITORY,
        snapshot=_snapshot(),
        context=_context(),
    )

    failure = next(
        result
        for result in report.hard_failures
        if result.code == f"aggregate.{metric_id.value}"
    )
    assert message in failure.message
    assert report.is_valid is False


def test_non_final_stage_has_no_aggregate_rules() -> None:
    registry = ConstraintRegistry(build_aggregate_bound_registrations(_bounds()))
    engine = RegisteredConstraintEngine(registry)

    report = engine.evaluate(
        subject=_subject(),
        stage="metrics",
        scope=ConstraintScope.TERRITORY,
        snapshot=_snapshot(),
        context=_context(),
    )

    assert report.results == ()
    assert report.is_valid is True


def test_subject_requires_unique_finite_metric_values() -> None:
    duplicate = AggregateMetricValue(RawMetricId.BUILDINGS_FAR, 1.0)

    with pytest.raises(AggregateBoundError, match="duplicate"):
        AggregateConstraintSubject(values=(duplicate, duplicate))

    with pytest.raises(AggregateBoundError, match="finite"):
        AggregateMetricValue(RawMetricId.BUILDINGS_FAR, float("nan"))


def test_engine_rejects_missing_metric_and_crs_mismatch() -> None:
    subject = AggregateConstraintSubject(
        values=(
            AggregateMetricValue(RawMetricId.BUILDINGS_COVERAGE_RATIO, 0.5),
        )
    )

    with pytest.raises(AggregateBoundError, match="missing"):
        _engine().evaluate(
            subject=subject,
            stage=FINAL_VALIDATION_STAGE,
            scope=ConstraintScope.TERRITORY,
            snapshot=_snapshot(),
            context=_context(),
        )

    with pytest.raises(AggregateBoundError, match="working_srid"):
        _engine().evaluate(
            subject=_subject(),
            stage=FINAL_VALIDATION_STAGE,
            scope=ConstraintScope.TERRITORY,
            snapshot=_snapshot(working_srid=32636),
            context=_context(),
        )


def test_bound_registration_contract_rejects_partial_duplicate_or_invalid_bounds() -> None:
    with pytest.raises(AggregateBoundError, match="exactly"):
        build_aggregate_bound_registrations(_bounds()[:-1])

    duplicate_bounds = (*_bounds()[:-1], _bounds()[0])
    with pytest.raises(AggregateBoundError, match="duplicate"):
        build_aggregate_bound_registrations(duplicate_bounds)

    with pytest.raises(AggregateBoundError, match="must not exceed"):
        AggregateMetricBound(
            metric_id=RawMetricId.BUILDINGS_FAR,
            minimum=3.0,
            maximum=2.0,
        )

    with pytest.raises(AggregateBoundError, match="minimum and/or maximum"):
        AggregateMetricBound(metric_id=RawMetricId.BUILDINGS_FAR)
