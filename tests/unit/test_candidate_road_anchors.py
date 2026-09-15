from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import box

from core.urban_generator.roads import (
    CandidateRoadAnchorError,
    CandidateRoadAnchorPolicy,
    CandidateRoadAnchorSampler,
)
from core.urban_generator.suitability import SuitabilityGridSpec, WeightedSuitabilityResult
from core.urban_generator.zoning import (
    SuitabilityTargetShareAssigner,
    ZoneClass,
    ZoneClassConfig,
    ZoningConfig,
    ZoningPartitionCell,
    ZoningPartitionResult,
    ZoningSeed,
)


def _config() -> ZoningConfig:
    return ZoningConfig(
        version="road-anchor-test-v1",
        zones=(
            ZoneClassConfig(ZoneClass.RESIDENTIAL, 0.5, 1.0),
            ZoneClassConfig(ZoneClass.MIXED, 0.5, 1.0),
            ZoneClassConfig(ZoneClass.PUBLIC, 0.0, 1.0),
            ZoneClassConfig(ZoneClass.RECREATION, 0.0, 1.0),
        ),
    )


def _partition(
    *,
    width_m: float = 20.0,
    height_m: float = 10.0,
    working_srid: int = 32637,
) -> ZoningPartitionResult:
    split_x = width_m / 2.0
    geometries = (
        box(0.0, 0.0, split_x, height_m),
        box(split_x, 0.0, width_m, height_m),
    )
    seeds = (
        ZoningSeed(
            row=0,
            col=0,
            x_m=split_x / 2.0,
            y_m=height_m / 2.0,
            suitability_score=0.9,
        ),
        ZoningSeed(
            row=0,
            col=1,
            x_m=split_x + split_x / 2.0,
            y_m=height_m / 2.0,
            suitability_score=0.8,
        ),
    )
    cells = tuple(
        ZoningPartitionCell(
            seed_index=index,
            seed=seeds[index],
            geometry=geometry,
            area_m2=float(geometry.area),
            validity_repaired=False,
        )
        for index, geometry in enumerate(geometries)
    )
    developable = box(0.0, 0.0, width_m, height_m)
    return ZoningPartitionResult(
        working_srid=working_srid,
        developable_area=developable,
        cells=cells,
        developable_area_m2=float(developable.area),
        covered_area_m2=float(developable.area),
        uncovered_area_m2=0.0,
        overlap_area_m2=0.0,
        developable_validity_repaired=False,
        repaired_cell_count=0,
    )


def _assignment(partition: ZoningPartitionResult):
    return SuitabilityTargetShareAssigner().assign(
        partition=partition,
        config=_config(),
    )


def _suitability(
    scores: np.ndarray,
    *,
    width_m: float = 20.0,
    height_m: float = 10.0,
    working_srid: int = 32637,
    valid_mask: np.ndarray | None = None,
) -> WeightedSuitabilityResult:
    scores = np.asarray(scores, dtype=np.float64)
    if valid_mask is None:
        valid_mask = np.ones(scores.shape, dtype=np.bool_)
    else:
        valid_mask = np.asarray(valid_mask, dtype=np.bool_)
    hard_mask = np.zeros(scores.shape, dtype=np.bool_)
    return WeightedSuitabilityResult(
        grid=SuitabilityGridSpec(
            working_srid=working_srid,
            bounds=(0.0, 0.0, width_m, height_m),
            width=scores.shape[1],
            height=scores.shape[0],
        ),
        scores=scores,
        valid_mask=valid_mask,
        hard_excluded_mask=hard_mask,
        config_version="suitability-test-v1",
        config_fingerprint="b" * 64,
        factor_versions=(("test_factor", "1"),),
    )


def test_sampler_ranks_suitability_and_preserves_zone_metadata() -> None:
    partition = _partition()
    assignment = _assignment(partition)
    suitability = _suitability(
        np.array(
            [
                [0.1, 0.9, 0.8, 0.7],
                [0.95, 0.6, 0.85, 0.2],
            ]
        )
    )

    result = CandidateRoadAnchorSampler().sample(
        partition=partition,
        assignment=assignment,
        suitability=suitability,
        policy=CandidateRoadAnchorPolicy(
            max_candidates=3,
            max_sampled_cells=8,
            minimum_suitability_score=0.8,
        ),
    )

    assert tuple(anchor.suitability_score for anchor in result.anchors) == (0.95, 0.9, 0.85)
    assert tuple(anchor.zone_class for anchor in result.anchors) == (
        ZoneClass.RESIDENTIAL,
        ZoneClass.RESIDENTIAL,
        ZoneClass.MIXED,
    )
    assert tuple(anchor.zoning_cell_index for anchor in result.anchors) == (0, 0, 1)
    assert tuple((anchor.point.x_m, anchor.point.y_m) for anchor in result.anchors) == (
        (2.5, 2.5),
        (7.5, 7.5),
        (12.5, 2.5),
    )
    assert tuple(anchor.anchor_id for anchor in result.anchors) == (
        "anchor:r00000001:c00000000",
        "anchor:r00000000:c00000001",
        "anchor:r00000001:c00000002",
    )
    assert result.diagnostics.eligible_candidate_count == 4
    assert result.diagnostics.selected_candidate_count == 3
    assert result.diagnostics.candidate_limit_reached is True
    assert result.diagnostics.sampling_limit_applied is False
    assert result.zoning_config_fingerprint == assignment.zoning_config_fingerprint
    assert result.suitability_config_fingerprint == suitability.config_fingerprint


