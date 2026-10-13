"""S14 streaming raster fingerprints preserve every byte of the v1 format."""

from __future__ import annotations

import numpy as np
import pytest

from core.urban_generator.domain import (
    StageContractError,
    StageFingerprintWriter,
    build_stage_fingerprint,
)
from core.urban_generator.stages.suitability import _write_raster_fingerprint


@pytest.mark.parametrize("shape", [(2, 3), (37, 53), (1, 8193)])
@pytest.mark.parametrize("dtype", [np.float64, np.float32, np.bool_])
def test_streaming_raster_fingerprint_is_byte_exact(
    shape: tuple[int, int], dtype: type,
) -> None:
    data = np.arange(shape[0] * shape[1], dtype=np.int64).reshape(shape)
    if dtype is np.bool_:
        raster = (data % 3 == 0)
    else:
        raster = data.astype(dtype) / 11
    writer = StageFingerprintWriter()
    writer.add_text("suitability")
    _write_raster_fingerprint(writer, raster)
    writer.add_text("v1")
    assert writer.finalize() == build_stage_fingerprint(
        "suitability", raster.tobytes(order="C"), "v1"
    )


def test_streamed_non_contiguous_grid_preserves_c_order() -> None:
    matrix = np.arange(512 * 61, dtype=np.float64).reshape((512, 61))[::3, ::2]
    assert not matrix.flags.c_contiguous
    writer = StageFingerprintWriter()
    _write_raster_fingerprint(writer, matrix)
    assert writer.finalize() == build_stage_fingerprint(matrix.tobytes(order="C"))


@pytest.mark.parametrize("length,parts", [
    (5, [b"12", b"34"]),
    (3, [b"1234"]),
    (2, [b"a", "b"]),
])
def test_streamed_length_and_type_are_strict(length: int, parts: list[object]) -> None:
    writer = StageFingerprintWriter()
    with pytest.raises(StageContractError):
        writer.add_binary_chunks(byte_length=length, chunks=parts)  # type: ignore[arg-type]


def test_streamed_multiple_binary_parts_do_not_concatenate_ambiguously() -> None:
    writer = StageFingerprintWriter()
    writer.add_text("a")
    writer.add_binary_chunks(byte_length=3, chunks=(b"1", b"2", b"3"))
    writer.add_binary_chunks(byte_length=0, chunks=())
    assert writer.finalize() == build_stage_fingerprint("a", b"123", b"")
    assert writer.finalize() != build_stage_fingerprint("a", b"123")


def test_no_parts_and_invalid_declared_length_raise() -> None:
    with pytest.raises(StageContractError):
        StageFingerprintWriter().finalize()
    writer = StageFingerprintWriter()
    with pytest.raises(StageContractError):
        writer.add_binary_chunks(byte_length=-1, chunks=())
    with pytest.raises(StageContractError):
        writer.add_binary_chunks(byte_length=True, chunks=())
