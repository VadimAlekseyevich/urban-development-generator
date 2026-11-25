from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import geopandas as gpd
import pyogrio
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely import make_valid
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from backend.app.services.vector_inspection import VectorLayerInspection, VectorSource
from core.urban_generator.domain import DataError, WorkingCRS


class VectorNormalizationError(DataError):
    """Base data error for vector normalization failures."""


class MissingVectorCRSError(VectorNormalizationError):
    """Raised when a spatial layer has no usable source CRS."""


class VectorGeometryRejectedError(VectorNormalizationError):
    """Raised when configured reject policy encounters an unsupported geometry."""


class VectorBatchContractError(VectorNormalizationError):
    """Raised when a backend violates the bounded batch-read contract."""


class GeometryFamily(StrEnum):
    POINT = "point"
    LINE = "line"
    POLYGON = "polygon"


class EmptyGeometryPolicy(StrEnum):
    DROP = "drop"
    REJECT = "reject"


class GeometryMismatchPolicy(StrEnum):
    DROP = "drop"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class VectorNormalizationConfig:
    """Explicit normalization policy for one canonical vector layer."""

    geometry_family: GeometryFamily
    batch_size: int = 5000
    empty_geometry_policy: EmptyGeometryPolicy = EmptyGeometryPolicy.DROP
    mismatch_policy: GeometryMismatchPolicy = GeometryMismatchPolicy.DROP

    def __post_init__(self) -> None:
        if isinstance(self.batch_size, bool) or not isinstance(self.batch_size, int):
            raise ValueError("batch_size must be a positive integer")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")


@dataclass(frozen=True, slots=True)
class VectorBatchDiagnostics:
    """Counts describing deterministic normalization decisions for one input batch."""

    input_features: int
    output_features: int
    repaired_features: int
    dropped_empty_features: int
    dropped_type_features: int
    filtered_collection_features: int


@dataclass(frozen=True, slots=True)
class NormalizedVectorBatch:
    """One bounded, normalized batch ready for S03-T06 persistence."""

    layer_name: str
    start_feature: int
    source_crs: str
    working_srid: int
    frame: gpd.GeoDataFrame
    diagnostics: VectorBatchDiagnostics


class VectorBatchBackend(Protocol):
    """Adapter seam for bounded vector feature reads."""

    def read_batch(
        self,
        source: VectorSource,
        *,
        layer: str,
        skip_features: int,
        max_features: int,
    ) -> gpd.GeoDataFrame: ...


class PyogrioVectorBatchBackend:
    """Read bounded GeoDataFrame chunks through Pyogrio."""

    def read_batch(
        self,
        source: VectorSource,
        *,
        layer: str,
        skip_features: int,
        max_features: int,
    ) -> gpd.GeoDataFrame:
        frame = pyogrio.read_dataframe(
            source,
            layer=layer,
            skip_features=skip_features,
            max_features=max_features,
        )
        if not isinstance(frame, gpd.GeoDataFrame):
            raise VectorBatchContractError(
                f"layer {layer!r} is not spatial and cannot be vector-normalized"
            )
        return frame


