import pytest
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from core.urban_generator.blocks import (
    BlockDevelopableArea,
    BlockDevelopableClippingError,
    BlockHardConstraintLayer,
    BlockPolygonizationDiagnostics,
    BlockPolygonizationResult,
    CandidateBlock,
    DevelopableBlockClipper,
)
from core.urban_generator.domain import WorkingCRS
from core.urban_generator.domain.crs import CRSContractError

WORKING_SRID = 3857


def _polygonization(*geometries: BaseGeometry) -> BlockPolygonizationResult:
    blocks = tuple(
        CandidateBlock(block_id=f"source:{index:03d}", geometry=geometry)
        for index, geometry in enumerate(geometries)
    )
    return BlockPolygonizationResult(
        working_crs=WorkingCRS(srid=WORKING_SRID),
        blocks=blocks,
        diagnostics=BlockPolygonizationDiagnostics(
            input_edge_count=0,
            unique_line_count=0,
            duplicate_line_count=0,
            candidate_block_count=len(blocks),
            cut_edge_count=0,
            dangle_edge_count=0,
            invalid_ring_count=0,
        ),
    )


def _area(
    *,
    project: BaseGeometry | None = None,
    developable: BaseGeometry | None = None,
    working_srid: int = WORKING_SRID,
) -> BlockDevelopableArea:
    return BlockDevelopableArea(
        project_boundary=project if project is not None else box(0, 0, 10, 10),
        developable_mask=developable if developable is not None else box(0, 0, 10, 10),
        working_srid=working_srid,
    )


def _hard(
    code: str,
    *geometries: BaseGeometry,
    working_srid: int = WORKING_SRID,
) -> BlockHardConstraintLayer:
    return BlockHardConstraintLayer(
        code=code,
        geometries=tuple(geometries),
        working_srid=working_srid,
    )


def test_clips_candidate_to_project_and_developable_intersection() -> None:
    polygonization = _polygonization(box(0, 0, 10, 10))
    area = _area(project=box(0, 0, 8, 10), developable=box(2, 0, 10, 10))

    result = DevelopableBlockClipper(working_srid=WORKING_SRID).clip(
        polygonization,
        area=area,
    )

    assert result.working_crs == WorkingCRS(srid=WORKING_SRID)
    assert len(result.blocks) == 1
    assert result.blocks[0].block_id == "block:00000000"
    assert result.blocks[0].source_block_id == "source:000"
    assert result.blocks[0].source_fragment_index == 0
    assert result.blocks[0].geometry.equals(box(2, 0, 8, 10))
    assert result.diagnostics.input_block_count == 1
    assert result.diagnostics.output_block_count == 1
    assert result.diagnostics.mask_clipped_block_count == 1
    assert result.diagnostics.mask_removed_block_count == 0


def test_hard_constraint_carves_hole_without_sliver_cleanup() -> None:
    polygonization = _polygonization(box(0, 0, 10, 10))
    constraint = _hard("water", box(4, 4, 6, 6))

    result = DevelopableBlockClipper(working_srid=WORKING_SRID).clip(
        polygonization,
        area=_area(),
        hard_constraints=(constraint,),
    )

    assert len(result.blocks) == 1
    assert result.blocks[0].geometry.area == pytest.approx(96.0)
    assert len(result.blocks[0].geometry.interiors) == 1
    assert result.diagnostics.hard_constraint_hit_block_count == 1
    assert result.diagnostics.hard_constraint_removed_block_count == 0
    assert result.diagnostics.split_source_block_count == 0


def test_hard_constraint_strip_splits_source_into_deterministic_fragments() -> None:
    polygonization = _polygonization(box(0, 0, 10, 10))
    constraint = _hard("protected", box(4, -1, 6, 11))

    result = DevelopableBlockClipper(working_srid=WORKING_SRID).clip(
        polygonization,
        area=_area(),
        hard_constraints=(constraint,),
    )

    assert len(result.blocks) == 2
    assert tuple(block.block_id for block in result.blocks) == (
        "block:00000000",
        "block:00000001",
    )
    assert tuple(block.source_block_id for block in result.blocks) == (
        "source:000",
        "source:000",
    )
    assert tuple(block.source_fragment_index for block in result.blocks) == (0, 1)
    assert sum(block.geometry.area for block in result.blocks) == pytest.approx(80.0)
    assert result.diagnostics.split_source_block_count == 1
    assert result.diagnostics.hard_constraint_hit_block_count == 1


def test_disjoint_mask_and_full_hard_exclusion_remove_blocks_with_diagnostics() -> None:
    polygonization = _polygonization(box(0, 0, 2, 2), box(10, 0, 12, 2))
    constraint = _hard("reserved", box(-1, -1, 3, 3))

    result = DevelopableBlockClipper(working_srid=WORKING_SRID).clip(
        polygonization,
        area=_area(project=box(0, 0, 5, 5), developable=box(0, 0, 5, 5)),
        hard_constraints=(constraint,),
    )

    assert result.blocks == ()
    assert result.diagnostics.mask_removed_block_count == 1
    assert result.diagnostics.hard_constraint_removed_block_count == 1
    assert result.diagnostics.hard_constraint_hit_block_count == 1


