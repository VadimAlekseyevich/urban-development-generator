from pathlib import Path
from typing import BinaryIO

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from backend.app.adapters import LocalArtifactStore
from backend.app.services.raster_normalization import (
    EmptyRasterClipError,
    MissingRasterCRSError,
    RasterNormalizationConfig,
    RasterNormalizationError,
    RasterNormalizer,
    RasterResampling,
)
from core.urban_generator.domain import ArtifactRef, ArtifactStore, WorkingCRS


def _write_ready_raster(
    store: LocalArtifactStore,
    *,
    key: str = "uploads/source/dem.tif",
    width: int = 64,
    height: int = 64,
    crs: str | None = "EPSG:32631",
    transform: object | None = None,
    nodata: float | None = -9999.0,
) -> ArtifactRef:
    transform = transform or from_origin(500_000.0, 1_000.0, 10.0, 10.0)
    values = np.arange(width * height, dtype=np.float32).reshape(height, width)
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype="float32",
            crs=crs,
            transform=transform,
            nodata=nodata,
        ) as dataset:
            dataset.write(values, 1)
        memory.seek(0)
        temporary = ArtifactRef(key=key)
        store.put(temporary, memory, content_type="image/tiff")
        return store.promote(temporary).ref


def _read_ready_raster(store: ArtifactStore, ref: ArtifactRef) -> tuple[bytes, dict[str, object]]:
    with store.open(ref) as stream:
        payload = stream.read()
    with MemoryFile(payload) as memory:
        with memory.open() as dataset:
            metadata: dict[str, object] = {
                "crs": dataset.crs.to_epsg() if dataset.crs is not None else None,
                "width": dataset.width,
                "height": dataset.height,
                "count": dataset.count,
                "transform": dataset.transform,
                "nodata": dataset.nodata,
                "profile": dict(dataset.profile),
                "tags": dataset.tags(),
            }
    return payload, metadata


