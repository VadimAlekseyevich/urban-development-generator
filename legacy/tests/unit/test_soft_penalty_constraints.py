import json
import uuid

import pytest

from core.urban_generator.constraints import (
    AggregateConstraintSubject,
    AggregateMetricBound,
    AggregateMetricValue,
    ConstraintRegistry,
    RegisteredConstraintEngine,
    SoftAggregatePreference,
    SoftPenaltyRuleError,
    build_aggregate_bound_registrations,
    build_soft_preference_registrations,
)
from core.urban_generator.domain import (
    SOFT_PENALTY_SCHEMA_VERSION,
    ConstraintContractError,
    ConstraintResult,
    ConstraintScope,
    ConstraintSeverity,
    RawMetricId,
    SoftPenaltyMetadata,
    deserialize_validation_report,
    serialize_validation_report,
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


def _subject(*, coverage: float = 0.35, far: float = 1.5) -> AggregateConstraintSubject:
    return AggregateConstraintSubject(
        values=(
            AggregateMetricValue(
                RawMetricId.BUILDINGS_COVERAGE_RATIO,
                coverage,
            ),
            AggregateMetricValue(RawMetricId.BUILDINGS_FAR, far),
        )
    )


def _snapshot() -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.uuid4(),
        project=ProjectRef(project_id=uuid.uuid4()),
        settings=ProjectSettings(working_srid=WORKING_SRID),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="boundary:soft-penalty-test",
        ),
    )


def _context() -> RunContext:
    return RunContext(
        run_id=uuid.uuid4(),
        mode=RunMode.FROM_SCRATCH,
        seed=42,
        working_srid=WORKING_SRID,
        config_refs=(),
        correlation=CorrelationMetadata(
            correlation_id="soft-penalty-test",
        ),
    )


def test_soft_penalty_metadata_is_versioned_bounded_and_weighted() -> None:
    metadata = SoftPenaltyMetadata(raw_penalty=0.5, weight=3.0)

    assert metadata.schema_version == SOFT_PENALTY_SCHEMA_VERSION
    assert metadata.weighted_penalty == 1.5

    with pytest.raises(ConstraintContractError, match="schema_version"):
        SoftPenaltyMetadata(
            raw_penalty=0.5,
            weight=1.0,
            schema_version=SOFT_PENALTY_SCHEMA_VERSION + 1,
        )
    with pytest.raises(ConstraintContractError, match="inside 0..1"):
        SoftPenaltyMetadata(raw_penalty=1.1, weight=1.0)
    with pytest.raises(ConstraintContractError, match="non-negative"):
        SoftPenaltyMetadata(raw_penalty=0.5, weight=-1.0)


def test_constraint_result_keeps_soft_penalty_separate_from_hard_invalidity() -> None:
    soft_failure = ConstraintResult(
        code="preference.test",
        severity=ConstraintSeverity.SOFT,
        scope=ConstraintScope.TERRITORY,
        passed=False,
        message="preferred value not met",
        soft_penalty=SoftPenaltyMetadata(raw_penalty=1.0, weight=2.0),
    )

    assert soft_failure.blocks_generation is False
    assert soft_failure.soft_penalty is not None
    assert soft_failure.soft_penalty.weighted_penalty == 2.0

    with pytest.raises(ConstraintContractError, match="only valid for SOFT"):
        ConstraintResult(
            code="hard.test",
            severity=ConstraintSeverity.HARD,
            scope=ConstraintScope.TERRITORY,
            passed=False,
            message="hard failure",
            soft_penalty=SoftPenaltyMetadata(raw_penalty=1.0, weight=1.0),
        )

    with pytest.raises(ConstraintContractError, match="zero raw_penalty"):
        ConstraintResult(
            code="preference.passing",
            severity=ConstraintSeverity.SOFT,
            scope=ConstraintScope.TERRITORY,
            passed=True,
            message="passing soft rule",
            soft_penalty=SoftPenaltyMetadata(raw_penalty=0.5, weight=1.0),
        )


def test_soft_preference_violation_does_not_make_report_invalid() -> None:
    preferences = (
        SoftAggregatePreference(
            metric_id=RawMetricId.BUILDINGS_COVERAGE_RATIO,
            minimum=0.4,
            maximum=0.7,
            weight=2.5,
        ),
        SoftAggregatePreference(
            metric_id=RawMetricId.BUILDINGS_FAR,
            minimum=1.0,
            maximum=2.0,
            weight=1.5,
        ),
    )
    engine = RegisteredConstraintEngine(
        ConstraintRegistry(build_soft_preference_registrations(preferences))
    )

    report = engine.evaluate(
        subject=_subject(coverage=0.35, far=1.5),
        stage=FINAL_VALIDATION_STAGE,
        scope=ConstraintScope.TERRITORY,
        snapshot=_snapshot(),
        context=_context(),
    )

    assert len(report.results) == 2
    assert len(report.soft_violations) == 1
    assert report.hard_failures == ()
    assert report.is_valid is True

    failure = report.soft_violations[0]
    assert failure.code == "preference.buildings.coverage_ratio"
    assert failure.soft_penalty is not None
    assert failure.soft_penalty.raw_penalty == 1.0
    assert failure.soft_penalty.weight == 2.5
    assert failure.soft_penalty.weighted_penalty == 2.5

    passing = next(
        result
        for result in report.results
        if result.code == "preference.buildings.far"
    )
    assert passing.passed is True
    assert passing.soft_penalty is not None
    assert passing.soft_penalty.raw_penalty == 0.0


