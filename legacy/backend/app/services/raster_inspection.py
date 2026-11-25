from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import rasterio
from pyproj import CRS
from pyproj.exceptions import CRSError

RasterSource = str | Path
RasterTransform = tuple[float, float, float, float, float, float]
RasterResolution = tuple[float, float]
RasterExtent = tuple[float, float, float, float]


class RasterInspectionError(ValueError):
    """Raised when raster metadata is unavailable, inconsistent, or invalid."""


@dataclass(frozen=True, slots=True)
class RasterInspection:
    """Metadata-only description of one raster datasource.

    ``extent`` and ``resolution`` are expressed in ``crs`` coordinates when CRS is present.
    Inspection does not reproject, resample, or read raster band pixels.
    """

    driver: str
    width: int
    height: int
    band_count: int
    dtypes: tuple[str, ...]
    crs: str | None
    transform: RasterTransform
    nodata_values: tuple[float | None, ...]
    resolution: RasterResolution
    extent: RasterExtent


class RasterMetadataBackend(Protocol):
    """Small adapter seam around Rasterio/GDAL metadata access."""

    def read_metadata(self, source: RasterSource) -> Mapping[str, object]: ...


class RasterioMetadataBackend:
    """Read dataset-level metadata without invoking ``DatasetReader.read``."""

    def read_metadata(self, source: RasterSource) -> Mapping[str, object]:
        with rasterio.open(source) as dataset:
            transform = dataset.transform
            crs = None if dataset.crs is None else dataset.crs.to_string()
            return {
                "driver": dataset.driver,
                "width": dataset.width,
                "height": dataset.height,
                "band_count": dataset.count,
                "dtypes": tuple(dataset.dtypes),
                "crs": crs,
                "transform": (
                    transform.a,
                    transform.b,
                    transform.c,
                    transform.d,
                    transform.e,
                    transform.f,
                ),
                "nodata_values": tuple(dataset.nodatavals),
            }


class RasterInspector:
    """Inspect raster georeferencing and band metadata without loading pixel arrays."""

    def __init__(self, *, backend: RasterMetadataBackend | None = None) -> None:
        self._backend = backend or RasterioMetadataBackend()

    def inspect(self, source: RasterSource) -> RasterInspection:
        try:
            metadata = self._backend.read_metadata(source)
        except RasterInspectionError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise RasterInspectionError("unable to inspect raster datasource metadata") from exc

        driver = _required_text(metadata.get("driver"), field="driver")
        width = _positive_int(metadata.get("width"), field="width")
        height = _positive_int(metadata.get("height"), field="height")
        band_count = _positive_int(metadata.get("band_count"), field="band_count")
        dtypes = _dtypes(metadata.get("dtypes"), band_count=band_count)
        crs = _crs(metadata.get("crs"))
        transform = _transform(metadata.get("transform"))
        nodata_values = _nodata_values(
            metadata.get("nodata_values"),
            band_count=band_count,
        )
        resolution = _resolution(transform)
        extent = _extent(transform, width=width, height=height)

        return RasterInspection(
            driver=driver,
            width=width,
            height=height,
            band_count=band_count,
            dtypes=dtypes,
            crs=crs,
            transform=transform,
            nodata_values=nodata_values,
            resolution=resolution,
            extent=extent,
        )


def _required_text(value: object, *, field: str) -> str:
    if value is None:
        raise RasterInspectionError(f"raster metadata has no {field}")
    text = str(value).strip()
    if not text:
        raise RasterInspectionError(f"raster metadata has blank {field}")
    return text


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise RasterInspectionError(f"raster {field} must be a positive integer")
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise RasterInspectionError(f"raster {field} must be a positive integer") from exc
    if parsed <= 0:
        raise RasterInspectionError(f"raster {field} must be a positive integer")
    return parsed


def _dtypes(value: object, *, band_count: int) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or value is None:
        raise RasterInspectionError("raster dtypes metadata must be a per-band sequence")
    try:
        items = tuple(str(item).strip() for item in cast(Any, value))
    except TypeError as exc:
        raise RasterInspectionError("raster dtypes metadata must be a per-band sequence") from exc
    if len(items) != band_count or any(not item for item in items):
        raise RasterInspectionError("raster dtypes metadata does not match band_count")
    return items


def _crs(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        crs = CRS.from_user_input(text)
    except CRSError as exc:
        raise RasterInspectionError("raster CRS metadata is invalid") from exc
    return crs.to_string()


def _transform(value: object) -> RasterTransform:
    if isinstance(value, (str, bytes, bytearray)) or value is None:
        raise RasterInspectionError("raster transform must contain six numeric coefficients")
    try:
        items = tuple(float(item) for item in cast(Any, value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise RasterInspectionError(
            "raster transform must contain six numeric coefficients"
        ) from exc
    if len(items) != 6 or not all(math.isfinite(item) for item in items):
        raise RasterInspectionError("raster transform must contain six finite coefficients")
    transform = cast(RasterTransform, items)
    a, b, _c, d, e, _f = transform
    determinant = a * e - b * d
    if determinant == 0.0 or not math.isfinite(determinant):
        raise RasterInspectionError("raster transform is degenerate")
    return transform


def _nodata_values(value: object, *, band_count: int) -> tuple[float | None, ...]:
    if isinstance(value, (str, bytes, bytearray)) or value is None:
        raise RasterInspectionError("raster nodata metadata must be a per-band sequence")
    try:
        raw_items = tuple(cast(Any, value))
    except TypeError as exc:
        raise RasterInspectionError(
            "raster nodata metadata must be a per-band sequence"
        ) from exc
    if len(raw_items) != band_count:
        raise RasterInspectionError("raster nodata metadata does not match band_count")

    parsed: list[float | None] = []
    for item in raw_items:
        if item is None:
            parsed.append(None)
            continue
        try:
            nodata = float(item)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RasterInspectionError("raster nodata value must be numeric or null") from exc
        if math.isinf(nodata):
            raise RasterInspectionError("raster nodata value must not be infinite")
        parsed.append(nodata)
    return tuple(parsed)


def _resolution(transform: RasterTransform) -> RasterResolution:
    a, b, _c, d, e, _f = transform
    x_resolution = math.hypot(a, d)
    y_resolution = math.hypot(b, e)
    if (
        not math.isfinite(x_resolution)
        or not math.isfinite(y_resolution)
        or x_resolution <= 0.0
        or y_resolution <= 0.0
    ):
        raise RasterInspectionError("raster resolution must be finite and positive")
    return (x_resolution, y_resolution)


def _extent(
    transform: RasterTransform,
    *,
    width: int,
    height: int,
) -> RasterExtent:
    a, b, c, d, e, f = transform
    corners = (
        (c, f),
        (a * width + c, d * width + f),
        (b * height + c, e * height + f),
        (a * width + b * height + c, d * width + e * height + f),
    )
    xs = tuple(point[0] for point in corners)
    ys = tuple(point[1] for point in corners)
    extent = (min(xs), min(ys), max(xs), max(ys))
    if not all(math.isfinite(item) for item in extent):
        raise RasterInspectionError("raster extent contains non-finite coordinates")
    if extent[0] >= extent[2] or extent[1] >= extent[3]:
        raise RasterInspectionError("raster extent has zero or negative span")
    return extent
