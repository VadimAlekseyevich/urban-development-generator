import pytest
from shapely.geometry import box

from core.urban_generator.blocks import (
    BlockSliverCleaner,
    OversizedBlockSplitDiagnostics,
    OversizedBlockSplitPolicy,
    OversizedBlockSplitResult,
    SliverCleanupAction,
    SliverCleanupError,
    SliverCleanupPolicy,
    SplitBlockCandidate,
    UnmergeableSliverAction,
)
from core.urban_generator.domain import WorkingCRS
from core.urban_generator.domain.crs import CRSContractError

WORKING_SRID = 3857


def _block(
    block_id: str,
    geometry,
    *,
    input_block_id: str = "input:shared",
    source_block_id: str = "source:shared",
    source_fragment_index: int = 0,
) -> SplitBlockCandidate:
    return SplitBlockCandidate(
        block_id=block_id,
        input_block_id=input_block_id,
        source_block_id=source_block_id,
        source_fragment_index=source_fragment_index,
        split_path=(),
        geometry=geometry,
    )


def _result(
    *blocks: SplitBlockCandidate,
    working_srid: int = WORKING_SRID,
) -> OversizedBlockSplitResult:
    return OversizedBlockSplitResult(
        working_crs=WorkingCRS(srid=working_srid),
        policy=OversizedBlockSplitPolicy(max_area_m2=1_000_000.0),
        blocks=tuple(blocks),
        diagnostics=OversizedBlockSplitDiagnostics(
            input_block_count=len(blocks),
            oversized_input_block_count=0,
            output_block_count=len(blocks),
            split_attempt_count=0,
            successful_split_count=0,
            road_informed_split_count=0,
            principal_axis_split_count=0,
            unsplittable_fragment_count=0,
            road_candidate_pair_count=0,
            max_observed_split_depth=0,
        ),
    )


def _clean(
    result: OversizedBlockSplitResult,
    *,
    min_area_m2: float,
    action: UnmergeableSliverAction = UnmergeableSliverAction.KEEP,
    merge_within_input_block_only: bool = True,
    **kwargs,
):
    return BlockSliverCleaner(
        working_srid=WORKING_SRID,
        policy=SliverCleanupPolicy(
            min_area_m2=min_area_m2,
            unmergeable_action=action,
            merge_within_input_block_only=merge_within_input_block_only,
        ),
        **kwargs,
    ).cleanup(result)


def test_non_sliver_passes_through_without_events() -> None:
    result = _clean(
        _result(_block("split:a", box(0, 0, 10, 10))),
        min_area_m2=20.0,
    )

    assert len(result.blocks) == 1
    assert result.blocks[0].member_block_ids == ("split:a",)
    assert result.blocks[0].geometry.equals(box(0, 0, 10, 10))
    assert result.events == ()
    assert result.diagnostics.input_sliver_count == 0
    assert result.diagnostics.input_area_m2 == pytest.approx(100.0)
    assert result.diagnostics.output_area_m2 == pytest.approx(100.0)
    assert result.diagnostics.dropped_area_m2 == 0.0


def test_sliver_merges_with_same_input_sibling_and_preserves_lineage() -> None:
    sliver = _block("split:a", box(0, 0, 1, 10))
    sibling = _block("split:b", box(1, 0, 10, 10))

    result = _clean(_result(sliver, sibling), min_area_m2=20.0)

    assert len(result.blocks) == 1
    assert result.blocks[0].member_block_ids == ("split:a", "split:b")
    assert result.blocks[0].input_block_ids == ("input:shared",)
    assert result.blocks[0].geometry.area == pytest.approx(100.0)
    assert len(result.events) == 1
    event = result.events[0]
    assert event.action is SliverCleanupAction.MERGED
    assert event.source_member_block_ids == ("split:a",)
    assert event.target_member_block_ids == ("split:b",)
    assert event.source_area_m2 == pytest.approx(10.0)
    assert event.shared_boundary_m == pytest.approx(10.0)
    assert result.diagnostics.merged_event_count == 1
    assert result.diagnostics.dropped_area_m2 == 0.0


def test_multiple_slivers_contract_adjacency_until_threshold_is_met() -> None:
    blocks = (
        _block("split:a", box(0, 0, 1, 10)),
        _block("split:b", box(1, 0, 2, 10)),
        _block("split:c", box(2, 0, 3, 10)),
    )

    result = _clean(_result(*blocks), min_area_m2=25.0)

    assert len(result.blocks) == 1
    assert result.blocks[0].member_block_ids == ("split:a", "split:b", "split:c")
    assert result.blocks[0].geometry.area == pytest.approx(30.0)
    assert result.diagnostics.merged_event_count == 2
    assert result.diagnostics.operation_count == 2
    assert result.diagnostics.input_area_m2 == pytest.approx(30.0)
    assert result.diagnostics.output_area_m2 == pytest.approx(30.0)


def test_default_scope_does_not_merge_across_original_road_bounded_blocks() -> None:
    left = _block(
        "split:left",
        box(0, 0, 1, 10),
        input_block_id="input:left",
    )
    right = _block(
        "split:right",
        box(1, 0, 2, 10),
        input_block_id="input:right",
    )

    result = _clean(_result(left, right), min_area_m2=20.0)

    assert len(result.blocks) == 2
    assert tuple(event.action for event in result.events) == (
        SliverCleanupAction.KEPT_UNRESOLVED,
        SliverCleanupAction.KEPT_UNRESOLVED,
    )
    assert result.diagnostics.merged_event_count == 0
    assert result.diagnostics.kept_unresolved_event_count == 2
    assert result.diagnostics.adjacency_pair_count == 0
    assert result.diagnostics.dropped_area_m2 == 0.0


