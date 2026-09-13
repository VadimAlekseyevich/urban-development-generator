from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

import rasterio
from pyproj import CRS
from pyproj.exceptions import CRSError
from rasterio.enums import Resampling
from rasterio.errors import RasterioError
from rasterio.io import DatasetReader
from rasterio.transform import Affine, array_bounds, from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform, transform_bounds

from core.urban_generator.domain import (
    ArtifactRef,
    ArtifactStat,
    ArtifactState,
    ArtifactStore,
    DataError,
    WorkingCRS,
    require_temporary_artifact_ref,
)

RasterClipBounds = tuple[float, float, float, float]
RasterResolution = tuple[float, float]
RasterExtent = tuple[float, float, float, float]

_DEFAULT_SPOOL_CHUNK_BYTES: Final = 1024 * 1024
_DEFAULT_TILE_SIZE: Final = 512
_MAX_TILE_SIZE: Final = 4096
_DEFAULT_MAX_OUTPUT_SAMPLES: Final = 100_000_000
_CONTENT_TYPE: Final = "image/tiff"


class RasterNormalizationError(DataError):
    """Base data error for windowed raster normalization failures."""


class MissingRasterCRSError(RasterNormalizationError):
    """Raised when a source raster has no usable CRS."""


class EmptyRasterClipError(RasterNormalizationError):
    """Raised when clip bounds do not intersect source coverage in target CRS."""


class RasterBandContractError(RasterNormalizationError):
    """Raised when band metadata cannot be represented safely in one GeoTIFF."""


class RasterResampling(StrEnum):
    NEAREST = "nearest"
    BILINEAR = "bilinear"
    CUBIC = "cubic"
    AVERAGE = "average"


_RESAMPLING_METHODS: dict[RasterResampling, Resampling] = {
    RasterResampling.NEAREST: Resampling.nearest,
    RasterResampling.BILINEAR: Resampling.bilinear,
    RasterResampling.CUBIC: Resampling.cubic,
    RasterResampling.AVERAGE: Resampling.average,
}


@dataclass(frozen=True, slots=True)
class RasterNormalizationConfig:
    """Explicit grid/window policy for one normalized raster artifact.

    ``clip_bounds`` are expressed in the target ``WorkingCRS``. When a clip does not
    align exactly to the output grid, the resulting extent is expanded by at most one
    pixel on the right/bottom so every requested source cell remains covered.
    """

    target_resolution_m: float | None = None
    clip_bounds: RasterClipBounds | None = None
    resampling: RasterResampling = RasterResampling.NEAREST
    tile_size: int = _DEFAULT_TILE_SIZE
    max_output_samples: int = _DEFAULT_MAX_OUTPUT_SAMPLES

    def __post_init__(self) -> None:
        if self.target_resolution_m is not None:
            if isinstance(self.target_resolution_m, bool):
                raise ValueError("target_resolution_m must be a positive finite number")
            resolution = float(self.target_resolution_m)
            if not math.isfinite(resolution) or resolution <= 0:
                raise ValueError("target_resolution_m must be a positive finite number")
        if self.clip_bounds is not None:
            _validate_bounds(self.clip_bounds, label="clip_bounds")
        if isinstance(self.tile_size, bool) or not isinstance(self.tile_size, int):
            raise ValueError("tile_size must be an integer")
        if self.tile_size < 16 or self.tile_size > _MAX_TILE_SIZE:
            raise ValueError(f"tile_size must be between 16 and {_MAX_TILE_SIZE}")
        if self.tile_size % 16 != 0:
            raise ValueError("tile_size must be a multiple of 16")
        if (
            isinstance(self.max_output_samples, bool)
            or not isinstance(self.max_output_samples, int)
            or self.max_output_samples <= 0
        ):
            raise ValueError("max_output_samples must be a positive integer")
        if not isinstance(self.resampling, RasterResampling):
            raise ValueError("resampling must be a RasterResampling value")


@dataclass(frozen=True, slots=True)
class NormalizedRasterArtifact:
    """Ready GeoTIFF artifact plus deterministic normalization diagnostics."""

    stat: ArtifactStat
    source_crs: str
    working_srid: int
    width: int
    height: int
    band_count: int
    dtype: str
    nodata: float | None
    resolution: RasterResolution
    extent: RasterExtent
    resampling: RasterResampling
    windows_written: int
    max_window_pixels: int
    source_size_bytes: int


