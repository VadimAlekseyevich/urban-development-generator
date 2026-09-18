from __future__ import annotations

import pytest

from core.urban_generator.buildings import (
    BuildingArchetype,
    BuildingArchetypeConfig,
    BuildingConfig,
    BuildingConfigError,
    BuildingFootprintStrategy,
    BuildingPlacementScope,
)
from core.urban_generator.zoning import ZoneClass


def _profiles() -> tuple[BuildingArchetypeConfig, ...]:
    return (
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.DETACHED,
            footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
            placement_scope=BuildingPlacementScope.PARCEL,
            allowed_zones=(ZoneClass.RESIDENTIAL,),
            selection_weight=4.0,
        ),
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.POINT,
            footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
            placement_scope=BuildingPlacementScope.PARCEL_OR_BLOCK,
            allowed_zones=(ZoneClass.RESIDENTIAL, ZoneClass.MIXED),
            selection_weight=2.0,
        ),
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.BAR,
            footprint_strategy=BuildingFootprintStrategy.BAR,
            placement_scope=BuildingPlacementScope.PARCEL_OR_BLOCK,
            allowed_zones=(ZoneClass.RESIDENTIAL, ZoneClass.MIXED),
            selection_weight=3.0,
        ),
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.PERIMETER,
            footprint_strategy=BuildingFootprintStrategy.PERIMETER,
            placement_scope=BuildingPlacementScope.BLOCK,
            allowed_zones=(ZoneClass.RESIDENTIAL, ZoneClass.MIXED),
            selection_weight=1.0,
        ),
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.COURTYARD,
            footprint_strategy=BuildingFootprintStrategy.COURTYARD,
            placement_scope=BuildingPlacementScope.BLOCK,
            allowed_zones=(ZoneClass.RESIDENTIAL, ZoneClass.MIXED),
            selection_weight=1.0,
        ),
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.PUBLIC,
            footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
            placement_scope=BuildingPlacementScope.PARCEL_OR_BLOCK,
            allowed_zones=(ZoneClass.PUBLIC, ZoneClass.RECREATION),
            selection_weight=1.0,
        ),
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.COMMERCIAL,
            footprint_strategy=BuildingFootprintStrategy.BAR,
            placement_scope=BuildingPlacementScope.PARCEL_OR_BLOCK,
            allowed_zones=(ZoneClass.MIXED,),
            selection_weight=2.0,
        ),
    )


def _config() -> BuildingConfig:
    return BuildingConfig(version="v1", archetypes=_profiles())


def test_building_archetype_codes_cover_s08_minimum_catalog() -> None:
    assert tuple(item.value for item in BuildingArchetype) == (
        "detached",
        "point",
        "bar",
        "perimeter",
        "courtyard",
        "public",
        "commercial",
    )
    assert tuple(item.value for item in BuildingFootprintStrategy) == (
        "rectangular_point",
        "bar",
        "perimeter",
        "courtyard",
    )
    assert tuple(item.value for item in BuildingPlacementScope) == (
        "parcel",
        "block",
        "parcel_or_block",
    )


def test_profile_canonicalizes_zone_order_and_exposes_applicability() -> None:
    profile = BuildingArchetypeConfig(
        archetype=BuildingArchetype.POINT,
        footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
        placement_scope=BuildingPlacementScope.PARCEL_OR_BLOCK,
        allowed_zones=(ZoneClass.MIXED, ZoneClass.RESIDENTIAL),
        selection_weight=2,
    )

    assert profile.allowed_zones == (
        ZoneClass.RESIDENTIAL,
        ZoneClass.MIXED,
    )
    assert profile.selection_weight == 2.0
    assert profile.allows_zone(ZoneClass.RESIDENTIAL) is True
    assert profile.allows_zone(ZoneClass.PUBLIC) is False


def test_config_canonicalizes_profiles_and_filters_by_zone_without_sampling() -> None:
    config = BuildingConfig(
        version="v1",
        archetypes=tuple(reversed(_profiles())),
    )

    assert tuple(profile.archetype for profile in config.archetypes) == tuple(
        BuildingArchetype
    )
    assert (
        config.archetype(BuildingArchetype.COURTYARD).placement_scope
        is BuildingPlacementScope.BLOCK
    )

    mixed = config.eligible_archetypes(ZoneClass.MIXED)
    assert tuple(profile.archetype for profile in mixed) == (
        BuildingArchetype.POINT,
        BuildingArchetype.BAR,
        BuildingArchetype.PERIMETER,
        BuildingArchetype.COURTYARD,
        BuildingArchetype.COMMERCIAL,
    )
    assert tuple(profile.selection_weight for profile in mixed) == (
        2.0,
        3.0,
        1.0,
        1.0,
        2.0,
    )


def test_config_may_define_a_subset_without_forcing_every_archetype_or_zone() -> None:
    detached_only = BuildingConfig(
        version="res-low-v1",
        archetypes=(_profiles()[0],),
    )

    assert detached_only.eligible_archetypes(ZoneClass.RESIDENTIAL) == (
        _profiles()[0],
    )
    assert detached_only.eligible_archetypes(ZoneClass.PUBLIC) == ()
    with pytest.raises(BuildingConfigError, match="unconfigured building archetype"):
        detached_only.archetype(BuildingArchetype.BAR)


