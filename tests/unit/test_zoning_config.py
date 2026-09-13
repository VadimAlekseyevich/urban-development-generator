from __future__ import annotations

import math

import pytest

from core.urban_generator.zoning import (
    ZoneAdjacencyPolicy,
    ZoneAdjacencyRule,
    ZoneClass,
    ZoneClassConfig,
    ZoningConfig,
    ZoningConfigError,
)


def _zones() -> tuple[ZoneClassConfig, ...]:
    return (
        ZoneClassConfig(
            zone_class=ZoneClass.RESIDENTIAL,
            target_share=0.55,
            minimum_area_m2=5_000.0,
        ),
        ZoneClassConfig(
            zone_class=ZoneClass.MIXED,
            target_share=0.20,
            minimum_area_m2=3_000.0,
        ),
        ZoneClassConfig(
            zone_class=ZoneClass.PUBLIC,
            target_share=0.15,
            minimum_area_m2=2_500.0,
        ),
        ZoneClassConfig(
            zone_class=ZoneClass.RECREATION,
            target_share=0.10,
            minimum_area_m2=4_000.0,
        ),
    )


def _rules() -> tuple[ZoneAdjacencyRule, ...]:
    return (
        ZoneAdjacencyRule(
            first=ZoneClass.RESIDENTIAL,
            second=ZoneClass.RECREATION,
            policy=ZoneAdjacencyPolicy.PREFERRED,
        ),
        ZoneAdjacencyRule(
            first=ZoneClass.PUBLIC,
            second=ZoneClass.RECREATION,
            policy=ZoneAdjacencyPolicy.PREFERRED,
        ),
        ZoneAdjacencyRule(
            first=ZoneClass.RESIDENTIAL,
            second=ZoneClass.MIXED,
            policy=ZoneAdjacencyPolicy.ALLOWED,
        ),
        ZoneAdjacencyRule(
            first=ZoneClass.MIXED,
            second=ZoneClass.RECREATION,
            policy=ZoneAdjacencyPolicy.DISCOURAGED,
        ),
    )


def _config() -> ZoningConfig:
    return ZoningConfig(
        version="v1",
        zones=_zones(),
        adjacency_rules=_rules(),
    )


def test_zone_classes_are_stable_canonical_codes() -> None:
    assert tuple(zone.value for zone in ZoneClass) == (
        "residential",
        "mixed",
        "public",
        "recreation",
    )


def test_config_canonicalizes_zone_and_rule_order_and_exposes_metric_rules() -> None:
    zones = tuple(reversed(_zones()))
    rules = tuple(reversed(_rules()))

    config = ZoningConfig(version="v1", zones=zones, adjacency_rules=rules)

    assert tuple(zone.zone_class for zone in config.zones) == tuple(ZoneClass)
    assert config.zone(ZoneClass.RESIDENTIAL).target_share == pytest.approx(0.55)
    assert config.zone(ZoneClass.RESIDENTIAL).minimum_area_m2 == pytest.approx(5_000.0)
    assert math.fsum(zone.target_share for zone in config.zones) == pytest.approx(1.0)
    assert config.adjacency_rules == tuple(
        sorted(
            rules,
            key=lambda rule: (
                tuple(ZoneClass).index(rule.first),
                tuple(ZoneClass).index(rule.second),
            ),
        )
    )


def test_adjacency_rules_are_symmetric_with_allowed_default_and_self_policy() -> None:
    config = _config()

    assert (
        config.adjacency_policy(ZoneClass.RESIDENTIAL, ZoneClass.RECREATION)
        is ZoneAdjacencyPolicy.PREFERRED
    )
    assert (
        config.adjacency_policy(ZoneClass.RECREATION, ZoneClass.RESIDENTIAL)
        is ZoneAdjacencyPolicy.PREFERRED
    )
    assert (
        config.adjacency_policy(ZoneClass.RESIDENTIAL, ZoneClass.PUBLIC)
        is ZoneAdjacencyPolicy.ALLOWED
    )
    assert (
        config.adjacency_policy(ZoneClass.PUBLIC, ZoneClass.PUBLIC)
        is ZoneAdjacencyPolicy.ALLOWED
    )
    assert config.allows_adjacency(ZoneClass.MIXED, ZoneClass.RECREATION) is True

    forbidden = ZoningConfig(
        version="v2",
        zones=_zones(),
        adjacency_rules=(
            ZoneAdjacencyRule(
                first=ZoneClass.PUBLIC,
                second=ZoneClass.RESIDENTIAL,
                policy=ZoneAdjacencyPolicy.FORBIDDEN,
            ),
        ),
    )
    assert forbidden.allows_adjacency(ZoneClass.PUBLIC, ZoneClass.RESIDENTIAL) is False
    assert forbidden.allows_adjacency(ZoneClass.RESIDENTIAL, ZoneClass.PUBLIC) is False


def test_reversed_adjacency_rule_is_canonicalized() -> None:
    rule = ZoneAdjacencyRule(
        first=ZoneClass.RECREATION,
        second=ZoneClass.RESIDENTIAL,
        policy=ZoneAdjacencyPolicy.DISCOURAGED,
    )

    assert rule.first is ZoneClass.RESIDENTIAL
    assert rule.second is ZoneClass.RECREATION
    assert rule.pair == (ZoneClass.RESIDENTIAL, ZoneClass.RECREATION)


