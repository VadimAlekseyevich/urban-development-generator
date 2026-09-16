import math

import pytest
from shapely.geometry import LineString, box

from core.urban_generator.blocks import (
    BlockDevelopableClippingDiagnostics,
    BlockDevelopableClippingResult,
    BlockFrontageValidator,
    BlockMetricsCalculator,
    DevelopableBlockCandidate,
    OversizedBlockSplitError,
    OversizedBlockSplitPolicy,
    OversizedBlockSplitter,
)
from core.urban_generator.domain import WorkingCRS, WorldStateContract
from core.urban_generator.domain.crs import CRSContractError
from core.urban_generator.roads import NodedRoad, RoadGraph, RoadGraphBuilder, RoadGraphInput

WORKING_SRID = 3857


def _block(
    geometry,
    *,
    block_id: str = "block:input",
    source_block_id: str = "source:input",
    source_fragment_index: int = 0,
) -> DevelopableBlockCandidate:
    return DevelopableBlockCandidate(
        block_id=block_id,
        source_block_id=source_block_id,
        source_fragment_index=source_fragment_index,
        geometry=geometry,
    )


def _road_graph(
    *roads: tuple[str, tuple[tuple[float, float], ...]],
    working_srid: int = WORKING_SRID,
) -> RoadGraph:
    inputs = tuple(
        RoadGraphInput(
            road=NodedRoad(road_id=road_id, parts=(LineString(coordinates),)),
            state=WorldStateContract.fixed_source(),
        )
        for road_id, coordinates in roads
    )
    return RoadGraphBuilder(working_srid=working_srid).build(inputs)


def _frontage_result(
    *blocks: DevelopableBlockCandidate,
    road_graph: RoadGraph,
    working_srid: int = WORKING_SRID,
):
    clipping = BlockDevelopableClippingResult(
        working_crs=WorkingCRS(srid=working_srid),
        blocks=tuple(blocks),
        diagnostics=BlockDevelopableClippingDiagnostics(
            input_block_count=len(blocks),
            output_block_count=len(blocks),
            mask_clipped_block_count=0,
            mask_removed_block_count=0,
            hard_constraint_hit_block_count=0,
            hard_constraint_removed_block_count=0,
            split_source_block_count=0,
            hard_constraint_geometry_count=0,
            constraint_candidate_count=0,
            validity_repair_count=0,
        ),
    )
    metrics = BlockMetricsCalculator(working_srid=working_srid).calculate(clipping)
    return BlockFrontageValidator(working_srid=working_srid).validate(
        metrics,
        road_graph=road_graph,
    )


def _split(
    geometry,
    *,
    max_area_m2: float,
    road_graph: RoadGraph | None = None,
    **kwargs,
):
    graph = road_graph or _road_graph()
    frontage = _frontage_result(_block(geometry), road_graph=graph)
    return OversizedBlockSplitter(
        working_srid=WORKING_SRID,
        policy=OversizedBlockSplitPolicy(max_area_m2=max_area_m2),
        **kwargs,
    ).split(frontage, road_graph=graph)


def test_non_oversized_block_is_preserved_without_split() -> None:
    result = _split(box(0, 0, 10, 10), max_area_m2=100.0)

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.block_id == "block:00000000"
    assert block.input_block_id == "block:input"
    assert block.source_block_id == "source:input"
    assert block.source_fragment_index == 0
    assert block.split_path == ()
    assert block.split_depth == 0
    assert block.geometry.equals(box(0, 0, 10, 10))
    assert result.diagnostics.oversized_input_block_count == 0
    assert result.diagnostics.split_attempt_count == 0
    assert result.diagnostics.successful_split_count == 0


def test_principal_axis_split_conserves_area_without_roads() -> None:
    source = box(0, 0, 20, 10)
    result = _split(source, max_area_m2=100.0)

    assert len(result.blocks) == 2
    assert all(block.geometry.area == pytest.approx(100.0) for block in result.blocks)
    assert math.fsum(block.geometry.area for block in result.blocks) == pytest.approx(source.area)
    assert tuple(block.split_path for block in result.blocks) == ((0,), (1,))
    assert result.diagnostics.successful_split_count == 1
    assert result.diagnostics.principal_axis_split_count == 1
    assert result.diagnostics.road_informed_split_count == 0
    assert result.diagnostics.unsplittable_fragment_count == 0


def test_recursive_principal_axis_split_is_bounded_and_conserves_area() -> None:
    source = box(0, 0, 40, 10)
    result = _split(source, max_area_m2=100.0)

    assert len(result.blocks) == 4
    assert all(block.geometry.area == pytest.approx(100.0) for block in result.blocks)
    assert math.fsum(block.geometry.area for block in result.blocks) == pytest.approx(source.area)
    assert result.diagnostics.split_attempt_count == 3
    assert result.diagnostics.successful_split_count == 3
    assert result.diagnostics.principal_axis_split_count == 3
    assert result.diagnostics.max_observed_split_depth == 2


