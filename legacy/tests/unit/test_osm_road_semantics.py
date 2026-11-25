import pytest

from backend.app.services.osm_road_semantics import (
    OsmRoadSemanticsNormalizer,
    RoadOnewayDirection,
)


def test_normalizes_structure_flags_and_signed_layer() -> None:
    result = OsmRoadSemanticsNormalizer().normalize(
        {
            "highway": "primary",
            "bridge": "viaduct",
            "tunnel": "no",
            "layer": "-2",
        }
    )

    assert result.bridge is True
    assert result.tunnel is False
    assert result.layer == -2
    assert result.one_way_direction is RoadOnewayDirection.BOTH
    assert result.one_way is False


@pytest.mark.parametrize("raw", ["yes", "TRUE", "1"])
def test_explicit_forward_oneway_values(raw: str) -> None:
    result = OsmRoadSemanticsNormalizer().normalize({"oneway": raw})

    assert result.one_way_direction is RoadOnewayDirection.FORWARD
    assert result.one_way is True


@pytest.mark.parametrize("raw", ["-1", "reverse"])
def test_explicit_reverse_oneway_values_preserve_geometry_direction(raw: str) -> None:
    result = OsmRoadSemanticsNormalizer().normalize({"oneway": raw})

    assert result.one_way_direction is RoadOnewayDirection.REVERSE
    assert result.one_way is True


def test_explicit_no_overrides_implied_motorway_and_roundabout_rules() -> None:
    normalizer = OsmRoadSemanticsNormalizer()

    motorway = normalizer.normalize({"highway": "motorway", "oneway": "no"})
    roundabout = normalizer.normalize({"junction": "roundabout", "oneway": "false"})

    assert motorway.one_way_direction is RoadOnewayDirection.BOTH
    assert roundabout.one_way_direction is RoadOnewayDirection.BOTH


def test_infers_oneway_for_motorway_and_roundabout_when_tag_is_absent() -> None:
    normalizer = OsmRoadSemanticsNormalizer()

    motorway = normalizer.normalize({"highway": "motorway_link"})
    roundabout = normalizer.normalize({"highway": "primary", "junction": "circular"})

    assert motorway.one_way_direction is RoadOnewayDirection.FORWARD
    assert roundabout.one_way_direction is RoadOnewayDirection.FORWARD


def test_unknown_dynamic_oneway_does_not_become_fixed_direction() -> None:
    result = OsmRoadSemanticsNormalizer().normalize(
        {"highway": "motorway", "oneway": "reversible"}
    )

    assert result.one_way_direction is RoadOnewayDirection.BOTH


@pytest.mark.parametrize("raw", ["", "1.5", "2;3", "99999999999999999999"])
def test_malformed_or_out_of_range_layer_falls_back_to_zero(raw: str) -> None:
    result = OsmRoadSemanticsNormalizer().normalize({"layer": raw})

    assert result.layer == 0


def test_structure_values_other_than_explicit_false_are_preserved_as_present() -> None:
    result = OsmRoadSemanticsNormalizer().normalize(
        {"bridge": "movable", "tunnel": "culvert"}
    )

    assert result.bridge is True
    assert result.tunnel is True


def test_rejects_non_string_tag_values_at_contract_boundary() -> None:
    with pytest.raises(TypeError, match="tag value must be a string"):
        OsmRoadSemanticsNormalizer().normalize({"layer": 2})  # type: ignore[dict-item]
