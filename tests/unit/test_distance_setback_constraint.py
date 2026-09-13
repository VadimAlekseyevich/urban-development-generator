import uuid

import pytest
from shapely.geometry import LineString, Point, Polygon, box

from core.urban_generator.constraints import (
    ConstraintRegistry,
    DistanceSetbackBand,
    DistanceSetbackCandidateLimitError,
    DistanceSetbackConstraint,
    DistanceSetbackError,
    DistanceSetbackIndex,
    DistanceSetbackKind,
    DistanceSetbackSubject,
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
        correlation=CorrelationMetadata(correlation_id="distance-setback-test"),
    )


def subject(geometry: Polygon | Point, *, working_srid: int = 32637) -> DistanceSetbackSubject:
    return DistanceSetbackSubject(geometry=geometry, working_srid=working_srid)


def make_index(*, max_candidates: int = 10_000) -> DistanceSetbackIndex:
    return DistanceSetbackIndex(
        bands=(
            DistanceSetbackBand(
                kind=DistanceSetbackKind.ROAD,
                minimum_distance_m=5.0,
                geometries=(LineString([(0, 0), (0, 100)]),),
            ),
            DistanceSetbackBand(
                kind=DistanceSetbackKind.BUILDING,
                minimum_distance_m=3.0,
                geometries=(box(20, 20, 30, 30),),
            ),
            DistanceSetbackBand(
                kind=DistanceSetbackKind.FEATURE,
                minimum_distance_m=2.0,
                geometries=(Point(60, 60),),
            ),
        ),
        working_srid=32637,
        max_candidates=max_candidates,
    )


def test_setback_checks_road_building_and_generic_feature_distance() -> None:
    index = make_index()

    road_hit = index.check(subject(Point(4, 50)))
    building_hit = index.check(subject(Point(31, 25)))
    feature_hit = index.check(subject(Point(61, 60)))

    assert road_hit is not None and road_hit.kind is DistanceSetbackKind.ROAD
    assert road_hit.actual_distance_m == pytest.approx(4.0)
    assert building_hit is not None and building_hit.kind is DistanceSetbackKind.BUILDING
    assert building_hit.actual_distance_m == pytest.approx(1.0)
    assert feature_hit is not None and feature_hit.kind is DistanceSetbackKind.FEATURE
    assert feature_hit.actual_distance_m == pytest.approx(1.0)


def test_exact_minimum_distance_passes_and_intersection_fails() -> None:
    index = make_index()

    assert index.check(subject(Point(5, 50))) is None
    intersection = index.check(subject(Point(0, 50)))

    assert intersection is not None
    assert intersection.actual_distance_m == 0.0


def test_band_order_and_feature_order_make_diagnostics_deterministic() -> None:
    index = DistanceSetbackIndex(
        bands=(
            DistanceSetbackBand(
                kind=DistanceSetbackKind.FEATURE,
                minimum_distance_m=10.0,
                geometries=(Point(3, 0), Point(1, 0)),
            ),
            DistanceSetbackBand(
                kind=DistanceSetbackKind.ROAD,
                minimum_distance_m=10.0,
                geometries=(LineString([(0, -10), (0, 10)]),),
            ),
        ),
        working_srid=32637,
    )

    hit = index.check(subject(Point(0, 0)))

    assert hit is not None
    assert hit.kind is DistanceSetbackKind.FEATURE
    assert hit.band_index == 0
    assert hit.feature_index == 0


def test_spatial_prefilter_avoids_full_scan_of_far_features() -> None:
    far = tuple(Point(1000 + index * 10, 1000) for index in range(100))
    index = DistanceSetbackIndex(
        bands=(
            DistanceSetbackBand(
                kind=DistanceSetbackKind.FEATURE,
                minimum_distance_m=2.0,
                geometries=far + (Point(1, 0),),
            ),
        ),
        working_srid=32637,
        max_candidates=1,
    )

    hit = index.check(subject(Point(0, 0)))

    assert hit is not None
    assert hit.feature_index == 100


def test_candidate_scan_is_bounded_and_fails_closed_on_overflow() -> None:
    index = DistanceSetbackIndex(
        bands=(
            DistanceSetbackBand(
                kind=DistanceSetbackKind.FEATURE,
                minimum_distance_m=2.0,
                geometries=(Point(1, 0), Point(1, 0)),
            ),
        ),
        working_srid=32637,
        max_candidates=1,
    )

    with pytest.raises(DistanceSetbackCandidateLimitError, match="candidate limit exceeded"):
        index.check(subject(Point(0, 0)))


def test_zero_distance_band_is_a_valid_noop() -> None:
    index = DistanceSetbackIndex(
        bands=(
            DistanceSetbackBand(
                kind=DistanceSetbackKind.FEATURE,
                minimum_distance_m=0,
                geometries=(Point(0, 0),),
            ),
        ),
        working_srid=32637,
    )

    assert index.feature_count == 1
    assert index.active_band_count == 0
    assert index.check(subject(Point(0, 0))) is None


def test_constraint_composes_with_registry_engine() -> None:
    rule = DistanceSetbackConstraint(scope=ConstraintScope.BUILDING, index=make_index())
    registry = ConstraintRegistry()
    registry.register(stage="buildings", constraint=rule)
    engine = RegisteredConstraintEngine(registry)

    report = engine.evaluate(
        subject=subject(Point(4, 50)),
        stage="buildings",
        scope=ConstraintScope.BUILDING,
        snapshot=make_snapshot(),
        context=make_context(),
    )

    assert report.is_valid is False
    assert report.results[0].message == (
        "geometry is 4.000 m from road feature #0 in setback band #0; "
        "requires at least 5.000 m"
    )


def test_subject_snapshot_and_run_crs_must_match_index() -> None:
    index = make_index()

    with pytest.raises(DistanceSetbackError, match="subject working_srid"):
        index.check(subject(Point(50, 50), working_srid=3857))

    rule = DistanceSetbackConstraint(scope=ConstraintScope.BUILDING, index=index)
    with pytest.raises(DistanceSetbackError, match="snapshot working_srid"):
        rule.evaluate(
            subject=subject(Point(50, 50)),
            snapshot=make_snapshot(working_srid=3857),
            context=make_context(),
        )
    with pytest.raises(DistanceSetbackError, match="run context working_srid"):
        rule.evaluate(
            subject=subject(Point(50, 50)),
            snapshot=make_snapshot(),
            context=make_context(working_srid=3857),
        )


def test_geographic_crs_and_invalid_band_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="not projected"):
        DistanceSetbackSubject(geometry=Point(0, 0), working_srid=4326)
    with pytest.raises(DistanceSetbackError, match="finite non-negative"):
        DistanceSetbackBand(
            kind=DistanceSetbackKind.ROAD,
            minimum_distance_m=-1,
            geometries=(Point(0, 0),),
        )
    with pytest.raises(DistanceSetbackError, match="immutable tuple"):
        DistanceSetbackIndex(bands=[], working_srid=32637)