def test_soft_and_hard_rules_share_engine_but_only_hard_failure_invalidates() -> None:
    hard = build_aggregate_bound_registrations(
        (
            AggregateMetricBound(
                metric_id=RawMetricId.BUILDINGS_COVERAGE_RATIO,
                minimum=0.1,
            ),
            AggregateMetricBound(
                metric_id=RawMetricId.BUILDINGS_FAR,
                minimum=0.5,
            ),
            AggregateMetricBound(
                metric_id=RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
                minimum=0.0,
            ),
            AggregateMetricBound(
                metric_id=RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
                minimum=0.0,
            ),
        )
    )
    soft = build_soft_preference_registrations(
        (
            SoftAggregatePreference(
                metric_id=RawMetricId.BUILDINGS_COVERAGE_RATIO,
                minimum=0.4,
                weight=2.0,
            ),
        )
    )
    engine = RegisteredConstraintEngine(ConstraintRegistry((*hard, *soft)))
    subject = AggregateConstraintSubject(
        values=(
            AggregateMetricValue(RawMetricId.BUILDINGS_COVERAGE_RATIO, 0.2),
            AggregateMetricValue(RawMetricId.BUILDINGS_FAR, 1.0),
            AggregateMetricValue(
                RawMetricId.DEMOGRAPHY_DENSITY_PER_KM2,
                1_000.0,
            ),
            AggregateMetricValue(
                RawMetricId.INFRASTRUCTURE_CAPACITY_UTILIZATION,
                0.5,
            ),
        )
    )

    report = engine.evaluate(
        subject=subject,
        stage=FINAL_VALIDATION_STAGE,
        scope=ConstraintScope.TERRITORY,
        snapshot=_snapshot(),
        context=_context(),
    )

    assert report.hard_failures == ()
    assert len(report.soft_violations) == 1
    assert report.is_valid is True


def test_soft_penalty_round_trips_in_validation_report_v2() -> None:
    engine = RegisteredConstraintEngine(
        ConstraintRegistry(
            build_soft_preference_registrations(
                (
                    SoftAggregatePreference(
                        metric_id=RawMetricId.BUILDINGS_COVERAGE_RATIO,
                        minimum=0.4,
                        weight=2.5,
                    ),
                )
            )
        )
    )
    source = engine.evaluate(
        subject=_subject(coverage=0.3),
        stage=FINAL_VALIDATION_STAGE,
        scope=ConstraintScope.TERRITORY,
        snapshot=_snapshot(),
        context=_context(),
    )

    payload = serialize_validation_report(source)
    decoded = json.loads(payload)

    assert decoded["schema_version"] == 2
    assert decoded["results"][0]["soft_penalty"] == {
        "raw_penalty": 1.0,
        "schema_version": SOFT_PENALTY_SCHEMA_VERSION,
        "weight": 2.5,
    }

    restored = deserialize_validation_report(payload)
    assert restored == source
    assert restored.is_valid is True


def test_validation_report_decoder_keeps_legacy_v1_results_compatible() -> None:
    payload = (
        b'{"results":[{"code":"legacy.soft","entity_ref":null,'
        b'"message":"legacy soft failure","passed":false,'
        b'"problem_geometry":null,"scope":"TERRITORY",'
        b'"severity":"SOFT"}],"schema_version":1}'
    )

    restored = deserialize_validation_report(payload)

    assert len(restored.soft_violations) == 1
    assert restored.soft_violations[0].soft_penalty is None
    assert restored.is_valid is True


def test_soft_preference_config_rejects_invalid_weights_ranges_and_duplicates() -> None:
    with pytest.raises(SoftPenaltyRuleError, match="positive"):
        SoftAggregatePreference(
            metric_id=RawMetricId.BUILDINGS_FAR,
            minimum=1.0,
            weight=0.0,
        )
    with pytest.raises(SoftPenaltyRuleError, match="must not exceed"):
        SoftAggregatePreference(
            metric_id=RawMetricId.BUILDINGS_FAR,
            minimum=2.0,
            maximum=1.0,
            weight=1.0,
        )
    preference = SoftAggregatePreference(
        metric_id=RawMetricId.BUILDINGS_FAR,
        minimum=1.0,
        weight=1.0,
    )
    with pytest.raises(SoftPenaltyRuleError, match="duplicate"):
        build_soft_preference_registrations((preference, preference))
