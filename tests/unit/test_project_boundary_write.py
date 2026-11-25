import pytest

from backend.app.application.project_boundary import (
    MAX_BOUNDARY_VERTICES,
    ProjectBoundaryValidationError,
    parse_project_boundary,
)


def _square() -> list[list[list[float]]]:
    return [[
        [37.6, 55.7],
        [37.61, 55.7],
        [37.61, 55.71],
        [37.6, 55.71],
        [37.6, 55.7],
    ]]


def test_valid_polygon_is_normalized_to_multipolygon() -> None:
    result = parse_project_boundary({"type": "Polygon", "coordinates": _square()})
    assert result.geom_type == "MultiPolygon"
    assert result.is_valid
    assert len(result.geoms) == 1
    assert result.bounds == pytest.approx((37.6, 55.7, 37.61, 55.71))


def test_multipolygon_accepts_disjoint_parts() -> None:
    second = [[
        [37.7, 55.7],
        [37.71, 55.7],
        [37.71, 55.71],
        [37.7, 55.71],
        [37.7, 55.7],
    ]]
    result = parse_project_boundary(
        {"type": "MultiPolygon", "coordinates": [_square(), second]}
    )
    assert len(result.geoms) == 2


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [37.6, 55.7]},
        {"type": "Polygon", "coordinates": [[]]},
        {"type": "Polygon", "coordinates": [[
            [37.6, 55.7], [37.61, 55.71], [37.61, 55.7],
            [37.6, 55.71], [37.6, 55.7],
        ]]},
        {"type": "Polygon", "coordinates": [[
            [37.6, 55.7], [37.61, 55.7], [37.61, 55.71],
            [37.6, 55.71],
        ]]},
        {"type": "Polygon", "coordinates": [[
            [False, 55.7], [37.61, 55.7], [37.61, 55.71],
            [False, 55.7],
        ]]},
        {"type": "Polygon", "coordinates": [[
            [200.0, 55.7], [201.0, 55.7],
            [201.0, 56.0], [200.0, 55.7],
        ]]},
        {"type": "Polygon", "coordinates": [[
            [179.9, 50.0], [-179.9, 50.0],
            [-179.9, 50.1], [179.9, 50.0],
        ]]},
        {"type": "MultiPolygon", "coordinates": [_square(), _square()]},
        {"type": "Polygon", "coordinates": _square(), "crs": "EPSG:3857"},
    ],
)
def test_invalid_boundary_is_rejected(geometry: dict[str, object]) -> None:
    with pytest.raises(ProjectBoundaryValidationError):
        parse_project_boundary(geometry)


def test_vertex_budget_is_enforced_before_shapely_construction() -> None:
    too_many = [[37.6, 55.7]] * (MAX_BOUNDARY_VERTICES + 1)
    with pytest.raises(ProjectBoundaryValidationError, match="vertex limit"):
        parse_project_boundary({"type": "Polygon", "coordinates": [too_many]})
