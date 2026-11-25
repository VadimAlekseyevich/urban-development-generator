from __future__ import annotations

import uuid
from typing import cast

import pytest
from shapely.geometry import Point, box

from core.urban_generator.domain.constraints import (
    ConstraintEngine,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    ValidationReport,
)
from core.urban_generator.domain.project import ProjectRef, ProjectSettings
from core.urban_generator.domain.run_context import CorrelationMetadata, RunContext
from core.urban_generator.domain.semantics import RunMode
from core.urban_generator.domain.territory import (
    SnapshotLayerKind,
    SnapshotLayerRef,
    TerritorySnapshot,
)
from core.urban_generator.zoning import (
    EvaluatedZoneConstraints,
    ZoneAssignment,
    ZoneAssignmentResult,
    ZoneClass,
    ZoneConstraintEvaluationError,
    ZoneConstraintEvaluator,
    ZoneConstraintSubject,
    ZoneShareDiagnostic,
    ZoningPartitionCell,
    ZoningPartitionResult,
    ZoningSeed,
)

WORKING_SRID = 32637


def make_partition(count: int = 2) -> ZoningPartitionResult:
    cells: list[ZoningPartitionCell] = []
    for index in range(count):
        geometry = box(float(index), 0.0, float(index + 1), 1.0)
        seed = ZoningSeed(
            row=0,
            col=index,
            x_m=index + 0.5,
            y_m=0.5,
            suitability_score=0.9 - index * 0.1,
        )
        cells.append(
            ZoningPartitionCell(
                seed_index=index,
                seed=seed,
                geometry=geometry,
                area_m2=1.0,
                validity_repaired=False,
            )
        )
    return ZoningPartitionResult(
        working_srid=WORKING_SRID,
        developable_area=box(0.0, 0.0, float(count), 1.0),
        cells=tuple(cells),
        developable_area_m2=float(count),
        covered_area_m2=float(count),
        uncovered_area_m2=0.0,
        overlap_area_m2=0.0,
        developable_validity_repaired=False,
        repaired_cell_count=0,
    )


def make_assignment(
    partition: ZoningPartitionResult,
    *,
    seed_indexes: tuple[int, ...] | None = None,
) -> ZoneAssignmentResult:
    labels = tuple(
        ZoneClass.RESIDENTIAL if index % 2 == 0 else ZoneClass.MIXED
        for index in range(len(partition.cells))
    )
    if seed_indexes is None:
        seed_indexes = tuple(cell.seed_index for cell in partition.cells)
    assignments = tuple(
        ZoneAssignment(
            cell_index=index,
            seed_index=seed_indexes[index],
            zone_class=labels[index],
            area_m2=cell.area_m2,
            suitability_score=cell.seed.suitability_score,
        )
        for index, cell in enumerate(partition.cells)
    )
    total = partition.developable_area_m2
    residential_area = sum(
        item.area_m2
        for item in assignments
        if item.zone_class is ZoneClass.RESIDENTIAL
    )
    mixed_area = total - residential_area
    assigned = {
        ZoneClass.RESIDENTIAL: residential_area,
        ZoneClass.MIXED: mixed_area,
        ZoneClass.PUBLIC: 0.0,
        ZoneClass.RECREATION: 0.0,
    }
    target = {
        ZoneClass.RESIDENTIAL: total / 2.0,
        ZoneClass.MIXED: total / 2.0,
        ZoneClass.PUBLIC: 0.0,
        ZoneClass.RECREATION: 0.0,
    }
    shares = tuple(
        ZoneShareDiagnostic(
            zone_class=zone_class,
            target_share=target[zone_class] / total,
            target_area_m2=target[zone_class],
            assigned_area_m2=assigned[zone_class],
            achieved_share=assigned[zone_class] / total,
            absolute_area_error_m2=abs(assigned[zone_class] - target[zone_class]),
            cell_count=sum(item.zone_class is zone_class for item in assignments),
        )
        for zone_class in ZoneClass
    )
    return ZoneAssignmentResult(
        assignments=assignments,
        shares=shares,
        total_area_m2=total,
        zoning_config_version="constraint-eval-test-v1",
        zoning_config_fingerprint="a" * 64,
        strategy_version="test",
    )


def make_snapshot(*, working_srid: int = WORKING_SRID) -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.uuid4(),
        project=ProjectRef(project_id=uuid.uuid4()),
        settings=ProjectSettings(working_srid=working_srid),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="boundary:test",
        ),
    )


def make_context(*, working_srid: int = WORKING_SRID) -> RunContext:
    return RunContext(
        run_id=uuid.uuid4(),
        mode=RunMode.FROM_SCRATCH,
        seed=42,
        working_srid=working_srid,
        config_refs=(),
        correlation=CorrelationMetadata(correlation_id="zoning-constraint-test"),
    )


