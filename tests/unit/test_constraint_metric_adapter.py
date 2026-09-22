import math

import pytest
from shapely.geometry import LineString, Polygon, box

from core.urban_generator.domain import (
    CANONICAL_METRIC_REGISTRY,
    ConstraintProblemGeometry,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    MetricSource,
    RawMetricId,
    SoftPenaltyMetadata,
    ValidationReport,
)
from core.urban_generator.metrics import (
    CONSTRAINT_RAW_METRIC_IDS,
    ConstraintMetricAdapter,
    ConstraintMetricAdapterError,
)


def _result(
    code: str,
    *,
    severity: ConstraintSeverity,
    passed: bool,
    geometry=None,
    working_srid: int = 32637,
    soft_penalty: SoftPenaltyMetadata | None = None,
) -> ConstraintResult:
    return ConstraintResult(
        code=code,
        severity=severity,
        scope=ConstraintScope.TERRITORY,
        passed=passed,
        message=f"{code} result",
        problem_geometry=(
            None
            if geometry is None
            else ConstraintProblemGeometry(
                geometry=geometry,
                working_srid=working_srid,
            )
        ),
        soft_penalty=soft_penalty,
    )


def _scalar(result, metric_id: RawMetricId) -> float:
    return result.require(metric_id).scalar_value


def test_adapter_uses_canonical_constraint_registry_order() -> None:
    expected = tuple(
        definition.metric_id
        for definition in CANONICAL_METRIC_REGISTRY.definitions_for(
            source=MetricSource.CONSTRAINTS
        )
    )

    assert CONSTRAINT_RAW_METRIC_IDS == expected
    assert CONSTRAINT_RAW_METRIC_IDS == (
        RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT,
        RawMetricId.CONSTRAINTS_AFFECTED_AREA_M2,
        RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY,
    )


def test_adapter_projects_hard_area_and_soft_penalty_from_report() -> None:
    report = ValidationReport(
        results=(
            _result(
                "constraints.hard.first",
                severity=ConstraintSeverity.HARD,
                passed=False,
                geometry=box(0, 0, 10, 10),
            ),
            _result(
                "constraints.soft.overlap",
                severity=ConstraintSeverity.SOFT,
                passed=False,
                geometry=box(5, 0, 15, 10),
                soft_penalty=SoftPenaltyMetadata(
                    raw_penalty=0.5,
                    weight=4.0,
                ),
            ),
            _result(
                "constraints.soft.line",
                severity=ConstraintSeverity.SOFT,
                passed=False,
                geometry=LineString([(0, 0), (100, 0)]),
                soft_penalty=SoftPenaltyMetadata(
                    raw_penalty=0.25,
                    weight=2.0,
                ),
            ),
            _result(
                "constraints.hard.passing",
                severity=ConstraintSeverity.HARD,
                passed=True,
                geometry=box(100, 100, 200, 200),
            ),
        )
    )

    result = ConstraintMetricAdapter().adapt(report)

    assert tuple(item.metric_id for item in result.raw_metrics) == (
        CONSTRAINT_RAW_METRIC_IDS
    )
    assert _scalar(
        result,
        RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT,
    ) == 1.0
    assert _scalar(
        result,
        RawMetricId.CONSTRAINTS_AFFECTED_AREA_M2,
    ) == 150.0
    assert _scalar(
        result,
        RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY,
    ) == 2.5

    diagnostics = result.diagnostics
    assert diagnostics.result_count == 4
    assert diagnostics.failure_count == 3
    assert diagnostics.hard_failure_count == 1
    assert diagnostics.soft_violation_count == 2
    assert diagnostics.failure_with_problem_geometry_count == 3
    assert diagnostics.polygonal_problem_geometry_count == 2
    assert diagnostics.non_polygonal_problem_geometry_count == 1
    assert diagnostics.missing_problem_geometry_failure_count == 0
    assert diagnostics.soft_violation_with_penalty_count == 2
    assert diagnostics.missing_soft_penalty_count == 0
    assert diagnostics.working_srid == 32637


def test_adapter_empty_report_emits_zero_metrics_and_no_srid() -> None:
    result = ConstraintMetricAdapter().adapt(ValidationReport())

    assert _scalar(
        result,
        RawMetricId.CONSTRAINTS_HARD_VIOLATION_COUNT,
    ) == 0.0
    assert _scalar(
        result,
        RawMetricId.CONSTRAINTS_AFFECTED_AREA_M2,
    ) == 0.0
    assert _scalar(
        result,
        RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY,
    ) == 0.0
    assert result.diagnostics.result_count == 0
    assert result.diagnostics.working_srid is None


