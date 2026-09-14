from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class RoadOnewayDirection(StrEnum):
    """Normalized traversal direction relative to stored road geometry."""

    BOTH = "both"
    FORWARD = "forward"
    REVERSE = "reverse"


@dataclass(frozen=True, slots=True)
class OsmRoadSemantics:
    """OSM transport semantics required by later road graph stages."""

    bridge: bool
    tunnel: bool
    layer: int
    one_way_direction: RoadOnewayDirection

    @property
    def one_way(self) -> bool:
        return self.one_way_direction is not RoadOnewayDirection.BOTH


class OsmRoadSemanticsNormalizer:
    """Normalize OSM road tags without leaking raw tag conventions downstream.

    Unknown or malformed optional values degrade to conservative defaults while raw OSM tags
    remain preserved by the existing mapping provenance contract. Explicit ``oneway`` values
    take precedence over OSM implied one-way rules for motorways and roundabouts.
    """

    def normalize(self, tags: Mapping[str, str]) -> OsmRoadSemantics:
        if not isinstance(tags, Mapping):
            raise TypeError("tags must be a mapping")
        return OsmRoadSemantics(
            bridge=_structure_flag(tags.get("bridge")),
            tunnel=_structure_flag(tags.get("tunnel")),
            layer=_layer(tags.get("layer")),
            one_way_direction=_oneway_direction(tags),
        )


_FALSE_VALUES = frozenset({"no", "false", "0"})
_FORWARD_ONEWAY_VALUES = frozenset({"yes", "true", "1"})
_REVERSE_ONEWAY_VALUES = frozenset({"-1", "reverse"})
_BOTH_ONEWAY_VALUES = _FALSE_VALUES
_IMPLIED_ONEWAY_HIGHWAYS = frozenset({"motorway", "motorway_link"})
_IMPLIED_ONEWAY_JUNCTIONS = frozenset({"roundabout", "circular"})
_MIN_POSTGRES_INT = -(2**31)
_MAX_POSTGRES_INT = 2**31 - 1


def _normalized(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"OSM {field_name} tag value must be a string")
    normalized = value.strip().casefold()
    return normalized or None


def _structure_flag(value: str | None) -> bool:
    normalized = _normalized(value, field_name="structure")
    return normalized is not None and normalized not in _FALSE_VALUES


def _layer(value: str | None) -> int:
    normalized = _normalized(value, field_name="layer")
    if normalized is None or re.fullmatch(r"[+-]?\d+", normalized) is None:
        return 0
    parsed = int(normalized)
    if parsed < _MIN_POSTGRES_INT or parsed > _MAX_POSTGRES_INT:
        return 0
    return parsed


def _oneway_direction(tags: Mapping[str, str]) -> RoadOnewayDirection:
    raw_oneway = _normalized(tags.get("oneway"), field_name="oneway")
    if raw_oneway in _FORWARD_ONEWAY_VALUES:
        return RoadOnewayDirection.FORWARD
    if raw_oneway in _REVERSE_ONEWAY_VALUES:
        return RoadOnewayDirection.REVERSE
    if raw_oneway in _BOTH_ONEWAY_VALUES:
        return RoadOnewayDirection.BOTH
    if raw_oneway is not None:
        # Values such as reversible/alternating are not a stable directed edge contract.
        return RoadOnewayDirection.BOTH

    junction = _normalized(tags.get("junction"), field_name="junction")
    if junction in _IMPLIED_ONEWAY_JUNCTIONS:
        return RoadOnewayDirection.FORWARD

    highway = _normalized(tags.get("highway"), field_name="highway")
    if highway in _IMPLIED_ONEWAY_HIGHWAYS:
        return RoadOnewayDirection.FORWARD

    return RoadOnewayDirection.BOTH