def test_normalizer_clips_resamples_and_writes_cog_friendly_geotiff(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts", chunk_size=11)
    source_ref = _write_ready_raster(store)
    output_ref = ArtifactRef(key="normalized/dem-20m.tif")

    result = RasterNormalizer(spool_chunk_bytes=13).normalize(
        store,
        source_ref=source_ref,
        output_ref=output_ref,
        working_crs=WorkingCRS(32631),
        config=RasterNormalizationConfig(
            target_resolution_m=20.0,
            clip_bounds=(500_080.0, 440.0, 500_560.0, 920.0),
            resampling=RasterResampling.BILINEAR,
            tile_size=16,
        ),
    )

    assert result.stat.ref == output_ref.as_ready()
    assert result.stat.content_type == "image/tiff"
    assert result.width == 24
    assert result.height == 24
    assert result.band_count == 1
    assert result.dtype == "float32"
    assert result.nodata == -9999.0
    assert result.resolution == pytest.approx((20.0, 20.0))
    assert result.extent == pytest.approx((500_080.0, 440.0, 500_560.0, 920.0))
    assert result.windows_written == 4
    assert result.max_window_pixels <= 16 * 16

    _payload, metadata = _read_ready_raster(store, result.stat.ref)
    assert metadata["crs"] == 32631
    assert metadata["width"] == 24
    assert metadata["height"] == 24
    profile = metadata["profile"]
    assert isinstance(profile, dict)
    assert profile["tiled"] is True
    assert str(profile["compress"]).lower() == "deflate"
    tags = metadata["tags"]
    assert isinstance(tags, dict)
    assert tags["NORMALIZATION_STAGE"] == "S03-T08"
    assert tags["RESAMPLING"] == "bilinear"
    with pytest.raises(KeyError):
        store.stat(output_ref)


def test_normalizer_reprojects_to_working_crs(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    source_ref = _write_ready_raster(
        store,
        width=16,
        height=16,
        crs="EPSG:4326",
        transform=from_origin(-0.08, 0.08, 0.01, 0.01),
    )

    result = RasterNormalizer().normalize(
        store,
        source_ref=source_ref,
        output_ref=ArtifactRef(key="normalized/reprojected.tif"),
        working_crs=WorkingCRS(3857),
        config=RasterNormalizationConfig(
            target_resolution_m=1_000.0,
            tile_size=16,
        ),
    )

    assert result.source_crs == "EPSG:4326"
    assert result.working_srid == 3857
    assert result.resolution == pytest.approx((1_000.0, 1_000.0))
    assert result.width > 0
    assert result.height > 0
    assert result.max_window_pixels <= 16 * 16
    _payload, metadata = _read_ready_raster(store, result.stat.ref)
    assert metadata["crs"] == 3857


def test_normalizer_spools_source_in_bounded_reads(tmp_path: Path) -> None:
    local = LocalArtifactStore(tmp_path / "artifacts")
    source_ref = _write_ready_raster(local, width=16, height=16)
    guarded = _GuardedStore(local, max_read_size=7)

    RasterNormalizer(spool_chunk_bytes=7).normalize(
        guarded,
        source_ref=source_ref,
        output_ref=ArtifactRef(key="normalized/bounded.tif"),
        working_crs=WorkingCRS(32631),
        config=RasterNormalizationConfig(tile_size=16),
    )

    assert guarded.max_observed_read <= 7
    assert guarded.read_calls > 1


def test_normalizer_rejects_missing_crs_and_cleans_output(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    source_ref = _write_ready_raster(store, crs=None)
    output_ref = ArtifactRef(key="normalized/missing-crs.tif")

    with pytest.raises(MissingRasterCRSError, match="no CRS"):
        RasterNormalizer().normalize(
            store,
            source_ref=source_ref,
            output_ref=output_ref,
            working_crs=WorkingCRS(32631),
            config=RasterNormalizationConfig(tile_size=16),
        )

    with pytest.raises(KeyError):
        store.stat(output_ref)
    with pytest.raises(KeyError):
        store.stat(output_ref.as_ready())


def test_normalizer_rejects_non_intersecting_clip_before_output(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    source_ref = _write_ready_raster(store)
    output_ref = ArtifactRef(key="normalized/no-overlap.tif")

    with pytest.raises(EmptyRasterClipError, match="do not intersect"):
        RasterNormalizer().normalize(
            store,
            source_ref=source_ref,
            output_ref=output_ref,
            working_crs=WorkingCRS(32631),
            config=RasterNormalizationConfig(
                clip_bounds=(900_000.0, 900_000.0, 901_000.0, 901_000.0),
                tile_size=16,
            ),
        )

    with pytest.raises(KeyError):
        store.stat(output_ref.as_ready())


def test_normalizer_bounds_total_output_samples(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    source_ref = _write_ready_raster(store, width=32, height=32)
    output_ref = ArtifactRef(key="normalized/too-many-samples.tif")

    with pytest.raises(RasterNormalizationError, match="limit is 100"):
        RasterNormalizer().normalize(
            store,
            source_ref=source_ref,
            output_ref=output_ref,
            working_crs=WorkingCRS(32631),
            config=RasterNormalizationConfig(
                tile_size=16,
                max_output_samples=100,
            ),
        )

    with pytest.raises(KeyError):
        store.stat(output_ref.as_ready())


def test_normalizer_compensates_when_promotion_fails(tmp_path: Path) -> None:
    local = LocalArtifactStore(tmp_path / "artifacts")
    source_ref = _write_ready_raster(local, width=16, height=16)
    store = _FailingPromoteStore(local)
    output_ref = ArtifactRef(key="normalized/promote-failure.tif")

    with pytest.raises(RuntimeError, match="promotion unavailable"):
        RasterNormalizer().normalize(
            store,
            source_ref=source_ref,
            output_ref=output_ref,
            working_crs=WorkingCRS(32631),
            config=RasterNormalizationConfig(tile_size=16),
        )

    with pytest.raises(KeyError):
        local.stat(output_ref)
    with pytest.raises(KeyError):
        local.stat(output_ref.as_ready())


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"target_resolution_m": 0.0}, "target_resolution_m"),
        ({"clip_bounds": (1.0, 0.0, 1.0, 2.0)}, "clip_bounds"),
        ({"tile_size": 17}, "multiple of 16"),
        ({"max_output_samples": 0}, "max_output_samples"),
        ({"resampling": "nearest"}, "RasterResampling"),
    ],
)
def test_normalization_config_rejects_invalid_policy(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RasterNormalizationConfig(**kwargs)  # type: ignore[arg-type]


class _GuardedSource:
    def __init__(self, source: BinaryIO, *, max_read_size: int, owner: "_GuardedStore") -> None:
        self._source = source
        self._max_read_size = max_read_size
        self._owner = owner

    def read(self, size: int = -1) -> bytes:
        assert 0 < size <= self._max_read_size
        self._owner.max_observed_read = max(self._owner.max_observed_read, size)
        self._owner.read_calls += 1
        return self._source.read(size)

    def close(self) -> None:
        self._source.close()

    def __enter__(self) -> "_GuardedSource":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


class _GuardedStore:
    def __init__(self, inner: LocalArtifactStore, *, max_read_size: int) -> None:
        self._inner = inner
        self._max_read_size = max_read_size
        self.max_observed_read = 0
        self.read_calls = 0

    def put(self, *args: object, **kwargs: object) -> object:
        return self._inner.put(*args, **kwargs)  # type: ignore[arg-type]

    def open(self, ref: ArtifactRef) -> BinaryIO:
        source = self._inner.open(ref)
        return _GuardedSource(  # type: ignore[return-value]
            source,
            max_read_size=self._max_read_size,
            owner=self,
        )

    def stat(self, ref: ArtifactRef) -> object:
        return self._inner.stat(ref)

    def delete(self, ref: ArtifactRef) -> None:
        self._inner.delete(ref)

    def promote(self, ref: ArtifactRef) -> object:
        return self._inner.promote(ref)


class _FailingPromoteStore:
    def __init__(self, inner: LocalArtifactStore) -> None:
        self._inner = inner

    def put(self, *args: object, **kwargs: object) -> object:
        return self._inner.put(*args, **kwargs)  # type: ignore[arg-type]

    def open(self, ref: ArtifactRef) -> BinaryIO:
        return self._inner.open(ref)

    def stat(self, ref: ArtifactRef) -> object:
        return self._inner.stat(ref)

    def delete(self, ref: ArtifactRef) -> None:
        self._inner.delete(ref)

    def promote(self, ref: ArtifactRef) -> object:
        if ref.key.startswith("normalized/"):
            raise RuntimeError("promotion unavailable")
        return self._inner.promote(ref)