def test_adapter_keeps_legacy_soft_violation_without_penalty_compatible() -> None:
    report = ValidationReport(
        results=(
            _result(
                "constraints.soft.legacy",
                severity=ConstraintSeverity.SOFT,
                passed=False,
                geometry=None,
            ),
            _result(
                "constraints.soft.versioned",
                severity=ConstraintSeverity.SOFT,
                passed=False,
                geometry=None,
                soft_penalty=SoftPenaltyMetadata(
                    raw_penalty=1.0,
                    weight=1.5,
                ),
            ),
        )
    )

    result = ConstraintMetricAdapter().adapt(report)

    assert _scalar(
        result,
        RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY,
    ) == 1.5
    assert result.diagnostics.soft_violation_count == 2
    assert result.diagnostics.soft_violation_with_penalty_count == 1
    assert result.diagnostics.missing_soft_penalty_count == 1
    assert result.diagnostics.missing_problem_geometry_failure_count == 2


def test_adapter_repairs_invalid_polygonal_evidence_for_area() -> None:
    bow_tie = Polygon(
        [(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)]
    )
    assert bow_tie.is_valid is False

    report = ValidationReport(
        results=(
            _result(
                "constraints.hard.invalid_geometry",
                severity=ConstraintSeverity.HARD,
                passed=False,
                geometry=bow_tie,
            ),
        )
    )

    result = ConstraintMetricAdapter().adapt(report)

    assert _scalar(
        result,
        RawMetricId.CONSTRAINTS_AFFECTED_AREA_M2,
    ) > 0.0
    assert result.diagnostics.invalid_problem_geometry_count == 1
    assert result.diagnostics.polygonal_problem_geometry_count == 1


def test_adapter_rejects_mixed_failure_problem_geometry_srid() -> None:
    report = ValidationReport(
        results=(
            _result(
                "constraints.hard.srid_a",
                severity=ConstraintSeverity.HARD,
                passed=False,
                geometry=box(0, 0, 1, 1),
                working_srid=32637,
            ),
            _result(
                "constraints.soft.srid_b",
                severity=ConstraintSeverity.SOFT,
                passed=False,
                geometry=box(2, 2, 3, 3),
                working_srid=32636,
                soft_penalty=SoftPenaltyMetadata(
                    raw_penalty=1.0,
                    weight=1.0,
                ),
            ),
        )
    )

    with pytest.raises(
        ConstraintMetricAdapterError,
        match="must use one working_srid",
    ):
        ConstraintMetricAdapter().adapt(report)


def test_adapter_weighted_soft_penalty_is_finite_and_exact_sum() -> None:
    report = ValidationReport(
        results=tuple(
            _result(
                f"constraints.soft.item_{index}",
                severity=ConstraintSeverity.SOFT,
                passed=False,
                soft_penalty=SoftPenaltyMetadata(
                    raw_penalty=0.1,
                    weight=float(index),
                ),
            )
            for index in range(1, 6)
        )
    )

    result = ConstraintMetricAdapter().adapt(report)
    value = _scalar(
        result,
        RawMetricId.CONSTRAINTS_WEIGHTED_SOFT_PENALTY,
    )

    assert math.isfinite(value)
    assert value == pytest.approx(1.5)


def test_adapter_bounds_problem_geometry_union() -> None:
    report = ValidationReport(
        results=(
            _result(
                "constraints.hard.bound_a",
                severity=ConstraintSeverity.HARD,
                passed=False,
                geometry=box(0, 0, 1, 1),
            ),
            _result(
                "constraints.hard.bound_b",
                severity=ConstraintSeverity.HARD,
                passed=False,
                geometry=box(2, 2, 3, 3),
            ),
        )
    )

    with pytest.raises(
        ConstraintMetricAdapterError,
        match="problem geometry limit exceeded",
    ):
        ConstraintMetricAdapter(max_problem_geometries=1).adapt(report)


def test_adapter_requires_positive_problem_geometry_bound() -> None:
    with pytest.raises(
        ConstraintMetricAdapterError,
        match="max_problem_geometries must be a positive integer",
    ):
        ConstraintMetricAdapter(max_problem_geometries=0)