class RecordingEngine:
    def __init__(self) -> None:
        self.calls: list[
            tuple[
                ZoneConstraintSubject,
                str,
                ConstraintScope,
                TerritorySnapshot,
                RunContext,
            ]
        ] = []

    def evaluate(
        self,
        *,
        subject: ZoneConstraintSubject,
        stage: str,
        scope: ConstraintScope,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ValidationReport:
        self.calls.append((subject, stage, scope, snapshot, context))
        if subject.cell_index == 0:
            result = ConstraintResult(
                code="zone.preference",
                severity=ConstraintSeverity.SOFT,
                scope=ConstraintScope.ZONE,
                passed=False,
                message="soft zoning preference not satisfied",
            )
        else:
            result = ConstraintResult(
                code="zone.blocking",
                severity=ConstraintSeverity.HARD,
                scope=ConstraintScope.ZONE,
                passed=False,
                message="hard zoning rule not satisfied",
            )
        return ValidationReport(results=(result,))


class PassingEngine:
    def evaluate(
        self,
        *,
        subject: ZoneConstraintSubject,
        stage: str,
        scope: ConstraintScope,
        snapshot: TerritorySnapshot,
        context: RunContext,
    ) -> ValidationReport:
        return ValidationReport()


def test_evaluator_routes_each_zone_through_shared_engine_contract() -> None:
    partition = make_partition()
    assignment = make_assignment(partition)
    snapshot = make_snapshot()
    context = make_context()
    engine = RecordingEngine()

    result = ZoneConstraintEvaluator().evaluate(
        partition=partition,
        assignment=assignment,
        snapshot=snapshot,
        context=context,
        engine=engine,
    )

    assert len(engine.calls) == 2
    for index, (subject, stage, scope, call_snapshot, call_context) in enumerate(engine.calls):
        assert subject.cell_index == index
        assert subject.seed_index == partition.cells[index].seed_index
        assert subject.zone_class is assignment.assignments[index].zone_class
        assert subject.geometry is partition.cells[index].geometry
        assert subject.area_m2 == partition.cells[index].area_m2
        assert subject.working_srid == WORKING_SRID
        assert stage == "zoning"
        assert scope is ConstraintScope.ZONE
        assert call_snapshot is snapshot
        assert call_context is context

    assert result.stage == "zoning"
    assert result.evaluator_version == "1"
    assert result.valid_zone_count == 1
    assert result.invalid_zone_count == 1
    assert len(result.report.soft_violations) == 1
    assert len(result.report.hard_failures) == 1
    assert result.report.is_valid is False


def test_evaluator_preserves_engine_authority_without_local_rule_checks() -> None:
    partition = make_partition(count=1)
    assignment = make_assignment(partition)

    result = ZoneConstraintEvaluator().evaluate(
        partition=partition,
        assignment=assignment,
        snapshot=make_snapshot(),
        context=make_context(),
        engine=PassingEngine(),
    )

    assert result.report.is_valid
    assert result.valid_zone_count == 1
    assert result.invalid_zone_count == 0
    assert result.evaluations[0].report.results == ()


def test_evaluated_zone_constraints_exposes_report_validity() -> None:
    subject = ZoneConstraintSubject(
        cell_index=0,
        seed_index=0,
        zone_class=ZoneClass.RESIDENTIAL,
        geometry=box(0.0, 0.0, 1.0, 1.0),
        area_m2=1.0,
        suitability_score=0.5,
        working_srid=WORKING_SRID,
    )
    evaluation = EvaluatedZoneConstraints(subject=subject, report=ValidationReport())
    assert evaluation.is_valid


def test_evaluator_rejects_snapshot_or_context_crs_mismatch_before_engine() -> None:
    partition = make_partition()
    assignment = make_assignment(partition)
    evaluator = ZoneConstraintEvaluator()

    with pytest.raises(ZoneConstraintEvaluationError, match="snapshot working_srid"):
        evaluator.evaluate(
            partition=partition,
            assignment=assignment,
            snapshot=make_snapshot(working_srid=32636),
            context=make_context(),
            engine=PassingEngine(),
        )

    with pytest.raises(ZoneConstraintEvaluationError, match="run context working_srid"):
        evaluator.evaluate(
            partition=partition,
            assignment=assignment,
            snapshot=make_snapshot(),
            context=make_context(working_srid=32636),
            engine=PassingEngine(),
        )


def test_evaluator_rejects_assignment_partition_reference_mismatch() -> None:
    partition = make_partition()
    assignment = make_assignment(partition, seed_indexes=(1, 0))

    with pytest.raises(ZoneConstraintEvaluationError, match="cell/seed references"):
        ZoneConstraintEvaluator().evaluate(
            partition=partition,
            assignment=assignment,
            snapshot=make_snapshot(),
            context=make_context(),
            engine=PassingEngine(),
        )


def test_evaluator_rejects_non_validation_report_from_engine() -> None:
    class BrokenEngine:
        def evaluate(
            self,
            *,
            subject: object,
            stage: str,
            scope: ConstraintScope,
            snapshot: TerritorySnapshot,
            context: RunContext,
        ) -> object:
            return object()

    partition = make_partition(count=1)
    assignment = make_assignment(partition)

    with pytest.raises(ZoneConstraintEvaluationError, match="must return ValidationReport"):
        ZoneConstraintEvaluator().evaluate(
            partition=partition,
            assignment=assignment,
            snapshot=make_snapshot(),
            context=make_context(),
            engine=cast(ConstraintEngine, BrokenEngine()),
        )


def test_subject_rejects_non_polygonal_contract_values() -> None:
    with pytest.raises(ZoneConstraintEvaluationError, match="polygonal"):
        ZoneConstraintSubject(
            cell_index=0,
            seed_index=0,
            zone_class=ZoneClass.RESIDENTIAL,
            geometry=Point(0.0, 0.0),
            area_m2=1.0,
            suitability_score=0.5,
            working_srid=WORKING_SRID,
        )