def test_config_requires_each_zone_class_once_and_target_shares_sum_to_one() -> None:
    with pytest.raises(ZoningConfigError, match="every canonical zone class"):
        ZoningConfig(version="v1", zones=_zones()[:-1])

    duplicate = (*_zones()[:-1], _zones()[0])
    with pytest.raises(ZoningConfigError, match="zone classes must be unique"):
        ZoningConfig(version="v1", zones=duplicate)

    invalid_sum = (
        ZoneClassConfig(ZoneClass.RESIDENTIAL, 0.50, 5_000.0),
        ZoneClassConfig(ZoneClass.MIXED, 0.20, 3_000.0),
        ZoneClassConfig(ZoneClass.PUBLIC, 0.15, 2_500.0),
        ZoneClassConfig(ZoneClass.RECREATION, 0.10, 4_000.0),
    )
    with pytest.raises(ZoningConfigError, match="target shares must sum to 1.0"):
        ZoningConfig(version="v1", zones=invalid_sum)


def test_zone_definition_rejects_invalid_share_area_and_types() -> None:
    with pytest.raises(ZoningConfigError, match="inside 0..1"):
        ZoneClassConfig(ZoneClass.RESIDENTIAL, 1.01, 1_000.0)
    with pytest.raises(ZoningConfigError, match="inside 0..1"):
        ZoneClassConfig(ZoneClass.RESIDENTIAL, -0.01, 1_000.0)
    with pytest.raises(ZoningConfigError, match="greater than zero"):
        ZoneClassConfig(ZoneClass.RESIDENTIAL, 0.5, 0.0)
    with pytest.raises(ZoningConfigError, match="finite number"):
        ZoneClassConfig(ZoneClass.RESIDENTIAL, 0.5, float("nan"))
    with pytest.raises(ZoningConfigError, match="ZoneClass value"):
        ZoneClassConfig("residential", 0.5, 1_000.0)  # type: ignore[arg-type]
    with pytest.raises(ZoningConfigError, match="finite number"):
        ZoneClassConfig(ZoneClass.RESIDENTIAL, True, 1_000.0)  # type: ignore[arg-type]


def test_adjacency_contract_rejects_self_invalid_policy_and_duplicate_pair() -> None:
    with pytest.raises(ZoningConfigError, match="self adjacency"):
        ZoneAdjacencyRule(
            first=ZoneClass.PUBLIC,
            second=ZoneClass.PUBLIC,
            policy=ZoneAdjacencyPolicy.ALLOWED,
        )
    with pytest.raises(ZoningConfigError, match="ZoneAdjacencyPolicy"):
        ZoneAdjacencyRule(
            first=ZoneClass.PUBLIC,
            second=ZoneClass.RECREATION,
            policy="FORBIDDEN",  # type: ignore[arg-type]
        )

    first = ZoneAdjacencyRule(
        first=ZoneClass.RESIDENTIAL,
        second=ZoneClass.RECREATION,
        policy=ZoneAdjacencyPolicy.PREFERRED,
    )
    reversed_pair = ZoneAdjacencyRule(
        first=ZoneClass.RECREATION,
        second=ZoneClass.RESIDENTIAL,
        policy=ZoneAdjacencyPolicy.FORBIDDEN,
    )
    with pytest.raises(ZoningConfigError, match="pairs must be unique"):
        ZoningConfig(
            version="v1",
            zones=_zones(),
            adjacency_rules=(first, reversed_pair),
        )


def test_fingerprint_is_stable_for_equivalent_order_and_changes_with_semantics() -> None:
    first = _config()
    reordered = ZoningConfig(
        version="v1",
        zones=tuple(reversed(_zones())),
        adjacency_rules=tuple(reversed(_rules())),
    )
    changed_share = ZoningConfig(
        version="v1",
        zones=(
            ZoneClassConfig(ZoneClass.RESIDENTIAL, 0.50, 5_000.0),
            ZoneClassConfig(ZoneClass.MIXED, 0.25, 3_000.0),
            ZoneClassConfig(ZoneClass.PUBLIC, 0.15, 2_500.0),
            ZoneClassConfig(ZoneClass.RECREATION, 0.10, 4_000.0),
        ),
        adjacency_rules=_rules(),
    )
    changed_rule = ZoningConfig(
        version="v1",
        zones=_zones(),
        adjacency_rules=(
            *_rules()[:-1],
            ZoneAdjacencyRule(
                first=ZoneClass.MIXED,
                second=ZoneClass.RECREATION,
                policy=ZoneAdjacencyPolicy.FORBIDDEN,
            ),
        ),
    )

    assert first.fingerprint == reordered.fingerprint
    assert len(first.fingerprint) == 64
    assert first.fingerprint != changed_share.fingerprint
    assert first.fingerprint != changed_rule.fingerprint
    assert first.fingerprint != ZoningConfig(
        version="v2",
        zones=_zones(),
        adjacency_rules=_rules(),
    ).fingerprint


def test_config_rejects_invalid_version_tuple_contract_and_lookup_types() -> None:
    with pytest.raises(ZoningConfigError, match="invalid zoning config version"):
        ZoningConfig(version="bad version", zones=_zones())
    with pytest.raises(ZoningConfigError, match="immutable tuple"):
        ZoningConfig(version="v1", zones=list(_zones()))  # type: ignore[arg-type]
    with pytest.raises(ZoningConfigError, match="immutable tuple"):
        ZoningConfig(
            version="v1",
            zones=_zones(),
            adjacency_rules=list(_rules()),  # type: ignore[arg-type]
        )

    config = _config()
    with pytest.raises(ZoningConfigError, match="ZoneClass value"):
        config.zone("residential")  # type: ignore[arg-type]
    with pytest.raises(ZoningConfigError, match="ZoneClass value"):
        config.adjacency_policy(ZoneClass.PUBLIC, "mixed")  # type: ignore[arg-type]