class VectorNormalizer:
    """Normalize one inspected spatial layer in bounded batches.

    Features are read only up to the exact count produced by S03-T04. Every output
    geometry is represented in the validated metric ``WorkingCRS`` and is valid,
    non-empty, and compatible with the configured geometry family.
    """

    def __init__(self, *, backend: VectorBatchBackend | None = None) -> None:
        self._backend = backend or PyogrioVectorBatchBackend()

    def iter_normalized_batches(
        self,
        source: VectorSource,
        *,
        inspection: VectorLayerInspection,
        working_crs: WorkingCRS,
        config: VectorNormalizationConfig,
    ) -> Iterator[NormalizedVectorBatch]:
        if inspection.feature_count < 0:
            raise VectorBatchContractError("inspection feature_count must be non-negative")
        if inspection.geometry_type is None:
            raise VectorNormalizationError(
                f"layer {inspection.name!r} has no geometry and cannot be normalized"
            )
        source_crs = _parse_source_crs(inspection.crs, layer_name=inspection.name)
        target_crs = working_crs.crs

        total_read = 0
        for start in range(0, inspection.feature_count, config.batch_size):
            requested = min(config.batch_size, inspection.feature_count - start)
            frame = self._read_batch(
                source,
                layer_name=inspection.name,
                skip_features=start,
                max_features=requested,
            )
            observed = len(frame)
            if observed == 0:
                raise VectorBatchContractError(
                    f"layer {inspection.name!r} ended at feature {start}; "
                    f"inspection declared {inspection.feature_count} features"
                )
            if observed > requested:
                raise VectorBatchContractError(
                    f"layer {inspection.name!r} returned {observed} features "
                    f"for a {requested}-feature bounded read"
                )
            if start + observed < inspection.feature_count and observed != requested:
                raise VectorBatchContractError(
                    f"layer {inspection.name!r} returned a short non-final batch "
                    f"at feature {start}: {observed} != {requested}"
                )

            normalized, diagnostics = self._normalize_frame(
                frame,
                layer_name=inspection.name,
                source_crs=source_crs,
                target_crs=target_crs,
                config=config,
            )
            total_read += observed
            yield NormalizedVectorBatch(
                layer_name=inspection.name,
                start_feature=start,
                source_crs=source_crs.to_string(),
                working_srid=working_crs.srid,
                frame=normalized,
                diagnostics=diagnostics,
            )

        if total_read != inspection.feature_count:
            raise VectorBatchContractError(
                f"layer {inspection.name!r} normalized {total_read} features; "
                f"inspection declared {inspection.feature_count}"
            )

    def _read_batch(
        self,
        source: VectorSource,
        *,
        layer_name: str,
        skip_features: int,
        max_features: int,
    ) -> gpd.GeoDataFrame:
        try:
            frame = self._backend.read_batch(
                source,
                layer=layer_name,
                skip_features=skip_features,
                max_features=max_features,
            )
        except VectorNormalizationError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise VectorNormalizationError(
                f"unable to read vector layer {layer_name!r} at feature {skip_features}"
            ) from exc
        if not isinstance(frame, gpd.GeoDataFrame):
            raise VectorBatchContractError(
                f"backend returned non-spatial frame for layer {layer_name!r}"
            )
        return frame

    @staticmethod
    def _normalize_frame(
        frame: gpd.GeoDataFrame,
        *,
        layer_name: str,
        source_crs: CRS,
        target_crs: CRS,
        config: VectorNormalizationConfig,
    ) -> tuple[gpd.GeoDataFrame, VectorBatchDiagnostics]:
        if frame.crs is None:
            raise MissingVectorCRSError(f"batch for layer {layer_name!r} has no CRS")
        try:
            batch_crs = CRS.from_user_input(frame.crs)
        except CRSError as exc:
            raise MissingVectorCRSError(
                f"batch for layer {layer_name!r} has invalid CRS"
            ) from exc
        if batch_crs != source_crs:
            raise VectorBatchContractError(
                f"layer {layer_name!r} CRS changed between inspection and read: "
                f"{source_crs.to_string()} -> {batch_crs.to_string()}"
            )

        keep_positions: list[int] = []
        normalized_geometries: list[BaseGeometry] = []
        repaired = 0
        dropped_empty = 0
        dropped_type = 0
        filtered_collection = 0

        for position, geometry in enumerate(frame.geometry):
            if geometry is None or geometry.is_empty:
                if config.empty_geometry_policy is EmptyGeometryPolicy.REJECT:
                    raise VectorGeometryRejectedError(
                        f"layer {layer_name!r} contains null/empty geometry "
                        f"at batch position {position}"
                    )
                dropped_empty += 1
                continue

            normalized_geometry = geometry
            if not normalized_geometry.is_valid:
                normalized_geometry = make_valid(normalized_geometry)
                repaired += 1

            if normalized_geometry is None or normalized_geometry.is_empty:
                if config.empty_geometry_policy is EmptyGeometryPolicy.REJECT:
                    raise VectorGeometryRejectedError(
                        f"layer {layer_name!r} geometry became empty after make_valid "
                        f"at batch position {position}"
                    )
                dropped_empty += 1
                continue

            filtered, collection_filtered = _filter_geometry_family(
                normalized_geometry,
                config.geometry_family,
            )
            if filtered is None or filtered.is_empty:
                if config.mismatch_policy is GeometryMismatchPolicy.REJECT:
                    raise VectorGeometryRejectedError(
                        f"layer {layer_name!r} geometry type "
                        f"{normalized_geometry.geom_type!r} is incompatible with "
                        f"{config.geometry_family.value!r}"
                    )
                dropped_type += 1
                continue
            if collection_filtered:
                filtered_collection += 1
            if not filtered.is_valid:
                raise VectorNormalizationError(
                    f"layer {layer_name!r} remains invalid after make_valid/type filtering"
                )

            keep_positions.append(position)
            normalized_geometries.append(filtered)

        normalized = frame.iloc[keep_positions].copy()
        normalized.geometry = gpd.GeoSeries(
            normalized_geometries,
            index=normalized.index,
            crs=source_crs,
        )
        if source_crs != target_crs:
            normalized = normalized.to_crs(target_crs)
        else:
            normalized = normalized.set_crs(target_crs, allow_override=True)

        normalized.reset_index(drop=True, inplace=True)
        for geometry in normalized.geometry:
            if geometry is None or geometry.is_empty or not geometry.is_valid:
                raise VectorNormalizationError(
                    f"layer {layer_name!r} produced invalid/empty output geometry"
                )
            bounds = geometry.bounds
            if len(bounds) != 4 or not all(math.isfinite(float(value)) for value in bounds):
                raise VectorNormalizationError(
                    f"layer {layer_name!r} produced non-finite projected geometry"
                )

        diagnostics = VectorBatchDiagnostics(
            input_features=len(frame),
            output_features=len(normalized),
            repaired_features=repaired,
            dropped_empty_features=dropped_empty,
            dropped_type_features=dropped_type,
            filtered_collection_features=filtered_collection,
        )
        return normalized, diagnostics


