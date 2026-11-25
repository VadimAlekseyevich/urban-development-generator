import uuid
from dataclasses import dataclass

import pytest

from core.urban_generator.constraints import (
    ConstraintRegistration,
    ConstraintRegistry,
    ConstraintRegistryError,
    RegisteredConstraintEngine,
)
from core.urban_generator.domain import (
    ConfigRef,
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
)


@dataclass(frozen=True, slots=True)
class Candidate:
    value: int


class MinimumConstraint:
    def __init__(
        self,
        *,
        code: str,
        severity: ConstraintSeverity,
        scope: ConstraintScope,
        minimum: int,
    ) -> None:
        self.code = code
        self.severity = severity
        self.scope = scope
        self.minimum = minimum
        self.calls = 0

    def evaluate(
        self,
        *,
        subject: Candidate,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ConstraintResult:
        del snapshot, context
        self.calls += 1
        return ConstraintResult(
            code=self.code,
            severity=self.severity,
            scope=self.scope,
            passed=subject.value >= self.minimum,
            message=f"value must be >= {self.minimum}",
        )


class MismatchedResultConstraint:
    code = "roads.expected"
    severity = ConstraintSeverity.HARD
    scope = ConstraintScope.ROAD

    def evaluate(
        self,
        *,
        subject: Candidate,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ConstraintResult:
        del subject, snapshot, context
        return ConstraintResult(
            code="roads.actual",
            severity=self.severity,
            scope=self.scope,
            passed=True,
            message="bad metadata",
        )


def make_snapshot() -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000501"),
        project=ProjectRef(project_id=uuid.UUID("00000000-0000-0000-0000-000000000502")),
        settings=ProjectSettings(working_srid=32637),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
    )


def make_context() -> RunContext:
    return RunContext(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000503"),
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=32637,
        config_refs=(ConfigRef(name="generation", ref="synthetic:generation:v1"),),
        correlation=CorrelationMetadata(correlation_id="constraint-registry-test"),
    )


def test_registry_filters_by_exact_stage_and_scope() -> None:
    building = MinimumConstraint(
        code="buildings.minimum",
        severity=ConstraintSeverity.HARD,
        scope=ConstraintScope.BUILDING,
        minimum=5,
    )
    validation = MinimumConstraint(
        code="validation.preferred",
        severity=ConstraintSeverity.SOFT,
        scope=ConstraintScope.BUILDING,
        minimum=10,
    )
    road = MinimumConstraint(
        code="roads.minimum",
        severity=ConstraintSeverity.HARD,
        scope=ConstraintScope.ROAD,
        minimum=1,
    )
    registry = ConstraintRegistry()
    registry.register(stage="buildings", constraint=building)
    registry.register(stage="validation", constraint=validation)
    registry.register(stage="buildings", constraint=road)
    engine = RegisteredConstraintEngine(registry)

    report = engine.evaluate(
        subject=Candidate(value=3),
        stage="buildings",
        scope=ConstraintScope.BUILDING,
        snapshot=make_snapshot(),
        context=make_context(),
    )

    assert isinstance(engine, ConstraintEngine)
    assert [result.code for result in report.results] == ["buildings.minimum"]
    assert report.is_valid is False
    assert building.calls == 1
    assert validation.calls == 0
    assert road.calls == 0


def test_registry_returns_rules_in_stable_code_order() -> None:
    registry = ConstraintRegistry()
    registry.register(
        stage="roads",
        constraint=MinimumConstraint(
            code="roads.z_last",
            severity=ConstraintSeverity.SOFT,
            scope=ConstraintScope.ROAD,
            minimum=0,
        ),
    )
    registry.register(
        stage="roads",
        constraint=MinimumConstraint(
            code="roads.a_first",
            severity=ConstraintSeverity.HARD,
            scope=ConstraintScope.ROAD,
            minimum=0,
        ),
    )

    report = RegisteredConstraintEngine(registry).evaluate(
        subject=Candidate(value=1),
        stage="roads",
        scope=ConstraintScope.ROAD,
        snapshot=make_snapshot(),
        context=make_context(),
    )

    assert [result.code for result in report.results] == [
        "roads.a_first",
        "roads.z_last",
    ]


def test_duplicate_binding_is_rejected_but_same_code_can_bind_another_stage() -> None:
    constraint = MinimumConstraint(
        code="roads.minimum",
        severity=ConstraintSeverity.HARD,
        scope=ConstraintScope.ROAD,
        minimum=1,
    )
    registry = ConstraintRegistry()
    registry.register(stage="roads", constraint=constraint)
    registry.register(stage="validation", constraint=constraint)

    with pytest.raises(ConstraintRegistryError, match="duplicate constraint registration"):
        registry.register(stage="roads", constraint=constraint)

    assert len(registry.registrations) == 2


def test_constructor_requires_immutable_registrations_and_valid_stage() -> None:
    constraint = MinimumConstraint(
        code="roads.minimum",
        severity=ConstraintSeverity.HARD,
        scope=ConstraintScope.ROAD,
        minimum=1,
    )

    with pytest.raises(ConstraintRegistryError, match="immutable tuple"):
        ConstraintRegistry(
            registrations=[
                ConstraintRegistration(stage="roads", constraint=constraint),
            ]
        )

    with pytest.raises(ConstraintRegistryError, match="invalid constraint stage"):
        ConstraintRegistration(stage="Roads.v1", constraint=constraint)


def test_empty_stage_scope_pair_returns_valid_empty_report() -> None:
    report = RegisteredConstraintEngine(ConstraintRegistry()).evaluate(
        subject=Candidate(value=-100),
        stage="suitability",
        scope=ConstraintScope.TERRITORY,
        snapshot=make_snapshot(),
        context=make_context(),
    )

    assert report.results == ()
    assert report.is_valid is True


def test_engine_rejects_constraint_that_lies_about_result_metadata() -> None:
    registry = ConstraintRegistry()
    registry.register(stage="roads", constraint=MismatchedResultConstraint())

    with pytest.raises(ConstraintContractError, match="mismatched code"):
        RegisteredConstraintEngine(registry).evaluate(
            subject=Candidate(value=1),
            stage="roads",
            scope=ConstraintScope.ROAD,
            snapshot=make_snapshot(),
            context=make_context(),
        )


def test_hard_and_soft_results_use_existing_validation_report_semantics() -> None:
    registry = ConstraintRegistry(
        registrations=(
            ConstraintRegistration(
                stage="buildings",
                constraint=MinimumConstraint(
                    code="buildings.hard",
                    severity=ConstraintSeverity.HARD,
                    scope=ConstraintScope.BUILDING,
                    minimum=10,
                ),
            ),
            ConstraintRegistration(
                stage="buildings",
                constraint=MinimumConstraint(
                    code="buildings.soft",
                    severity=ConstraintSeverity.SOFT,
                    scope=ConstraintScope.BUILDING,
                    minimum=20,
                ),
            ),
        )
    )

    report = RegisteredConstraintEngine(registry).evaluate(
        subject=Candidate(value=5),
        stage="buildings",
        scope=ConstraintScope.BUILDING,
        snapshot=make_snapshot(),
        context=make_context(),
    )

    assert report.is_valid is False
    assert [result.code for result in report.hard_failures] == ["buildings.hard"]
    assert [result.code for result in report.soft_violations] == ["buildings.soft"]
