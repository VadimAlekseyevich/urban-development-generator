from __future__ import annotations

import uuid

import pytest

from core.urban_generator.buildings import (
    AssignedBuildingAttributes,
    BuildingArchetype,
    BuildingAttributeAssigner,
    BuildingAttributeAssignmentError,
    BuildingAttributeConfig,
    BuildingAttributeRule,
    BuildingAttributeSubject,
    BuildingUse,
)
from core.urban_generator.domain import (
    CorrelationMetadata,
    RunContext,
    RunMode,
)
from core.urban_generator.zoning import ZoneClass

WORKING_SRID = 3857


def _context(*, seed: int = 12345) -> RunContext:
    return RunContext(
        run_id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        mode=RunMode.EXPANSION,
        seed=seed,
        working_srid=WORKING_SRID,
        config_refs=(),
        correlation=CorrelationMetadata(correlation_id="building-attrs-test"),
    )


def _rule(
    zone_class: ZoneClass,
    archetype: BuildingArchetype,
    use: BuildingUse,
    min_floors: int,
    max_floors: int,
) -> BuildingAttributeRule:
    return BuildingAttributeRule(
        zone_class=zone_class,
        archetype=archetype,
        use=use,
        min_floors=min_floors,
        max_floors=max_floors,
    )


def _subject(
    building_id: str,
    zone_class: ZoneClass,
    archetype: BuildingArchetype,
) -> BuildingAttributeSubject:
    return BuildingAttributeSubject(
        building_id=building_id,
        source_id="parcel:test",
        zone_class=zone_class,
        archetype=archetype,
    )


def test_assigns_exact_use_and_fixed_floors_without_geometry() -> None:
    config = BuildingAttributeConfig(
        version="attrs-v1",
        rules=(
            _rule(
                ZoneClass.RESIDENTIAL,
                BuildingArchetype.DETACHED,
                BuildingUse.RESIDENTIAL,
                2,
                2,
            ),
            _rule(
                ZoneClass.MIXED,
                BuildingArchetype.BAR,
                BuildingUse.MIXED,
                5,
                5,
            ),
        ),
    )
    result = BuildingAttributeAssigner().assign(
        (
            _subject(
                "building:b",
                ZoneClass.MIXED,
                BuildingArchetype.BAR,
            ),
            _subject(
                "building:a",
                ZoneClass.RESIDENTIAL,
                BuildingArchetype.DETACHED,
            ),
        ),
        config=config,
        context=_context(),
    )

    assert tuple(item.building_id for item in result.buildings) == (
        "building:a",
        "building:b",
    )
    first, second = result.buildings
    assert isinstance(first, AssignedBuildingAttributes)
    assert first.use is BuildingUse.RESIDENTIAL
    assert first.floors == 2
    assert second.use is BuildingUse.MIXED
    assert second.floors == 5
    assert not hasattr(first, "geometry")
    assert not hasattr(first, "gfa_m2")


def test_variable_floor_assignment_is_order_independent_and_reproducible() -> None:
    config = BuildingAttributeConfig(
        version="attrs-v1",
        rules=(
            _rule(
                ZoneClass.RESIDENTIAL,
                BuildingArchetype.POINT,
                BuildingUse.RESIDENTIAL,
                4,
                12,
            ),
        ),
    )
    subjects = (
        _subject(
            "building:a",
            ZoneClass.RESIDENTIAL,
            BuildingArchetype.POINT,
        ),
        _subject(
            "building:b",
            ZoneClass.RESIDENTIAL,
            BuildingArchetype.POINT,
        ),
    )
    assigner = BuildingAttributeAssigner()

    first = assigner.assign(subjects, config=config, context=_context(seed=77))
    second = assigner.assign(
        tuple(reversed(subjects)),
        config=config,
        context=_context(seed=77),
    )

    assert first == second
    assert all(4 <= item.floors <= 12 for item in first.buildings)