def test_positive_road_frontage_informs_split_direction() -> None:
    source = box(0, 0, 30, 10)
    graph = _road_graph(("left-frontage", ((0, 0), (0, 10))))
    result = _split(source, max_area_m2=150.0, road_graph=graph)

    assert len(result.blocks) == 2
    assert result.diagnostics.road_informed_split_count == 1
    assert result.diagnostics.principal_axis_split_count == 0
    assert all(block.geometry.area == pytest.approx(150.0) for block in result.blocks)
    # A vertical frontage supplies a vertical reference axis, so the splitter cuts horizontally.
    assert all((block.geometry.bounds[2] - block.geometry.bounds[0]) == pytest.approx(30.0) for block in result.blocks)
    assert all((block.geometry.bounds[3] - block.geometry.bounds[1]) == pytest.approx(5.0) for block in result.blocks)


def test_transverse_crossing_does_not_become_road_informed_frontage() -> None:
    graph = _road_graph(("crossing", ((15, -5), (15, 15))))
    result = _split(box(0, 0, 30, 10), max_area_m2=150.0, road_graph=graph)

    assert len(result.blocks) == 2
    assert result.diagnostics.road_informed_split_count == 0
    assert result.diagnostics.principal_axis_split_count == 1
    assert result.diagnostics.road_candidate_pair_count == 1


def test_max_depth_retains_oversized_fragments_with_explicit_diagnostics() -> None:
    source = box(0, 0, 40, 10)
    result = _split(source, max_area_m2=100.0, max_split_depth=1)

    assert len(result.blocks) == 2
    assert all(block.geometry.area == pytest.approx(200.0) for block in result.blocks)
    assert math.fsum(block.geometry.area for block in result.blocks) == pytest.approx(source.area)
    assert result.diagnostics.successful_split_count == 1
    assert result.diagnostics.unsplittable_fragment_count == 2
    assert result.diagnostics.max_observed_split_depth == 1


def test_split_is_deterministic_for_road_input_order() -> None:
    source = box(0, 0, 30, 10)
    first_graph = _road_graph(
        ("left", ((0, 0), (0, 10))),
        ("right", ((30, 10), (30, 0))),
    )
    second_graph = _road_graph(
        ("right", ((30, 0), (30, 10))),
        ("left", ((0, 10), (0, 0))),
    )

    first = _split(source, max_area_m2=150.0, road_graph=first_graph)
    second = _split(source, max_area_m2=150.0, road_graph=second_graph)

    assert first.blocks == second.blocks
    assert first.diagnostics == second.diagnostics


def test_enforces_split_operation_candidate_and_output_bounds() -> None:
    with pytest.raises(OversizedBlockSplitError, match="split operation limit exceeded"):
        _split(box(0, 0, 40, 10), max_area_m2=100.0, max_split_operations=2)

    graph = _road_graph(
        ("bottom", ((0, 0), (20, 0))),
        ("top", ((0, 10), (20, 10))),
    )
    with pytest.raises(OversizedBlockSplitError, match="split road candidate limit exceeded"):
        _split(
            box(0, 0, 20, 10),
            max_area_m2=100.0,
            road_graph=graph,
            max_road_candidates_per_fragment=1,
        )

    with pytest.raises(OversizedBlockSplitError, match="split output limit exceeded"):
        _split(
            box(0, 0, 20, 10),
            max_area_m2=100.0,
            max_output_blocks=1,
        )


def test_requires_matching_metric_crs() -> None:
    splitter = OversizedBlockSplitter(
        working_srid=WORKING_SRID,
        policy=OversizedBlockSplitPolicy(max_area_m2=100.0),
    )
    alternate_graph = _road_graph(working_srid=3395)
    alternate_frontage = _frontage_result(
        _block(box(0, 0, 20, 10)),
        road_graph=alternate_graph,
        working_srid=3395,
    )

    with pytest.raises(OversizedBlockSplitError, match="frontage working CRS"):
        splitter.split(alternate_frontage, road_graph=_road_graph())

    main_frontage = _frontage_result(
        _block(box(0, 0, 20, 10)),
        road_graph=_road_graph(),
    )
    with pytest.raises(OversizedBlockSplitError, match="road graph working CRS"):
        splitter.split(main_frontage, road_graph=alternate_graph)

    with pytest.raises(CRSContractError, match="not projected"):
        OversizedBlockSplitter(
            working_srid=4326,
            policy=OversizedBlockSplitPolicy(max_area_m2=100.0),
        )


def test_policy_and_constructor_bounds_require_positive_finite_values() -> None:
    with pytest.raises(OversizedBlockSplitError, match="max_area_m2"):
        OversizedBlockSplitPolicy(max_area_m2=0.0)
    with pytest.raises(OversizedBlockSplitError, match="max_area_m2"):
        OversizedBlockSplitPolicy(max_area_m2=float("inf"))
    with pytest.raises(OversizedBlockSplitError, match="max_split_depth"):
        OversizedBlockSplitter(
            working_srid=WORKING_SRID,
            policy=OversizedBlockSplitPolicy(max_area_m2=100.0),
            max_split_depth=0,
        )
