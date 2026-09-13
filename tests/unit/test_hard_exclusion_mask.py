from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import box

from core.urban_generator.suitability import SuitabilityGridSpec
from core.urban_generator.suitability.hard_exclusion import (
    ExclusionRasterizationPolicy,
    HardExclusionBoundary,
    HardExclusionGeometryLayer,
    HardExclusionMaskError,
    HardExclusionRasterLayer,
    build_hard_exclusion_mask,
)


def _grid() -> SuitabilityGridSpec:
    return SuitabilityGridSpec(
        working_srid=32637,
        bounds=(0.0, 0.0, 4.0, 4.0),
        width=4,
        height=4,
    )


def test_boundary_excludes_cells_outside_project_area() -> None:
    grid = _grid()

    result = build_hard_exclusion_mask(
        grid=grid,
        boundary=HardExclusionBoundary(
            geometry=box(1.0, 1.0, 3.0, 3.0),
            working_srid=32637,
        ),
        exclusion_policy=ExclusionRasterizationPolicy.CELL_CENTER,
    )

    assert result.excluded_count == 12
    assert result.developable_count == 4
    assert result.excluded_fraction == pytest.approx(0.75)
    assert result.source_codes == ("boundary",)
    assert np.array_equal(
        result.excluded,
        np.array(
            [
                [True, True, True, True],
                [True, False, False, True],
                [True, False, False, True],
                [True, True, True, True],
            ],
            dtype=np.bool_,
        ),
    )


def test_vector_and_raster_hard_sources_are_combined_with_or() -> None:
    grid = _grid()
    raster_mask = np.zeros(grid.shape, dtype=np.bool_)
    raster_mask[0, 3] = True

    result = build_hard_exclusion_mask(
        grid=grid,
        boundary=HardExclusionBoundary(
            geometry=box(0.0, 0.0, 4.0, 4.0),
            working_srid=32637,
        ),
        geometry_layers=(
            HardExclusionGeometryLayer(
                code="water",
                geometries=(box(1.0, 1.0, 2.0, 2.0),),
                working_srid=32637,
            ),
        ),
        raster_layers=(
            HardExclusionRasterLayer(
                code="slope.threshold",
                grid=grid,
                excluded=raster_mask,
            ),
        ),
        exclusion_policy=ExclusionRasterizationPolicy.CELL_CENTER,
    )

    assert result.excluded_count == 2
    assert result.excluded[2, 1]
    assert result.excluded[0, 3]
    assert result.source_codes == ("boundary", "water", "slope.threshold")


def test_any_touch_is_more_conservative_than_cell_center() -> None:
    grid = _grid()
    tiny_crossing_shape = box(0.9, 2.9, 1.1, 3.1)
    layer = HardExclusionGeometryLayer(
        code="protected",
        geometries=(tiny_crossing_shape,),
        working_srid=32637,
    )
    boundary = HardExclusionBoundary(
        geometry=box(0.0, 0.0, 4.0, 4.0),
        working_srid=32637,
    )

    center = build_hard_exclusion_mask(
        grid=grid,
        boundary=boundary,
        geometry_layers=(layer,),
        exclusion_policy=ExclusionRasterizationPolicy.CELL_CENTER,
    )
    touched = build_hard_exclusion_mask(
        grid=grid,
        boundary=boundary,
        geometry_layers=(layer,),
        exclusion_policy=ExclusionRasterizationPolicy.ANY_TOUCH,
    )

    assert center.excluded_count == 0
    assert touched.excluded_count == 4


def test_inputs_and_result_masks_are_defensively_copied_and_read_only() -> None:
    grid = _grid()
    original = np.zeros(grid.shape, dtype=np.bool_)
    layer = HardExclusionRasterLayer(code="raster.rule", grid=grid, excluded=original)
    original[0, 0] = True

    result = build_hard_exclusion_mask(
        grid=grid,
        boundary=HardExclusionBoundary(
            geometry=box(0.0, 0.0, 4.0, 4.0),
            working_srid=32637,
        ),
        raster_layers=(layer,),
    )

    assert not layer.excluded[0, 0]
    assert not result.excluded.flags.writeable
    with pytest.raises(ValueError):
        result.excluded[0, 0] = True


def test_shape_and_cell_budgets_fail_closed() -> None:
    grid = _grid()
    boundary = HardExclusionBoundary(
        geometry=box(0.0, 0.0, 4.0, 4.0),
        working_srid=32637,
    )
    layer = HardExclusionGeometryLayer(
        code="constraints",
        geometries=(box(0.0, 0.0, 0.1, 0.1), box(1.0, 1.0, 1.1, 1.1)),
        working_srid=32637,
    )

    with pytest.raises(HardExclusionMaskError, match="shape limit exceeded"):
        build_hard_exclusion_mask(
            grid=grid,
            boundary=boundary,
            geometry_layers=(layer,),
            max_shapes=1,
        )

    with pytest.raises(HardExclusionMaskError, match="grid cell limit exceeded"):
        build_hard_exclusion_mask(
            grid=grid,
            boundary=boundary,
            max_cells=15,
        )


def test_crs_grid_and_source_code_mismatches_are_rejected() -> None:
    grid = _grid()
    boundary = HardExclusionBoundary(
        geometry=box(0.0, 0.0, 4.0, 4.0),
        working_srid=32637,
    )

    with pytest.raises(HardExclusionMaskError, match="working_srid"):
        build_hard_exclusion_mask(
            grid=grid,
            boundary=HardExclusionBoundary(
                geometry=box(0.0, 0.0, 4.0, 4.0),
                working_srid=32638,
            ),
        )

    mismatched_grid = SuitabilityGridSpec(
        working_srid=32637,
        bounds=(0.0, 0.0, 8.0, 8.0),
        width=4,
        height=4,
    )
    with pytest.raises(HardExclusionMaskError, match="exactly the target grid"):
        build_hard_exclusion_mask(
            grid=grid,
            boundary=boundary,
            raster_layers=(
                HardExclusionRasterLayer(
                    code="raster.rule",
                    grid=mismatched_grid,
                    excluded=np.zeros(mismatched_grid.shape, dtype=np.bool_),
                ),
            ),
        )

    with pytest.raises(HardExclusionMaskError, match="source codes must be unique"):
        build_hard_exclusion_mask(
            grid=grid,
            boundary=boundary,
            geometry_layers=(
                HardExclusionGeometryLayer(
                    code="water",
                    geometries=(),
                    working_srid=32637,
                ),
            ),
            raster_layers=(
                HardExclusionRasterLayer(
                    code="water",
                    grid=grid,
                    excluded=np.zeros(grid.shape, dtype=np.bool_),
                ),
            ),
        )


def test_geographic_crs_and_invalid_geometry_contracts_are_rejected() -> None:
    with pytest.raises(ValueError, match="projected"):
        HardExclusionBoundary(
            geometry=box(0.0, 0.0, 1.0, 1.0),
            working_srid=4326,
        )

    with pytest.raises(HardExclusionMaskError, match="Polygon or MultiPolygon"):
        from shapely.geometry import LineString

        HardExclusionBoundary(
            geometry=LineString([(0.0, 0.0), (1.0, 1.0)]),
            working_srid=32637,
        )
