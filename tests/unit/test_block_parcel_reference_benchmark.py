import pytest

from benchmarks.block_parcel_reference import (
    BlockParcelBenchmarkConfig,
    run_reference_block_parcel_benchmark,
)


def test_small_block_parcel_reference_fixture_has_bounded_index_work() -> None:
    result = run_reference_block_parcel_benchmark(
        BlockParcelBenchmarkConfig(grid_size=4)
    )

    assert result.block_count == 16
    assert result.zone_count == 16
    assert result.road_edge_count == 16
    assert result.parcel_count == 64
    assert result.zone_spatial_candidate_pair_count == 16
    assert result.zone_positive_overlap_pair_count == 16
    assert result.parcel_road_candidate_pair_count == 16
    assert result.parcel_frontage_overlap_pair_count == 16
    assert result.parceled_area_m2 == pytest.approx(16 * 40.0 * 20.0)
    assert result.zone_association_ms >= 0.0
    assert result.parcel_subdivision_ms >= 0.0


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("grid_size", 1),
        ("block_width_m", 0.0),
        ("block_height_m", float("inf")),
        ("column_spacing_m", 40.0),
        ("row_spacing_m", 20.0),
    ),
)
def test_block_parcel_reference_config_rejects_invalid_bounds(
    field_name: str,
    value: float,
) -> None:
    kwargs = {field_name: value}
    with pytest.raises(ValueError):
        BlockParcelBenchmarkConfig(**kwargs)
