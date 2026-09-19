from __future__ import annotations

import math

import pytest

from core.urban_generator.demography import (
    PopulationRasterError,
    PopulationRasterNoDataPolicy,
    PopulationRasterSampleLimitError,
    PopulationRasterSampler,
    PopulationRasterSamplingPolicy,
    PopulationRasterSubject,
    PopulationRasterValueKind,
    PopulationRasterWindow,
)


class FakePopulationRasterSource:
    def __init__(
        self,
        values: tuple[tuple[float | int | None, ...], ...],
        *,
        working_srid: int = 32637,
        cell_area_m2: float = 100.0,
        value_kind: PopulationRasterValueKind = (
            PopulationRasterValueKind.POPULATION_PER_CELL
        ),
    ) -> None:
        self.values = values
        self.working_srid = working_srid
        self.cell_area_m2 = cell_area_m2
        self.value_kind = value_kind
        self.height = len(values)
        self.width = len(values[0])
        self.reads: list[PopulationRasterWindow] = []

    def read_window(
        self,
        *,
        window: PopulationRasterWindow,
    ) -> tuple[tuple[float | int | None, ...], ...]:
        self.reads.append(window)
        return tuple(
            tuple(
                self.values[row][col]
                for col in range(
                    window.col_off,
                    window.col_off + window.width,
                )
            )
            for row in range(
                window.row_off,
                window.row_off + window.height,
            )
        )


def _subject(
    subject_id: str = "block-1",
    *windows: PopulationRasterWindow,
    working_srid: int = 32637,
) -> PopulationRasterSubject:
    resolved = windows or (
        PopulationRasterWindow(
            row_off=0,
            col_off=0,
            height=2,
            width=2,
        ),
    )
    return PopulationRasterSubject(
        subject_id=subject_id,
        windows=resolved,
        working_srid=working_srid,
    )


def test_population_per_cell_is_normalized_to_population_and_density() -> None:
    source = FakePopulationRasterSource(
        ((10.0, None), (20.0, 30.0)),
        cell_area_m2=100.0,
    )

    result = PopulationRasterSampler(source=source).sample((_subject(),))

    sample = result.samples[0]
    assert sample.requested_cell_count == 4
    assert sample.valid_cell_count == 3
    assert sample.nodata_cell_count == 1
    assert sample.sampled_area_m2 == 300.0
    assert sample.sampled_population == 60.0
    assert sample.mean_density_per_km2 == 200_000.0
    assert sample.valid_fraction == 0.75
    assert sample.has_valid_data is True


def test_density_cells_are_converted_using_cell_area() -> None:
    source = FakePopulationRasterSource(
        ((1_000.0, 2_000.0),),
        cell_area_m2=100.0,
        value_kind=PopulationRasterValueKind.DENSITY_PER_KM2,
    )
    subject = PopulationRasterSubject(
        subject_id="zone-1",
        windows=(PopulationRasterWindow(0, 0, 1, 2),),
        working_srid=32637,
    )

    sample = PopulationRasterSampler(source=source).sample((subject,)).samples[0]

    assert sample.sampled_population == pytest.approx(0.3)
    assert sample.sampled_area_m2 == 200.0
    assert sample.mean_density_per_km2 == pytest.approx(1_500.0)


def test_all_nodata_is_explicit_zero_without_nan_or_inf() -> None:
    source = FakePopulationRasterSource(((None, None), (None, None)))

    sample = PopulationRasterSampler(source=source).sample((_subject(),)).samples[0]

    assert sample.valid_cell_count == 0
    assert sample.nodata_cell_count == 4
    assert sample.sampled_area_m2 == 0.0
    assert sample.sampled_population == 0.0
    assert sample.mean_density_per_km2 == 0.0
    assert sample.has_valid_data is False
    assert math.isfinite(sample.mean_density_per_km2)


def test_reject_nodata_policy_fails_explicitly() -> None:
    source = FakePopulationRasterSource(((1.0, None), (2.0, 3.0)))
    sampler = PopulationRasterSampler(
        source=source,
        policy=PopulationRasterSamplingPolicy(
            nodata_policy=PopulationRasterNoDataPolicy.REJECT,
        ),
    )

    with pytest.raises(PopulationRasterError, match="nodata rejected"):
        sampler.sample((_subject(),))


