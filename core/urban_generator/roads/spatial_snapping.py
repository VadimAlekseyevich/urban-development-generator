from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite

from shapely.geometry import Point
from shapely.strtree import STRtree

from core.urban_generator.domain.crs import require_working_crs
from core.urban_generator.domain.network import NetworkPoint

DEFAULT_MAX_SNAP_TARGETS = 500_000


class SpatialSnappingError(ValueError):
    """Raised when metric snapping inputs violate the spatial-index contract."""


@dataclass(frozen=True, slots=True)
class SpatialSnapTarget:
    """Stable endpoint/intersection/node candidate expressed in one working CRS."""

    target_id: str
    point: NetworkPoint

    def __post_init__(self) -> None:
        _require_target_id(self.target_id)
        if not isinstance(self.point, NetworkPoint):
            raise SpatialSnappingError("point must be a NetworkPoint")


@dataclass(frozen=True, slots=True)
class SpatialSnapMatch:
    """One exact metric match returned by the snapping index."""

    target: SpatialSnapTarget
    distance_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.target, SpatialSnapTarget):
            raise SpatialSnappingError("target must be a SpatialSnapTarget")
        _require_tolerance_m(self.distance_m, field_name="distance_m")


class SpatialSnapIndex:
    """Reusable STRtree-backed point snapping index in an explicit metric CRS.

    The tree narrows each query to targets within ``tolerance_m`` before exact Cartesian
    distances are evaluated. Input size is capped at construction time, so callers never
    fall back to an unbounded point-by-point scan. Equal-distance matches are ordered by
    ``target_id`` to keep snapping deterministic across STRtree traversal order.
    """

    def __init__(
        self,
        *,
        targets: tuple[SpatialSnapTarget, ...],
        working_srid: int,
        max_targets: int = DEFAULT_MAX_SNAP_TARGETS,
    ) -> None:
        require_working_crs(working_srid)
        if not isinstance(targets, tuple):
            raise SpatialSnappingError("targets must be an immutable tuple")
        _require_positive_int("max_targets", max_targets)
        if len(targets) > max_targets:
            raise SpatialSnappingError(
                f"snap target limit exceeded: {len(targets)} > {max_targets}"
            )

        seen_ids: set[str] = set()
        geometries: list[Point] = []
        targets_by_id: dict[str, SpatialSnapTarget] = {}
        for index, target in enumerate(targets):
            if not isinstance(target, SpatialSnapTarget):
                raise SpatialSnappingError(
                    f"targets[{index}] must be a SpatialSnapTarget"
                )
            if target.target_id in seen_ids:
                raise SpatialSnappingError(
                    f"duplicate snap target_id: {target.target_id!r}"
                )
            seen_ids.add(target.target_id)
            targets_by_id[target.target_id] = target
            geometries.append(Point(target.point.x_m, target.point.y_m))

        self.working_srid = working_srid
        self.targets = targets
        self.max_targets = max_targets
        self._targets_by_id = targets_by_id
        self._tree = STRtree(geometries) if geometries else None

    @property
    def target_count(self) -> int:
        return len(self.targets)

    def candidates(
        self,
        point: NetworkPoint,
        *,
        tolerance_m: float,
        exclude_target_ids: frozenset[str] = frozenset(),
    ) -> tuple[SpatialSnapMatch, ...]:
        """Return all targets within tolerance, ordered by distance then stable id."""

        if not isinstance(point, NetworkPoint):
            raise SpatialSnappingError("point must be a NetworkPoint")
        tolerance = _require_tolerance_m(tolerance_m, field_name="tolerance_m")
        excluded = _require_excluded_target_ids(exclude_target_ids)
        if self._tree is None:
            return ()

        query_point = Point(point.x_m, point.y_m)
        raw_indexes = self._tree.query(
            query_point,
            predicate="dwithin",
            distance=tolerance,
        )

        matches: list[SpatialSnapMatch] = []
        for raw_index in raw_indexes:
            index = int(raw_index)
            if index < 0 or index >= len(self.targets):
                raise SpatialSnappingError("STRtree returned an out-of-range target index")
            target = self.targets[index]
            if target.target_id in excluded:
                continue
            distance_m = hypot(
                target.point.x_m - point.x_m,
                target.point.y_m - point.y_m,
            )
            if not isfinite(distance_m) or distance_m < 0.0:
                raise SpatialSnappingError("computed snap distance must be finite and non-negative")
            if distance_m > tolerance:
                continue
            matches.append(SpatialSnapMatch(target=target, distance_m=distance_m))

        matches.sort(key=lambda match: (match.distance_m, match.target.target_id))
        return tuple(matches)

    def snap(
        self,
        point: NetworkPoint,
        *,
        tolerance_m: float,
        exclude_target_ids: frozenset[str] = frozenset(),
    ) -> SpatialSnapMatch | None:
        """Return the deterministic nearest target within ``tolerance_m``."""

        matches = self.candidates(
            point,
            tolerance_m=tolerance_m,
            exclude_target_ids=exclude_target_ids,
        )
        return matches[0] if matches else None

    def snap_target(
        self,
        target_id: str,
        *,
        tolerance_m: float,
    ) -> SpatialSnapMatch | None:
        """Snap one indexed target to its nearest *other* target within tolerance."""

        _require_target_id(target_id)
        try:
            target = self._targets_by_id[target_id]
        except KeyError as exc:
            raise SpatialSnappingError(
                f"unknown snap target_id: {target_id!r}"
            ) from exc
        return self.snap(
            target.point,
            tolerance_m=tolerance_m,
            exclude_target_ids=frozenset({target_id}),
        )


def _require_target_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SpatialSnappingError("target_id must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise SpatialSnappingError("target_id must not contain line breaks")


def _require_tolerance_m(value: float, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise SpatialSnappingError(f"{field_name} must be a finite number")
    if value < 0.0:
        raise SpatialSnappingError(f"{field_name} must be non-negative")
    return float(value)


def _require_excluded_target_ids(values: frozenset[str]) -> frozenset[str]:
    if not isinstance(values, frozenset):
        raise SpatialSnappingError("exclude_target_ids must be a frozenset")
    for value in values:
        _require_target_id(value)
    return values


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SpatialSnappingError(f"{field_name} must be a positive integer")