def test_unrelated_subject_does_not_change_existing_building_stream() -> None:
    config = BuildingAttributeConfig(
        version="attrs-v1",
        rules=(
            _rule(
                ZoneClass.RESIDENTIAL,
                BuildingArchetype.POINT,
                BuildingUse.RESIDENTIAL,
                3,
                20,
            ),
        ),
    )
    subject_a = _subject(
        "building:a",
        ZoneClass.RESIDENTIAL,
        BuildingArchetype.POINT,
    )
    subject_b = _subject(
        "building:b",
        ZoneClass.RESIDENTIAL,
        BuildingArchetype.POINT,
    )
    assigner = BuildingAttributeAssigner()

    alone = assigner.assign(
        (subject_a,),
        config=config,
        context=_context(seed=9),
    )
    with_other = assigner.assign(
        (subject_b, subject_a),
        config=config,
        context=_context(seed=9),
    )

    alone_floor = alone.buildings[0].floors
    mapped = {item.building_id: item.floors for item in with_other.buildings}
    assert mapped["building:a"] == alone_floor


def test_config_fingerprint_is_canonical_across_rule_order() -> None:
    first_rule = _rule(
        ZoneClass.PUBLIC,
        BuildingArchetype.PUBLIC,
        BuildingUse.PUBLIC,
        2,
        6,
    )
    second_rule = _rule(
        ZoneClass.MIXED,
        BuildingArchetype.COMMERCIAL,
        BuildingUse.COMMERCIAL,
        3,
        10,
    )

    first = BuildingAttributeConfig(
        version="attrs-v1",
        rules=(first_rule, second_rule),
    )
    second = BuildingAttributeConfig(
        version="attrs-v1",
        rules=(second_rule, first_rule),
    )

    assert first == second
    assert first.fingerprint == second.fingerprint


def test_missing_exact_zone_archetype_rule_is_rejected() -> None:
    config = BuildingAttributeConfig(
        version="attrs-v1",
        rules=(
            _rule(
                ZoneClass.RESIDENTIAL,
                BuildingArchetype.DETACHED,
                BuildingUse.RESIDENTIAL,
                1,
                3,
            ),
        ),
    )

    with pytest.raises(
        BuildingAttributeAssignmentError,
        match="no building attribute rule",
    ):
        BuildingAttributeAssigner().assign(
            (
                _subject(
                    "building:a",
                    ZoneClass.RESIDENTIAL,
                    BuildingArchetype.BAR,
                ),
            ),
            config=config,
            context=_context(),
        )


def test_rule_floor_bounds_and_duplicate_pairs_are_validated() -> None:
    with pytest.raises(
        BuildingAttributeAssignmentError,
        match="max_floors",
    ):
        _rule(
            ZoneClass.RESIDENTIAL,
            BuildingArchetype.DETACHED,
            BuildingUse.RESIDENTIAL,
            5,
            4,
        )

    duplicate = _rule(
        ZoneClass.RESIDENTIAL,
        BuildingArchetype.DETACHED,
        BuildingUse.RESIDENTIAL,
        1,
        2,
    )
    with pytest.raises(
        BuildingAttributeAssignmentError,
        match="pairs must be unique",
    ):
        BuildingAttributeConfig(
            version="attrs-v1",
            rules=(duplicate, duplicate),
        )


def test_subject_ids_and_max_subject_bound_are_validated() -> None:
    config = BuildingAttributeConfig(
        version="attrs-v1",
        rules=(
            _rule(
                ZoneClass.RESIDENTIAL,
                BuildingArchetype.DETACHED,
                BuildingUse.RESIDENTIAL,
                1,
                2,
            ),
        ),
    )
    subject = _subject(
        "building:a",
        ZoneClass.RESIDENTIAL,
        BuildingArchetype.DETACHED,
    )

    with pytest.raises(BuildingAttributeAssignmentError, match="unique"):
        BuildingAttributeAssigner().assign(
            (subject, subject),
            config=config,
            context=_context(),
        )

    with pytest.raises(BuildingAttributeAssignmentError, match="limit exceeded"):
        BuildingAttributeAssigner(max_subjects=1).assign(
            (
                subject,
                _subject(
                    "building:b",
                    ZoneClass.RESIDENTIAL,
                    BuildingArchetype.DETACHED,
                ),
            ),
            config=config,
            context=_context(),
        )


def test_assignment_requires_typed_config_context_and_valid_ids() -> None:
    with pytest.raises(BuildingAttributeAssignmentError, match="building_id"):
        _subject(
            "bad id",
            ZoneClass.RESIDENTIAL,
            BuildingArchetype.DETACHED,
        )

    assigner = BuildingAttributeAssigner()
    with pytest.raises(BuildingAttributeAssignmentError, match="config"):
        assigner.assign((), config=object(), context=_context())  # type: ignore[arg-type]