def test_sampler_filters_by_zone_class_minimum_score_and_valid_mask() -> None:
    partition = _partition()
    assignment = _assignment(partition)
    scores = np.array(
        [
            [0.95, 0.9, 0.85, 0.8],
            [0.75, 0.7, 0.65, 0.6],
        ]
    )
    valid_mask = np.ones(scores.shape, dtype=np.bool_)
    valid_mask[0, 2] = False
    scores[0, 2] = 0.0
    suitability = _suitability(scores, valid_mask=valid_mask)

    result = CandidateRoadAnchorSampler().sample(
        partition=partition,
        assignment=assignment,
        suitability=suitability,
        policy=CandidateRoadAnchorPolicy(
            max_candidates=10,
            max_sampled_cells=8,
            minimum_suitability_score=0.7,
            allowed_zone_classes=(ZoneClass.MIXED,),
        ),
    )

    assert tuple(anchor.zone_class for anchor in result.anchors) == (ZoneClass.MIXED,)
    assert tuple(anchor.suitability_score for anchor in result.anchors) == (0.8,)
    assert result.diagnostics.valid_sampled_cell_count == 7
    assert result.diagnostics.eligible_candidate_count == 1
    assert result.diagnostics.zone_counts[1].zone_class is ZoneClass.MIXED
    assert result.diagnostics.zone_counts[1].selected_count == 1


def test_sampler_applies_two_dimensional_sample_budget_before_zone_lookup() -> None:
    partition = _partition(width_m=100.0, height_m=100.0)
    assignment = _assignment(partition)
    suitability = _suitability(
        np.ones((100, 100), dtype=np.float64),
        width_m=100.0,
        height_m=100.0,
    )

    result = CandidateRoadAnchorSampler().sample(
        partition=partition,
        assignment=assignment,
        suitability=suitability,
        policy=CandidateRoadAnchorPolicy(
            max_candidates=100,
            max_sampled_cells=25,
        ),
    )

    assert result.diagnostics.grid_cell_count == 10_000
    assert result.diagnostics.sampled_row_count == 5
    assert result.diagnostics.sampled_col_count == 5
    assert result.diagnostics.sampled_cell_count == 25
    assert result.diagnostics.sampling_limit_applied is True
    assert len(result.anchors) == 25


def test_sampler_is_deterministic_for_equal_scores_and_policy_zone_order() -> None:
    partition = _partition()
    assignment = _assignment(partition)
    suitability = _suitability(np.full((2, 4), 0.5, dtype=np.float64))
    policy = CandidateRoadAnchorPolicy(
        max_candidates=8,
        max_sampled_cells=8,
        allowed_zone_classes=(ZoneClass.MIXED, ZoneClass.RESIDENTIAL),
    )
    sampler = CandidateRoadAnchorSampler()

    first = sampler.sample(
        partition=partition,
        assignment=assignment,
        suitability=suitability,
        policy=policy,
    )
    second = sampler.sample(
        partition=partition,
        assignment=assignment,
        suitability=suitability,
        policy=policy,
    )

    assert policy.allowed_zone_classes == (ZoneClass.RESIDENTIAL, ZoneClass.MIXED)
    assert first == second
    assert tuple(anchor.zone_class for anchor in first.anchors[:4]) == (
        ZoneClass.RESIDENTIAL,
        ZoneClass.RESIDENTIAL,
        ZoneClass.RESIDENTIAL,
        ZoneClass.RESIDENTIAL,
    )
    assert tuple((anchor.raster_row, anchor.raster_col) for anchor in first.anchors[:4]) == (
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
    )


def test_sampler_can_return_empty_candidate_set_without_failing() -> None:
    partition = _partition()
    assignment = _assignment(partition)
    suitability = _suitability(np.zeros((2, 4), dtype=np.float64))

    result = CandidateRoadAnchorSampler().sample(
        partition=partition,
        assignment=assignment,
        suitability=suitability,
        policy=CandidateRoadAnchorPolicy(minimum_suitability_score=0.1),
    )

    assert result.anchors == ()
    assert result.diagnostics.eligible_candidate_count == 0
    assert result.diagnostics.selected_candidate_count == 0
    assert result.diagnostics.candidate_limit_reached is False


def test_sampler_rejects_crs_mismatch_and_invalid_policy_bounds() -> None:
    partition = _partition(working_srid=32637)
    assignment = _assignment(partition)
    suitability = _suitability(
        np.ones((2, 4), dtype=np.float64),
        working_srid=3857,
    )

    with pytest.raises(CandidateRoadAnchorError, match="same working_srid"):
        CandidateRoadAnchorSampler().sample(
            partition=partition,
            assignment=assignment,
            suitability=suitability,
        )

    with pytest.raises(CandidateRoadAnchorError, match="max_candidates must be a positive"):
        CandidateRoadAnchorPolicy(max_candidates=0)
    with pytest.raises(CandidateRoadAnchorError, match="minimum_suitability_score"):
        CandidateRoadAnchorPolicy(minimum_suitability_score=1.1)
    with pytest.raises(CandidateRoadAnchorError, match="duplicates"):
        CandidateRoadAnchorPolicy(
            allowed_zone_classes=(ZoneClass.RESIDENTIAL, ZoneClass.RESIDENTIAL)
        )
