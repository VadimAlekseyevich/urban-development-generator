import numpy as np
import pytest
from shapely.geometry import box

from backend.app.adapters.spatial_generation_chain import build_developable_area
from backend.app.application.generation import GenerationExecutionError
from core.urban_generator.stages import ConstraintMaskStageInput
from core.urban_generator.suitability import (
    HardExclusionBoundary,
    HardExclusionGeometryLayer,
    SuitabilityGridSpec,
    WeightedSuitabilityResult,
)


def test_zoning_geometry_respects_validity_threshold_and_exact_boundary() -> None:
    grid = SuitabilityGridSpec(32637, (0, 0, 30, 20), 3, 2)
    boundary = box(0, 0, 28, 20)
    hard = box(20, 0, 22, 10)
    prepared = ConstraintMaskStageInput(
        grid=grid,
        boundary=HardExclusionBoundary(boundary, 32637),
        geometry_layers=(HardExclusionGeometryLayer("water", (hard,), 32637),),
    )
    scores = np.array([[0.9, 0.1, 0.0], [0.8, 0.0, 0.7]])
    valid = np.array([[True, True, False], [True, False, True]])
    weighted = WeightedSuitabilityResult(
        grid=grid, scores=scores, valid_mask=valid,
        hard_excluded_mask=np.array([[False, False, True], [False, False, False]]),
        config_version="v1", config_fingerprint="a" * 64, factor_versions=(("landuse", "v1"),),
    )
    area, filtered = build_developable_area(
        prepared=prepared, suitability=weighted, minimum_score=0.5,
    )
    assert area.area == pytest.approx(260)
    assert boundary.covers(area)
    assert area.intersection(hard).area == 0
    assert filtered.valid_mask.tolist() == [[True, False, False], [True, False, True]]
    assert filtered.scores[0, 1] == 0
    assert weighted.scores[0, 1] == 0.1
    with pytest.raises(GenerationExecutionError, match="no developable cells"):
        build_developable_area(prepared=prepared, suitability=weighted, minimum_score=1.0)