def test_limits_are_checked_before_source_reads() -> None:
    source = FakePopulationRasterSource(
        tuple(tuple(float(col) for col in range(10)) for _ in range(10))
    )
    sampler = PopulationRasterSampler(
        source=source,
        policy=PopulationRasterSamplingPolicy(
            max_subjects=2,
            max_windows_per_subject=2,
            max_sample_cells=4,
        ),
    )

    with pytest.raises(
        PopulationRasterSampleLimitError,
        match="sample-cell limit exceeded",
    ):
        sampler.sample(
            (
                PopulationRasterSubject(
                    subject_id="too-large",
                    windows=(PopulationRasterWindow(0, 0, 1, 5),),
                    working_srid=32637,
                ),
            )
        )
    assert source.reads == []


def test_window_limit_is_checked_before_source_reads() -> None:
    source = FakePopulationRasterSource(((1.0, 2.0), (3.0, 4.0)))
    sampler = PopulationRasterSampler(
        source=source,
        policy=PopulationRasterSamplingPolicy(
            max_windows_per_subject=1,
        ),
    )
    subject = PopulationRasterSubject(
        subject_id="block-1",
        windows=(
            PopulationRasterWindow(0, 0, 1, 1),
            PopulationRasterWindow(1, 1, 1, 1),
        ),
        working_srid=32637,
    )

    with pytest.raises(
        PopulationRasterSampleLimitError,
        match="window limit exceeded",
    ):
        sampler.sample((subject,))
    assert source.reads == []


def test_overlapping_windows_for_one_subject_are_rejected() -> None:
    with pytest.raises(PopulationRasterError, match="must not overlap"):
        PopulationRasterSubject(
            subject_id="block-1",
            windows=(
                PopulationRasterWindow(0, 0, 2, 2),
                PopulationRasterWindow(1, 1, 2, 2),
            ),
            working_srid=32637,
        )


def test_crs_and_bounds_are_validated_before_reads() -> None:
    source = FakePopulationRasterSource(((1.0, 2.0), (3.0, 4.0)))
    sampler = PopulationRasterSampler(source=source)

    with pytest.raises(PopulationRasterError, match="working_srid"):
        sampler.sample((_subject(working_srid=3857),))
    assert source.reads == []

    out_of_bounds = PopulationRasterSubject(
        subject_id="block-1",
        windows=(PopulationRasterWindow(0, 1, 1, 2),),
        working_srid=32637,
    )
    with pytest.raises(PopulationRasterError, match="exceeds source width"):
        sampler.sample((out_of_bounds,))
    assert source.reads == []


def test_source_window_shape_and_values_are_validated() -> None:
    class BrokenSource(FakePopulationRasterSource):
        def read_window(
            self,
            *,
            window: PopulationRasterWindow,
        ) -> tuple[tuple[float | int | None, ...], ...]:
            return ((-1.0,),)

    sampler = PopulationRasterSampler(
        source=BrokenSource(((1.0, 2.0), (3.0, 4.0)))
    )
    subject = PopulationRasterSubject(
        subject_id="block-1",
        windows=(PopulationRasterWindow(0, 0, 1, 1),),
        working_srid=32637,
    )

    with pytest.raises(PopulationRasterError, match="non-negative"):
        sampler.sample((subject,))


def test_subject_output_order_is_canonical() -> None:
    source = FakePopulationRasterSource(((1.0, 2.0), (3.0, 4.0)))
    first = PopulationRasterSubject(
        subject_id="b",
        windows=(PopulationRasterWindow(0, 0, 1, 1),),
        working_srid=32637,
    )
    second = PopulationRasterSubject(
        subject_id="a",
        windows=(PopulationRasterWindow(1, 1, 1, 1),),
        working_srid=32637,
    )

    result = PopulationRasterSampler(source=source).sample((first, second))

    assert [item.subject_id for item in result.samples] == ["a", "b"]
    assert [item.sampled_population for item in result.samples] == [4.0, 1.0]