@dataclass(frozen=True, slots=True)
class _NormalizedFile:
    source_crs: str
    width: int
    height: int
    band_count: int
    dtype: str
    nodata: float | None
    resolution: RasterResolution
    extent: RasterExtent
    windows_written: int
    max_window_pixels: int


class RasterNormalizer:
    """Normalize one immutable raster artifact using bounded Rasterio warp windows.

    Input blobs are spooled to disk in fixed-size chunks because Rasterio/GDAL require
    random access. Reprojection never materializes the complete raster as a NumPy array:
    one destination block and one band are read/written at a time.
    """

    def __init__(self, *, spool_chunk_bytes: int = _DEFAULT_SPOOL_CHUNK_BYTES) -> None:
        if (
            isinstance(spool_chunk_bytes, bool)
            or not isinstance(spool_chunk_bytes, int)
            or spool_chunk_bytes <= 0
        ):
            raise ValueError("spool_chunk_bytes must be a positive integer")
        self._spool_chunk_bytes = spool_chunk_bytes

    def normalize(
        self,
        store: ArtifactStore,
        *,
        source_ref: ArtifactRef,
        output_ref: ArtifactRef,
        working_crs: WorkingCRS,
        config: RasterNormalizationConfig,
    ) -> NormalizedRasterArtifact:
        if source_ref.state is not ArtifactState.READY:
            raise RasterNormalizationError("source raster artifact must be ready/immutable")
        require_temporary_artifact_ref(output_ref)
        if source_ref.key == output_ref.key:
            raise RasterNormalizationError("source and output artifact keys must differ")
        if not isinstance(working_crs, WorkingCRS):
            raise TypeError("working_crs must be a WorkingCRS")
        if not isinstance(config, RasterNormalizationConfig):
            raise TypeError("config must be a RasterNormalizationConfig")

        source_stat = store.stat(source_ref)
        ready_output_ref = output_ref.as_ready()
        try:
            with tempfile.TemporaryDirectory(prefix="urban-raster-normalize-") as temp_dir:
                root = Path(temp_dir)
                source_path = root / "source-raster"
                output_path = root / "normalized.tif"
                self._spool_source(
                    store,
                    source_ref=source_ref,
                    destination=source_path,
                    expected_size=source_stat.size_bytes,
                )
                normalized = self._normalize_file(
                    source_path,
                    output_path,
                    working_crs=working_crs,
                    config=config,
                )
                with output_path.open("rb") as output:
                    store.put(output_ref, output, content_type=_CONTENT_TYPE)
                ready_stat = store.promote(output_ref)
        except Exception:
            self._best_effort_delete(store, output_ref)
            self._best_effort_delete(store, ready_output_ref)
            raise

        return NormalizedRasterArtifact(
            stat=ready_stat,
            source_crs=normalized.source_crs,
            working_srid=working_crs.srid,
            width=normalized.width,
            height=normalized.height,
            band_count=normalized.band_count,
            dtype=normalized.dtype,
            nodata=normalized.nodata,
            resolution=normalized.resolution,
            extent=normalized.extent,
            resampling=config.resampling,
            windows_written=normalized.windows_written,
            max_window_pixels=normalized.max_window_pixels,
            source_size_bytes=source_stat.size_bytes,
        )

    def _spool_source(
        self,
        store: ArtifactStore,
        *,
        source_ref: ArtifactRef,
        destination: Path,
        expected_size: int,
    ) -> None:
        observed_size = 0
        with store.open(source_ref) as source, destination.open("wb") as target:
            while True:
                chunk = source.read(self._spool_chunk_bytes)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise TypeError("artifact source must yield bytes")
                observed_size += len(chunk)
                if observed_size > expected_size:
                    raise RasterNormalizationError(
                        "source artifact stream exceeds its declared size"
                    )
                target.write(chunk)
        if observed_size != expected_size:
            raise RasterNormalizationError(
                "source artifact stream size does not match artifact metadata"
            )

    def _normalize_file(
        self,
        source_path: Path,
        output_path: Path,
        *,
        working_crs: WorkingCRS,
        config: RasterNormalizationConfig,
    ) -> _NormalizedFile:
        try:
            with rasterio.open(source_path) as source:
                source_crs = _source_crs(source.crs)
                band_count = int(source.count)
                if band_count <= 0:
                    raise RasterBandContractError("source raster must contain at least one band")
                dtype = _uniform_dtype(
                    tuple(str(value) for value in source.dtypes),
                    band_count=band_count,
                )
                nodata = _uniform_nodata(
                    tuple(source.nodatavals),
                    band_count=band_count,
                )

                source_crs_text = source_crs.to_string()
                target_crs = working_crs.authority
                transform, width, height = _target_grid(
                    source,
                    source_crs=source_crs_text,
                    target_crs=target_crs,
                    config=config,
                )
                resolution = (abs(float(transform.a)), abs(float(transform.e)))
                if (
                    not all(math.isfinite(value) and value > 0 for value in resolution)
                    or width <= 0
                    or height <= 0
                ):
                    raise RasterNormalizationError("target raster grid is invalid")
                output_samples = width * height * band_count
                if output_samples > config.max_output_samples:
                    raise RasterNormalizationError(
                        f"normalized raster would contain {output_samples} samples; "
                        f"limit is {config.max_output_samples}"
                    )

                with rasterio.open(
                    output_path,
                    "w",
                    driver="GTiff",
                    width=width,
                    height=height,
                    count=band_count,
                    dtype=dtype,
                    crs=target_crs,
                    transform=transform,
                    nodata=nodata,
                    tiled=True,
                    blockxsize=config.tile_size,
                    blockysize=config.tile_size,
                    compress="DEFLATE",
                    BIGTIFF="IF_SAFER",
                ) as target:
                    windows_written = 0
                    max_window_pixels = 0
                    with WarpedVRT(
                        source,
                        crs=target_crs,
                        transform=transform,
                        width=width,
                        height=height,
                        resampling=_RESAMPLING_METHODS[config.resampling],
                        src_nodata=nodata,
                        nodata=nodata,
                        init_dest_nodata=True,
                    ) as warped:
                        for _block_index, window in target.block_windows(1):
                            window_pixels = int(window.width * window.height)
                            if window_pixels > config.tile_size * config.tile_size:
                                raise RasterNormalizationError(
                                    "Rasterio emitted a block larger than configured tile_size"
                                )
                            max_window_pixels = max(max_window_pixels, window_pixels)
                            for band_index in range(1, band_count + 1):
                                data = warped.read(band_index, window=window)
                                if int(data.size) != window_pixels:
                                    raise RasterNormalizationError(
                                        "warped raster window size is inconsistent"
                                    )
                                target.write(data, band_index, window=window)
                                windows_written += 1
                    target.update_tags(
                        NORMALIZATION_STAGE="S03-T08",
                        SOURCE_CRS=source_crs_text,
                        TARGET_CRS=working_crs.authority,
                        RESAMPLING=config.resampling.value,
                    )

                west, south, east, north = array_bounds(height, width, transform)
                extent = (float(west), float(south), float(east), float(north))
                _validate_bounds(extent, label="normalized extent")
                return _NormalizedFile(
                    source_crs=source_crs_text,
                    width=width,
                    height=height,
                    band_count=band_count,
                    dtype=dtype,
                    nodata=nodata,
                    resolution=resolution,
                    extent=extent,
                    windows_written=windows_written,
                    max_window_pixels=max_window_pixels,
                )
        except RasterNormalizationError:
            raise
        except (RasterioError, CRSError, OSError, ValueError) as exc:
            raise RasterNormalizationError("unable to normalize raster datasource") from exc

    @staticmethod
    def _best_effort_delete(store: ArtifactStore, ref: ArtifactRef) -> None:
        try:
            store.delete(ref)
        except Exception:
            pass