def test_disjoint_project_and_developable_masks_skip_constraint_scan() -> None:
    polygonization = _polygonization(box(0, 0, 2, 2), box(3, 0, 5, 2))
    constraint = _hard("unused", box(0, 0, 5, 5))

    result = DevelopableBlockClipper(working_srid=WORKING_SRID).clip(
        polygonization,
        area=_area(project=box(0, 0, 5, 5), developable=box(10, 10, 20, 20)),
        hard_constraints=(constraint,),
    )

    assert result.blocks == ()
    assert result.diagnostics.mask_removed_block_count == 2
    assert result.diagnostics.constraint_candidate_count == 0
    assert result.diagnostics.hard_constraint_hit_block_count == 0


def test_constraint_touching_only_boundary_does_not_remove_area() -> None:
    polygonization = _polygonization(box(0, 0, 10, 10))
    constraint = _hard("touch", box(10, 2, 12, 8))

    result = DevelopableBlockClipper(working_srid=WORKING_SRID).clip(
        polygonization,
        area=_area(),
        hard_constraints=(constraint,),
    )

    assert len(result.blocks) == 1
    assert result.blocks[0].geometry.equals(box(0, 0, 10, 10))
    assert result.diagnostics.constraint_candidate_count == 1
    assert result.diagnostics.hard_constraint_hit_block_count == 0


def test_output_is_deterministic_independent_of_constraint_input_order() -> None:
    polygonization = _polygonization(box(0, 0, 10, 10))
    alpha = _hard("alpha", box(1, 1, 2, 2), box(8, 8, 9, 9))
    beta = _hard("beta", box(4, -1, 6, 11))
    reversed_alpha = _hard("alpha", box(8, 8, 9, 9), box(1, 1, 2, 2))
    clipper = DevelopableBlockClipper(working_srid=WORKING_SRID)

    first = clipper.clip(
        polygonization,
        area=_area(),
        hard_constraints=(alpha, beta),
    )
    second = clipper.clip(
        polygonization,
        area=_area(),
        hard_constraints=(beta, reversed_alpha),
    )

    assert first.blocks == second.blocks
    assert first.diagnostics == second.diagnostics


def test_small_positive_fragment_is_retained_for_later_sliver_policy() -> None:
    polygonization = _polygonization(box(0, 0, 10, 10))
    constraint = _hard("almost-all", box(0.001, -1, 11, 11))

    result = DevelopableBlockClipper(working_srid=WORKING_SRID).clip(
        polygonization,
        area=_area(),
        hard_constraints=(constraint,),
    )

    assert len(result.blocks) == 1
    assert result.blocks[0].geometry.area == pytest.approx(0.01)
    assert result.diagnostics.hard_constraint_removed_block_count == 0


def test_requires_matching_metric_crs_for_masks_and_constraints() -> None:
    polygonization = _polygonization(box(0, 0, 10, 10))
    clipper = DevelopableBlockClipper(working_srid=WORKING_SRID)

    with pytest.raises(BlockDevelopableClippingError, match="developable area working_srid"):
        clipper.clip(polygonization, area=_area(working_srid=3395))

    with pytest.raises(BlockDevelopableClippingError, match="hard constraint layer"):
        clipper.clip(
            polygonization,
            area=_area(),
            hard_constraints=(
                _hard("wrong-crs", box(1, 1, 2, 2), working_srid=3395),
            ),
        )

    with pytest.raises(CRSContractError, match="not projected"):
        BlockDevelopableArea(
            project_boundary=box(0, 0, 1, 1),
            developable_mask=box(0, 0, 1, 1),
            working_srid=4326,
        )


def test_enforces_block_constraint_candidate_and_output_bounds() -> None:
    two_blocks = _polygonization(box(0, 0, 4, 4), box(6, 0, 10, 4))
    with pytest.raises(BlockDevelopableClippingError, match="input limit exceeded"):
        DevelopableBlockClipper(working_srid=WORKING_SRID, max_blocks=1).clip(
            two_blocks,
            area=_area(),
        )

    two_constraints = _hard("many", box(1, 1, 3, 3), box(7, 1, 9, 3))
    with pytest.raises(BlockDevelopableClippingError, match="geometry limit exceeded"):
        DevelopableBlockClipper(
            working_srid=WORKING_SRID,
            max_hard_constraint_geometries=1,
        ).clip(two_blocks, area=_area(), hard_constraints=(two_constraints,))

    overlapping = _hard("overlap", box(1, 1, 5, 5), box(2, 2, 6, 6))
    with pytest.raises(BlockDevelopableClippingError, match="candidate limit exceeded"):
        DevelopableBlockClipper(
            working_srid=WORKING_SRID,
            max_constraint_candidates_per_block=1,
        ).clip(
            _polygonization(box(0, 0, 4, 4)),
            area=_area(),
            hard_constraints=(overlapping,),
        )

    split = _hard("split", box(4, -1, 6, 11))
    with pytest.raises(BlockDevelopableClippingError, match="output limit exceeded"):
        DevelopableBlockClipper(
            working_srid=WORKING_SRID,
            max_output_blocks=1,
        ).clip(
            _polygonization(box(0, 0, 10, 10)),
            area=_area(),
            hard_constraints=(split,),
        )


def test_rejects_duplicate_constraint_codes() -> None:
    polygonization = _polygonization(box(0, 0, 10, 10))

    with pytest.raises(
        BlockDevelopableClippingError,
        match="duplicate hard constraint layer code",
    ):
        DevelopableBlockClipper(working_srid=WORKING_SRID).clip(
            polygonization,
            area=_area(),
            hard_constraints=(
                _hard("water", box(1, 1, 2, 2)),
                _hard("water", box(3, 3, 4, 4)),
            ),
        )
