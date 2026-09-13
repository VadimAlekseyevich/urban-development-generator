import uuid
from dataclasses import dataclass

import pytest

from core.urban_generator.domain import (
    ConfigRef,
    Constraint,
    ConstraintContractError,
    ConstraintEngine,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    CorrelationMetadata,
    ProjectRef,
    ProjectSettings,
    RunContext,
    RunMode,
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
    ValidationReport,
    validate_constraint_metadata,
)


@dataclass(frozen=True, slots=True)
class DummySubject:
    value: int


class PositiveValueConstraint:
    code = "dummy.positive_value"
    severity = ConstraintSeverity.HARD
    scope = ConstraintScope.BUILDING

    def evaluate(
        self,
        *,
        subject: DummySubject,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ConstraintResult:
        del snapshot, context
        passed = subject.value > 0
        return ConstraintResult(
            code=self.code,
            severity=self.severity,
            scope=self.scope,
            passed=passed,
            message="Value must be positive",
        )


class PreferredValueConstraint:
    code = "dummy.preferred_value"
    severity = ConstraintSeverity.SOFT
    scope = ConstraintScope.BUILDING

    def evaluate(
        self,
        *,
        subject: DummySubject,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ConstraintResult:
        del snapshot, context
        passed = subject.value >= 10
        return ConstraintResult(
            code=self.code,
            severity=self.severity,
            scope=self.scope,
            passed=passed,
            message="Value should be at least 10",
        )


class DummyConstraintEngine:
    def __init__(self) -> None:
        self.constraints = (PositiveValueConstraint(), PreferredValueConstraint())

    def evaluate[SubjectT](
        self,
        *,
        subject: SubjectT,
        stage: str,
        scope: ConstraintScope,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ValidationReport:
        if not isinstance(subject, DummySubject):
            raise TypeError("dummy engine expects DummySubject")
        if stage != "buildings":
            return ValidationReport()
        results = tuple(
            constraint.evaluate(subject=subject, snapshot=snapshot, context=context)
            for constraint in self.constraints
            if constraint.scope is scope
        )
        return ValidationReport(results=results)


def make_snapshot() -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000401"),
        project=ProjectRef(project_id=uuid.UUID("00000000-0000-0000-0000-000000000402")),
        settings=ProjectSettings(working_srid=32637),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
    )


def make_context() -> RunContext:
    return RunContext(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000403"),
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=32637,
        config_refs=(ConfigRef(name="generation", ref="synthetic:generation:v1"),),
        correlation=CorrelationMetadata(correlation_id="constraint-contract-test"),
    )


def test_constraint_and_engine_protocols_work_without_backend() -> None:
    constraint = PositiveValueConstraint()
    engine = DummyConstraintEngine()

    result = constraint.evaluate(
        subject=DummySubject(value=5),
        snapshot=make_snapshot(),
        context=make_context(),
    )
    report = engine.evaluate(
        subject=DummySubject(value=5),
        stage="buildings",
        scope=ConstraintScope.BUILDING,
        snapshot=make_snapshot(),
        context=make_context(),
    )

    assert isinstance(constraint, Constraint)
    assert isinstance(engine, ConstraintEngine)
    assert result.passed is True
    assert report.is_valid is True
    assert len(report.soft_violations) == 1


def test_hard_failure_invalidates_report() -> None:
    report = DummyConstraintEngine().evaluate(
        subject=DummySubject(value=-1),
        stage="buildings",
        scope=ConstraintScope.BUILDING,
        snapshot=make_snapshot(),
        context=make_context(),
    )

    assert report.is_valid is False
    assert len(report.hard_failures) == 1
    assert report.hard_failures[0].code == "dummy.positive_value"


def test_soft_failure_does_not_invalidate_report() -> None:
    report = DummyConstraintEngine().evaluate(
        subject=DummySubject(value=5),
        stage="buildings",
        scope=ConstraintScope.BUILDING,
        snapshot=make_snapshot(),
        context=make_context(),
    )

    assert report.is_valid is True
    assert len(report.soft_violations) == 1
    assert report.soft_violations[0].blocks_generation is False


def test_constraint_metadata_is_validated() -> None:
    validate_constraint_metadata(
        "roads.max_slope",
        ConstraintSeverity.HARD,
        ConstraintScope.ROAD,
    )

    with pytest.raises(ConstraintContractError, match="invalid constraint code"):
        validate_constraint_metadata(
            "INVALID CODE",
            ConstraintSeverity.HARD,
            ConstraintScope.ROAD,
        )


def test_constraint_result_requires_structured_values() -> None:
    with pytest.raises(ConstraintContractError, match="passed flag must be bool"):
        ConstraintResult(
            code="roads.max_slope",
            severity=ConstraintSeverity.HARD,
            scope=ConstraintScope.ROAD,
            passed=1,
            message="Slope limit",
        )

    with pytest.raises(ConstraintContractError, match="non-empty string"):
        ConstraintResult(
            code="roads.max_slope",
            severity=ConstraintSeverity.HARD,
            scope=ConstraintScope.ROAD,
            passed=True,
            message=" ",
        )


def test_validation_report_requires_immutable_results() -> None:
    with pytest.raises(ConstraintContractError, match="immutable tuple"):
        ValidationReport(results=[])