def test_profile_rejects_invalid_types_duplicate_zones_and_nonpositive_weight() -> None:
    with pytest.raises(BuildingConfigError, match="BuildingArchetype"):
        BuildingArchetypeConfig(
            archetype="detached",  # type: ignore[arg-type]
            footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
            placement_scope=BuildingPlacementScope.PARCEL,
            allowed_zones=(ZoneClass.RESIDENTIAL,),
        )
    with pytest.raises(BuildingConfigError, match="BuildingFootprintStrategy"):
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.DETACHED,
            footprint_strategy="rectangular_point",  # type: ignore[arg-type]
            placement_scope=BuildingPlacementScope.PARCEL,
            allowed_zones=(ZoneClass.RESIDENTIAL,),
        )
    with pytest.raises(BuildingConfigError, match="BuildingPlacementScope"):
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.DETACHED,
            footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
            placement_scope="parcel",  # type: ignore[arg-type]
            allowed_zones=(ZoneClass.RESIDENTIAL,),
        )
    with pytest.raises(BuildingConfigError, match="immutable tuple"):
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.DETACHED,
            footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
            placement_scope=BuildingPlacementScope.PARCEL,
            allowed_zones=[ZoneClass.RESIDENTIAL],  # type: ignore[arg-type]
        )
    with pytest.raises(BuildingConfigError, match="at least one"):
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.DETACHED,
            footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
            placement_scope=BuildingPlacementScope.PARCEL,
            allowed_zones=(),
        )
    with pytest.raises(BuildingConfigError, match="unique"):
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.DETACHED,
            footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
            placement_scope=BuildingPlacementScope.PARCEL,
            allowed_zones=(ZoneClass.RESIDENTIAL, ZoneClass.RESIDENTIAL),
        )
    with pytest.raises(BuildingConfigError, match="greater than zero"):
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.DETACHED,
            footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
            placement_scope=BuildingPlacementScope.PARCEL,
            allowed_zones=(ZoneClass.RESIDENTIAL,),
            selection_weight=0.0,
        )
    with pytest.raises(BuildingConfigError, match="finite number"):
        BuildingArchetypeConfig(
            archetype=BuildingArchetype.DETACHED,
            footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
            placement_scope=BuildingPlacementScope.PARCEL,
            allowed_zones=(ZoneClass.RESIDENTIAL,),
            selection_weight=float("nan"),
        )


def test_building_config_rejects_duplicate_archetypes_invalid_version_and_collections() -> None:
    first = _profiles()[0]
    with pytest.raises(BuildingConfigError, match="building archetypes must be unique"):
        BuildingConfig(version="v1", archetypes=(first, first))
    with pytest.raises(BuildingConfigError, match="invalid building config version"):
        BuildingConfig(version="bad version", archetypes=(first,))
    with pytest.raises(BuildingConfigError, match="immutable tuple"):
        BuildingConfig(version="v1", archetypes=[first])  # type: ignore[arg-type]
    with pytest.raises(BuildingConfigError, match="must contain archetype profiles"):
        BuildingConfig(version="v1", archetypes=())

    config = _config()
    with pytest.raises(BuildingConfigError, match="BuildingArchetype"):
        config.archetype("bar")  # type: ignore[arg-type]
    with pytest.raises(BuildingConfigError, match="ZoneClass"):
        config.eligible_archetypes("mixed")  # type: ignore[arg-type]


def test_fingerprint_is_order_stable_and_changes_with_archetype_semantics() -> None:
    first = _config()
    reordered = BuildingConfig(
        version="v1",
        archetypes=tuple(reversed(_profiles())),
    )
    changed_weight_profiles = list(_profiles())
    changed_weight_profiles[0] = BuildingArchetypeConfig(
        archetype=BuildingArchetype.DETACHED,
        footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
        placement_scope=BuildingPlacementScope.PARCEL,
        allowed_zones=(ZoneClass.RESIDENTIAL,),
        selection_weight=5.0,
    )
    changed_weight = BuildingConfig(
        version="v1",
        archetypes=tuple(changed_weight_profiles),
    )
    changed_strategy_profiles = list(_profiles())
    changed_strategy_profiles[-1] = BuildingArchetypeConfig(
        archetype=BuildingArchetype.COMMERCIAL,
        footprint_strategy=BuildingFootprintStrategy.RECTANGULAR_POINT,
        placement_scope=BuildingPlacementScope.PARCEL_OR_BLOCK,
        allowed_zones=(ZoneClass.MIXED,),
        selection_weight=2.0,
    )
    changed_strategy = BuildingConfig(
        version="v1",
        archetypes=tuple(changed_strategy_profiles),
    )

    assert first.fingerprint == reordered.fingerprint
    assert len(first.fingerprint) == 64
    assert first.fingerprint != changed_weight.fingerprint
    assert first.fingerprint != changed_strategy.fingerprint
    assert first.fingerprint != BuildingConfig(
        version="v2",
        archetypes=_profiles(),
    ).fingerprint