def test_explicit_drop_records_deleted_area_in_event_and_diagnostics() -> None:
    sliver = _block(
        "split:sliver",
        box(0, 0, 1, 10),
        input_block_id="input:sliver",
    )
    neighbor = _block(
        "split:neighbor",
        box(1, 0, 10, 10),
        input_block_id="input:neighbor",
    )

    result = _clean(
        _result(sliver, neighbor),
        min_area_m2=20.0,
        action=UnmergeableSliverAction.DROP,
    )

    assert len(result.blocks) == 1
    assert result.blocks[0].member_block_ids == ("split:neighbor",)
    assert len(result.events) == 1
    event = result.events[0]
    assert event.action is SliverCleanupAction.DROPPED
    assert event.source_member_block_ids == ("split:sliver",)
    assert event.source_area_m2 == pytest.approx(10.0)
    assert result.diagnostics.dropped_event_count == 1
    assert result.diagnostics.input_area_m2 == pytest.approx(100.0)
    assert result.diagnostics.output_area_m2 == pytest.approx(90.0)
    assert result.diagnostics.dropped_area_m2 == pytest.approx(10.0)


def test_cross_input_merge_requires_explicit_opt_in() -> None:
    left = _block(
        "split:left",
        box(0, 0, 1, 10),
        input_block_id="input:left",
    )
    right = _block(
        "split:right",
        box(1, 0, 10, 10),
        input_block_id="input:right",
    )

    result = _clean(
        _result(left, right),
        min_area_m2=20.0,
        merge_within_input_block_only=False,
    )

    assert len(result.blocks) == 1
    assert result.blocks[0].input_block_ids == ("input:left", "input:right")
    assert result.events[0].action is SliverCleanupAction.MERGED
    assert result.diagnostics.adjacency_pair_count == 1


def test_merge_target_prefers_longest_shared_boundary() -> None:
    sliver = _block("split:sliver", box(0, 0, 1, 4))
    long_neighbor = _block("split:long", box(1, 0, 3, 4))
    short_neighbor = _block("split:short", box(0, 4, 1, 10))

    result = _clean(
        _result(sliver, long_neighbor, short_neighbor),
        min_area_m2=5.0,
    )

    assert result.events[0].action is SliverCleanupAction.MERGED
    assert result.events[0].target_member_block_ids == ("split:long",)
    assert result.events[0].shared_boundary_m == pytest.approx(4.0)


def test_positive_area_overlap_is_rejected_instead_of_repaired_silently() -> None:
    first = _block("split:a", box(0, 0, 2, 2))
    second = _block("split:b", box(1, 0, 3, 2))

    with pytest.raises(SliverCleanupError, match="must not overlap by positive area"):
        _clean(_result(first, second), min_area_m2=10.0)


def test_cleanup_is_deterministic_independent_of_input_order() -> None:
    blocks = (
        _block("split:a", box(0, 0, 1, 10)),
        _block("split:b", box(1, 0, 2, 10)),
        _block("split:c", box(2, 0, 5, 10)),
    )

    first = _clean(_result(*blocks), min_area_m2=25.0)
    second = _clean(_result(*reversed(blocks)), min_area_m2=25.0)

    assert first.blocks == second.blocks
    assert first.events == second.events
    assert first.diagnostics == second.diagnostics


def test_candidate_and_operation_bounds_are_enforced() -> None:
    left = _block("split:left", box(0, 0, 1, 2))
    middle = _block("split:middle", box(1, 0, 2, 2))
    right = _block("split:right", box(2, 0, 3, 2))

    with pytest.raises(SliverCleanupError, match="spatial candidate limit exceeded"):
        _clean(
            _result(left, middle, right),
            min_area_m2=10.0,
            max_candidates_per_block=1,
        )

    isolated = (
        _block("split:a", box(0, 0, 1, 1), input_block_id="input:a"),
        _block("split:b", box(10, 0, 11, 1), input_block_id="input:b"),
    )
    with pytest.raises(SliverCleanupError, match="operation limit exceeded"):
        _clean(
            _result(*isolated),
            min_area_m2=10.0,
            max_operations=1,
        )


def test_requires_matching_metric_crs_and_valid_policy() -> None:
    alternate = _result(
        _block("split:a", box(0, 0, 1, 1)),
        working_srid=3395,
    )
    cleaner = BlockSliverCleaner(
        working_srid=WORKING_SRID,
        policy=SliverCleanupPolicy(min_area_m2=10.0),
    )

    with pytest.raises(SliverCleanupError, match="split result working CRS"):
        cleaner.cleanup(alternate)

    with pytest.raises(SliverCleanupError, match="min_area_m2"):
        SliverCleanupPolicy(min_area_m2=0.0)

    with pytest.raises(CRSContractError, match="not projected"):
        BlockSliverCleaner(
            working_srid=4326,
            policy=SliverCleanupPolicy(min_area_m2=10.0),
        )