def _source_crs(value: object) -> CRS:
    if value is None:
        raise MissingRasterCRSError("source raster has no CRS")
    try:
        crs = CRS.from_user_input(value)
    except (CRSError, TypeError, ValueError) as exc:
        raise MissingRasterCRSError("source raster CRS is invalid") from exc
    if crs.is_bound:
        crs = crs.source_crs
    return crs


def _uniform_dtype(dtypes: tuple[str, ...], *, band_count: int) -> str:
    if len(dtypes) != band_count:
        raise RasterBandContractError("source raster band dtype metadata is incomplete")
    if any(not dtype.strip() for dtype in dtypes):
        raise RasterBandContractError("source raster contains a blank band dtype")
    if len(set(dtypes)) != 1:
        raise RasterBandContractError(
            "mixed band dtypes are not supported by the normalized GeoTIFF contract"
        )
    return dtypes[0]


def _uniform_nodata(values: tuple[object, ...], *, band_count: int) -> float | None:
    if len(values) != band_count:
        raise RasterBandContractError("source raster band nodata metadata is incomplete")

    normalized: list[float | None] = []
    for value in values:
        if value is None:
            normalized.append(None)
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RasterBandContractError("source raster nodata value is invalid") from exc
        if math.isinf(number):
            raise RasterBandContractError("source raster nodata value must not be infinite")
        normalized.append(number)

    first = normalized[0]
    if any(not _same_nodata(first, value) for value in normalized[1:]):
        raise RasterBandContractError(
            "mixed per-band nodata values are not supported by the normalized GeoTIFF contract"
        )
    return first


