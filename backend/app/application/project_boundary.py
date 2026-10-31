"""Validate a bounded WGS84 project boundary before storing it in PostGIS.

The project boundary is a fixed input. Reject ambiguous CRS and geometries
that could silently change area, topology or the generated snapshot identity.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from shapely.geometry import MultiPolygon, Polygon

MAX_BOUNDARY_VERTICES = 10_000
MAX_BOUNDARY_POLYGONS = 64


class ProjectBoundaryValidationError(ValueError):
    """The supplied GeoJSON cannot be a canonical project boundary."""


def parse_project_boundary(geometry: Mapping[str, object]) -> MultiPolygon:
    """Accept GeoJSON Polygon/MultiPolygon, normalize to valid WGS84 MultiPolygon.

    Only (longitude, latitude) positions in EPSG:4326 are accepted. Explicit
    topological and size checks run before any CRS transformation.
    """
    if not isinstance(geometry, Mapping) or set(geometry) != {"type", "coordinates"}:
        raise ProjectBoundaryValidationError(
            "boundary must contain exactly GeoJSON type and coordinates"
        )
    kind = geometry["type"]
    coords = geometry["coordinates"]
    if kind == "Polygon":
        polygons = [coords]
    elif kind == "MultiPolygon":
        polygons = coords
    else:
        raise ProjectBoundaryValidationError("boundary must be Polygon or MultiPolygon")

    if not isinstance(polygons, (list, tuple)) or not 1 <= len(polygons) <= MAX_BOUNDARY_POLYGONS:
        raise ProjectBoundaryValidationError("boundary has no polygons or exceeds polygon limit")

    parsed: list[Polygon] = []
    vertex_count = 0
    for polygon_rings in polygons:
        if not isinstance(polygon_rings, (list, tuple)) or not polygon_rings:
            raise ProjectBoundaryValidationError("each polygon must have an exterior ring")
        rings: list[list[tuple[float, float]]] = []
        for ring in polygon_rings:
            if not isinstance(ring, (list, tuple)) or not 4 <= len(ring):
                raise ProjectBoundaryValidationError("boundary rings need at least four positions")
            vertex_count += len(ring)
            if vertex_count > MAX_BOUNDARY_VERTICES:
                raise ProjectBoundaryValidationError("boundary exceeds vertex limit")
            positions: list[tuple[float, float]] = []
            for position in ring:
                if not isinstance(position, (list, tuple)) or len(position) != 2:
                    raise ProjectBoundaryValidationError("positions must be [longitude, latitude]")
                x, y = position
                if (
                    isinstance(x, bool) or isinstance(y, bool)
                    or not isinstance(x, (int, float))
                    or not isinstance(y, (int, float))
                ):
                    raise ProjectBoundaryValidationError("boundary coordinates must be numbers")
                longitude, latitude = float(x), float(y)
                if (
                    not math.isfinite(longitude) or not math.isfinite(latitude)
                    or not -180 <= longitude <= 180
                    or not -90 <= latitude <= 90
                ):
                    raise ProjectBoundaryValidationError(
                        "boundary coordinates must be finite WGS84"
                    )
                positions.append((longitude, latitude))
            if positions[0] != positions[-1]:
                raise ProjectBoundaryValidationError("boundary rings must be closed")
            rings.append(positions)
        polygon = Polygon(rings[0], holes=rings[1:])
        if polygon.is_empty or polygon.area <= 0 or not polygon.is_valid:
            raise ProjectBoundaryValidationError("boundary polygon is empty or invalid")
        parsed.append(polygon)

    result = MultiPolygon(parsed)
    if result.is_empty or not result.is_valid:
        raise ProjectBoundaryValidationError("boundary polygons overlap or are invalid")
    west, _, east, _ = result.bounds
    if east - west > 180:
        raise ProjectBoundaryValidationError("antimeridian-crossing boundaries are not supported")
    return result