def _parse_source_crs(raw_crs: str | None, *, layer_name: str) -> CRS:
    if raw_crs is None or not raw_crs.strip():
        raise MissingVectorCRSError(
            f"layer {layer_name!r} has no source CRS; normalization requires explicit CRS"
        )
    try:
        crs = CRS.from_user_input(raw_crs)
    except CRSError as exc:
        raise MissingVectorCRSError(
            f"layer {layer_name!r} has invalid source CRS: {raw_crs!r}"
        ) from exc
    if not (crs.is_geographic or crs.is_projected) or len(crs.axis_info) < 2:
        raise MissingVectorCRSError(
            f"layer {layer_name!r} source CRS is not a usable horizontal CRS"
        )
    return crs


def _filter_geometry_family(
    geometry: BaseGeometry,
    family: GeometryFamily,
) -> tuple[BaseGeometry | None, bool]:
    if _matches_family(geometry, family):
        return geometry, False
    if not isinstance(geometry, GeometryCollection):
        return None, False

    parts: list[BaseGeometry] = []
    for part in geometry.geoms:
        filtered, _nested = _filter_geometry_family(part, family)
        if filtered is None or filtered.is_empty:
            continue
        if isinstance(filtered, (MultiPoint, MultiLineString, MultiPolygon)):
            parts.extend(filtered.geoms)
        else:
            parts.append(filtered)

    if not parts:
        return None, True
    if family is GeometryFamily.POINT:
        typed_points = [part for part in parts if isinstance(part, Point)]
        return (
            typed_points[0] if len(typed_points) == 1 else MultiPoint(typed_points),
            True,
        )
    if family is GeometryFamily.LINE:
        typed_lines = [part for part in parts if isinstance(part, LineString)]
        return (
            typed_lines[0] if len(typed_lines) == 1 else MultiLineString(typed_lines),
            True,
        )
    typed_polygons = [part for part in parts if isinstance(part, Polygon)]
    return (
        typed_polygons[0] if len(typed_polygons) == 1 else MultiPolygon(typed_polygons),
        True,
    )


def _matches_family(geometry: BaseGeometry, family: GeometryFamily) -> bool:
    if family is GeometryFamily.POINT:
        return isinstance(geometry, (Point, MultiPoint))
    if family is GeometryFamily.LINE:
        return isinstance(geometry, (LineString, MultiLineString))
    return isinstance(geometry, (Polygon, MultiPolygon))