def _same_nodata(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    if math.isnan(left) and math.isnan(right):
        return True
    return left == right


def _target_grid(
    source: DatasetReader,
    *,
    source_crs: str,
    target_crs: str,
    config: RasterNormalizationConfig,
) -> tuple[Affine, int, int]:
    bounds = source.bounds
    source_bounds = (
        float(bounds.left),
        float(bounds.bottom),
        float(bounds.right),
        float(bounds.top),
    )
    _validate_bounds(source_bounds, label="source raster bounds")

    resolution: float | tuple[float, float] | None = config.target_resolution_m
    default_transform, default_width, default_height = calculate_default_transform(
        source_crs,
        target_crs,
        int(source.width),
        int(source.height),
        *source_bounds,
        resolution=resolution,
    )
    if config.clip_bounds is None:
        return default_transform, int(default_width), int(default_height)

    transformed_source_bounds = transform_bounds(
        source_crs,
        target_crs,
        *source_bounds,
        densify_pts=21,
    )
    target_bounds = _intersection(
        tuple(float(value) for value in transformed_source_bounds),
        config.clip_bounds,
    )
    if target_bounds is None:
        raise EmptyRasterClipError(
            "clip_bounds do not intersect source raster coverage in target CRS"
        )

    x_resolution = abs(float(default_transform.a))
    y_resolution = abs(float(default_transform.e))
    if not (
        math.isfinite(x_resolution)
        and x_resolution > 0
        and math.isfinite(y_resolution)
        and y_resolution > 0
    ):
        raise RasterNormalizationError("target raster resolution is invalid")

    left, bottom, right, top = target_bounds
    width = max(1, math.ceil((right - left) / x_resolution))
    height = max(1, math.ceil((top - bottom) / y_resolution))
    return from_origin(left, top, x_resolution, y_resolution), width, height


def _intersection(
    left_bounds: RasterClipBounds,
    right_bounds: RasterClipBounds,
) -> RasterClipBounds | None:
    left = max(left_bounds[0], right_bounds[0])
    bottom = max(left_bounds[1], right_bounds[1])
    right = min(left_bounds[2], right_bounds[2])
    top = min(left_bounds[3], right_bounds[3])
    if left >= right or bottom >= top:
        return None
    return (left, bottom, right, top)


def _validate_bounds(bounds: RasterClipBounds, *, label: str) -> None:
    if len(bounds) != 4:
        raise ValueError(f"{label} must contain four values")
    try:
        left, bottom, right, top = (float(value) for value in bounds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must contain finite numeric values") from exc
    if not all(math.isfinite(value) for value in (left, bottom, right, top)):
        raise ValueError(f"{label} must contain finite numeric values")
    if left >= right or bottom >= top:
        raise ValueError(f"{label} must satisfy left < right and bottom < top")
