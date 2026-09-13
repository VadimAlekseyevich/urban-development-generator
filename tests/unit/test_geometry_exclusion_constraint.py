import uuid

import pytest
from shapely.geometry import Polygon, box

from core.urban_generator.constraints import (
    ConstraintRegistry,
    GeometryExclusionCandidateLimitError,
    GeometryExclusionConstraint,
    GeometryExclusionError,
    GeometryExclusionIndex,
    GeometryExclusionReason,
    GeometryExclusionSubject,
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


def make_snapshot(*, working_srid: int = 32637) -> TerritorySnapshot:
    return TerritorySnapshot(
        snapshot_id=uuid.UUID("00000000-0000-0000-0000-000000000601"),
        project=ProjectRef(project_id=uuid.UUID("00000000-0000-0000-0000-000000000602")),
        settings=ProjectSettings(working_srid=working_srid),
        boundary=SnapshotLayerRef(
            kind=SnapshotLayerKind.BOUNDARY,
            source_ref="synthetic:boundary:v1",
        ),
    )


def make_context(*, working_srid: int = 32637) -> RunContext:
    return RunContext(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000603"),
        mode=RunMode.EXPANSION,
        seed=2026,
        working_srid=working_srid,
        config_refs=(ConfigRef(name="generation", ref="synthetic:generation:v1"),),
        correlation=CorrelationMetadata(correlation_id="geometry-exclusion-test"),
    )


def make_index(*, max_candidates: int = 10_000) -> GeometryExclusionIndex:
    return GeometryExclusionIndex(
        boundary=box(0, 0, 100, 100),
        working_srid=32637,
        water=(box(20, 20, 30, 30),),
        protected=(box(60, 60, 80, 80),),
        max_candidates=max_candidates,
    )


def subject(geometry: Polygon, *, working_srid: int = 32637) -> GeometryExclusionSubject:
    return GeometryExclusionSubject(geometry=geometry, working_srid=working_srid)


def test_allowed_geometry_passes_and_boundary_touch_is_allowed() -> None:
    index = make_index()

    assert index.check(subject(box(5, 5, 10, 10))) is None
    assert index.check(subject(box(0, 10, 5, 15))) is None


def test_geometry_outside_or_crossing_boundary_is_rejected() -> None:
    index = make_index()

    outside = index.check(subject(box(101, 10, 105, 15)))
    crossing = index.check(subject(box(-1, 10, 5, 15)))

    assert outside is not None
    assert outside.reason is GeometryExclusionReason.OUTSIDE_BOUNDARY
    assert crossing is not None
    assert crossing.reason is GeometryExclusionReason.OUTSIDE_BOUNDARY


def test_water_intersection_and_touch_are_hard_exclusions() -> None:
    index = make_index()

    overlap = index.check(subject(box(22, 22, 24, 24)))
    touching = index.check(subject(box(10, 20, 20, 30)))

    assert overlap is not None
    assert overlap.reason is GeometryExclusionReason.WATER
    assert overlap.exclusion_index == 0
    assert touching is not None
    assert touching.reason is GeometryExclusionReason.WATER


def test_protected_intersection_is_reported() -> None:
    hit = make_index().check(subject(box(65, 65, 70, 70)))

    assert hit is not None
    assert hit.reason is GeometryExclusionReason.PROTECTED
    assert hit.exclusion_index == 0


def test_hit_precedence_is_deterministic_when_candidate_intersects_multiple_kinds() -> None:
    index = GeometryExclusionIndex(
        boundary=box(0, 0, 100, 100),
        working_srid=32637,
        water=(box(40, 40, 60, 60),),
        protected=(box(45, 45, 65, 65),),
    )

    hit = index.check(subject(box(50, 50, 55, 55)))

    assert hit is not None
    assert hit.reason is GeometryExclusionReason.WATER


def test_constraint_composes_with_registry_engine() -> None:
    rule = GeometryExclusionConstraint(
        scope=ConstraintScope.BUILDING,
        index=make_index(),
    )
    registry = ConstraintRegistry()
    registry.register(stage="buildings", constraint=rule)
    engine = RegisteredConstraintEngine(registry)

    allowed = engine.evaluate(
        subject=subject(box(5, 5, 10, 10)),
        stage="buildings",
        scope=ConstraintScope.BUILDING,
        snapshot=make_snapshot(),
        context=make_context(),
    )
    blocked = engine.evaluate(
        subject=subject(box(22, 22, 24, 24)),
        stage="buildings",
        scope=ConstraintScope.BUILDING,
        snapshot=make_snapshot(),
        context=make_context(),
    )

    assert allowed.is_valid is True
    assert allowed.results[0].passed is True
    assert blocked.is_valid is False
    assert blocked.results[0].message == "geometry intersects water exclusion #0"


def test_empty_exclusion_set_uses_boundary_only() -> None:
    index = GeometryExclusionIndex(
        boundary=box(0, 0, 100, 100),
        working_srid=32637,
    )

    assert index.exclusion_count == 0
    assert index.water_count == 0
    assert index.protected_count == 0
    assert index.check(subject(box(5, 5, 10, 10))) is None


def test_candidate_scan_is_bounded_and_fails_closed_on_overflow() -> None:
    index = GeometryExclusionIndex(
        boundary=box(0, 0, 100, 100),
        working_srid=32637,
        water=(
            box(20, 20, 30, 30),
            box(20, 20, 30, 30),
            box(20, 20, 30, 30),
        ),
        max_candidates=2,
    )

    with pytest.raises(GeometryExclusionCandidateLimitError, match="candidate limit exceeded"):
        index.check(subject(box(21, 21, 22, 22)))


def test_subject_and_run_crs_must_match_index() -> None:
    index = make_index()

    with pytest.raises(GeometryExclusionError, match="subject working_srid"):
        index.check(subject(box(5, 5, 10, 10), working_srid=3857))

    rule = GeometryExclusionConstraint(scope=ConstraintScope.BUILDING, index=index)
    with pytest.raises(GeometryExclusionError, match="snapshot working_srid"):
        rule.evaluate(
            subject=subject(box(5, 5, 10, 10)),
            snapshot=make_snapshot(working_srid=3857),
            context=make_context(),
        )
    with pytest.raises(GeometryExclusionError, match="run context working_srid"):
        rule.evaluate(
            subject=subject(box(5, 5, 10, 10)),
            snapshot=make_snapshot(),
            context=make_context(working_srid=3857),
        )


def test_invalid_boundary_and_exclusion_inputs_are_rejected() -> None:
    bow_tie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])

    with pytest.raises(GeometryExclusionError, match="boundary must be Polygon"):
        GeometryExclusionIndex(
            boundary=box(0, 0, 1, 1).boundary,
            working_srid=32637,
        )
    with pytest.raises(GeometryExclusionError, match=r"water\[0\] geometry must be valid"):
        GeometryExclusionIndex(
            boundary=box(0, 0, 100, 100),
            working_srid=32637,
            water=(bow_tie,),
        )
    with pytest.raises(GeometryExclusionError, match="immutable tuple"):
        GeometryExclusionIndex(
            boundary=box(0, 0, 100, 100),
            working_srid=32637,
            water=[box(20, 20, 30, 30)],
        )
