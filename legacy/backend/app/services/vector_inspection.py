import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import pyogrio

VectorSource = str | Path
VectorBounds = tuple[float, float, float, float]
_DEFAULT_MAX_LAYERS = 128


class VectorInspectionError(ValueError):
    """Base error for vector datasource metadata inspection failures."""


class VectorLayerLimitError(VectorInspectionError):
    """Raised before per-layer inspection when a datasource exposes too many layers."""

    def __init__(self, *, max_layers: int, observed_layers: int) -> None:
        self.max_layers = max_layers
        self.observed_layers = observed_layers
        super().__init__(
            f"vector datasource exposes {observed_layers} layers; limit is {max_layers}"
        )


class IncompleteVectorMetadataError(VectorInspectionError):
    """Raised when exact metadata remains unavailable after the bounded fallback pass."""


@dataclass(frozen=True, slots=True)
class VectorLayerInspection:
    """Metadata-only inspection of one OGR layer.

    ``bbox`` is expressed in ``crs`` coordinates. No reprojection or geometry repair is
    performed by this capability.
    """

    name: str
    driver: str
    geometry_type: str | None
    crs: str | None
    feature_count: int
    bbox: VectorBounds | None
    encoding: str | None
    feature_count_forced: bool
    bbox_forced: bool


@dataclass(frozen=True, slots=True)
class VectorDatasetInspection:
    """Deterministically ordered metadata for one vector datasource."""

    driver: str
    layers: tuple[VectorLayerInspection, ...]


class VectorMetadataBackend(Protocol):
    """Small adapter seam around Pyogrio/OGR for deterministic unit tests."""

    def list_layers(self, source: VectorSource) -> tuple[tuple[str, str | None], ...]: ...

    def read_info(
        self,
        source: VectorSource,
        *,
        layer: str,
        force_feature_count: bool,
        force_total_bounds: bool,
    ) -> Mapping[str, object]: ...


class PyogrioMetadataBackend:
    """Pyogrio implementation that only exposes datasource/layer metadata calls."""

    def list_layers(self, source: VectorSource) -> tuple[tuple[str, str | None], ...]:
        rows = pyogrio.list_layers(source)
        layers: list[tuple[str, str | None]] = []
        for row in rows:
            name = str(row[0]).strip()
            geometry_type = None if row[1] is None else str(row[1]).strip() or None
            layers.append((name, geometry_type))
        return tuple(layers)

    def read_info(
        self,
        source: VectorSource,
        *,
        layer: str,
        force_feature_count: bool,
        force_total_bounds: bool,
    ) -> Mapping[str, object]:
        info = pyogrio.read_info(
            source,
            layer=layer,
            force_feature_count=force_feature_count,
            force_total_bounds=force_total_bounds,
        )
        return cast(Mapping[str, object], info)


class VectorInspector:
    """Inspect vector datasource metadata without loading full feature geometries.

    Pyogrio/OGR metadata is requested first. Drivers that report unknown feature count or
    extent get at most one forced metadata pass for that layer. ``max_layers`` bounds the
    number of potentially expensive fallback passes.
    """

    def __init__(
        self,
        *,
        backend: VectorMetadataBackend | None = None,
        max_layers: int = _DEFAULT_MAX_LAYERS,
    ) -> None:
        if isinstance(max_layers, bool) or not isinstance(max_layers, int) or max_layers <= 0:
            raise ValueError("max_layers must be a positive integer")
        self._backend = backend or PyogrioMetadataBackend()
        self._max_layers = max_layers

    def inspect(self, source: VectorSource) -> VectorDatasetInspection:
        layers = self._list_layers(source)
        if not layers:
            raise VectorInspectionError("vector datasource contains no layers")
        if len(layers) > self._max_layers:
            raise VectorLayerLimitError(
                max_layers=self._max_layers,
                observed_layers=len(layers),
            )

        inspected = tuple(
            self._inspect_layer(
                source,
                layer_name=layer_name,
                advertised_geometry_type=advertised_geometry_type,
            )
            for layer_name, advertised_geometry_type in layers
        )
        driver = inspected[0].driver
        if any(layer.driver != driver for layer in inspected[1:]):
            raise VectorInspectionError("OGR reported inconsistent drivers across layers")
        return VectorDatasetInspection(driver=driver, layers=inspected)

    def _list_layers(self, source: VectorSource) -> tuple[tuple[str, str | None], ...]:
        try:
            layers = self._backend.list_layers(source)
        except (OSError, RuntimeError, ValueError) as exc:
            raise VectorInspectionError("unable to list vector datasource layers") from exc

        seen: set[str] = set()
        for name, _geometry_type in layers:
            if not name.strip():
                raise VectorInspectionError("OGR returned a blank layer name")
            if name in seen:
                raise VectorInspectionError(f"OGR returned duplicate layer name: {name!r}")
            seen.add(name)
        return layers

    def _inspect_layer(
        self,
        source: VectorSource,
        *,
        layer_name: str,
        advertised_geometry_type: str | None,
    ) -> VectorLayerInspection:
        info = self._read_info(
            source,
            layer_name=layer_name,
            force_feature_count=False,
            force_total_bounds=False,
        )
        metadata = self._parse_layer_metadata(
            info,
            layer_name=layer_name,
            advertised_geometry_type=advertised_geometry_type,
        )

        needs_feature_count = metadata.feature_count is None
        needs_bbox = (
            metadata.geometry_type is not None
            and metadata.feature_count != 0
            and metadata.bbox is None
        )
        if needs_feature_count or needs_bbox:
            info = self._read_info(
                source,
                layer_name=layer_name,
                force_feature_count=needs_feature_count,
                force_total_bounds=needs_bbox,
            )
            metadata = self._parse_layer_metadata(
                info,
                layer_name=layer_name,
                advertised_geometry_type=advertised_geometry_type,
            )

        if metadata.feature_count is None:
            raise IncompleteVectorMetadataError(
                f"feature count is unavailable for layer {layer_name!r} after forced metadata pass"
            )
        if (
            metadata.geometry_type is not None
            and metadata.feature_count > 0
            and metadata.bbox is None
        ):
            raise IncompleteVectorMetadataError(
                f"bbox is unavailable for non-empty spatial layer {layer_name!r} "
                "after forced metadata pass"
            )

        return VectorLayerInspection(
            name=layer_name,
            driver=metadata.driver,
            geometry_type=metadata.geometry_type,
            crs=metadata.crs,
            feature_count=metadata.feature_count,
            bbox=metadata.bbox,
            encoding=metadata.encoding,
            feature_count_forced=needs_feature_count,
            bbox_forced=needs_bbox,
        )

    def _read_info(
        self,
        source: VectorSource,
        *,
        layer_name: str,
        force_feature_count: bool,
        force_total_bounds: bool,
    ) -> Mapping[str, object]:
        try:
            return self._backend.read_info(
                source,
                layer=layer_name,
                force_feature_count=force_feature_count,
                force_total_bounds=force_total_bounds,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise VectorInspectionError(
                f"unable to inspect vector layer {layer_name!r}"
            ) from exc

    @staticmethod
    def _parse_layer_metadata(
        info: Mapping[str, object],
        *,
        layer_name: str,
        advertised_geometry_type: str | None,
    ) -> "_ParsedLayerMetadata":
        reported_name = _optional_text(info.get("layer_name"))
        if reported_name is not None and reported_name != layer_name:
            raise VectorInspectionError(
                f"OGR layer identity changed during inspection: {layer_name!r} -> {reported_name!r}"
            )

        driver = _required_text(info.get("driver"), field="driver", layer_name=layer_name)
        geometry_type = _optional_text(info.get("geometry_type")) or advertised_geometry_type
        crs = _optional_text(info.get("crs"))
        encoding = _optional_text(info.get("encoding"))
        feature_count = _feature_count(info.get("features"), layer_name=layer_name)
        bbox = _bounds(info.get("total_bounds"), layer_name=layer_name)
        return _ParsedLayerMetadata(
            driver=driver,
            geometry_type=geometry_type,
            crs=crs,
            feature_count=feature_count,
            bbox=bbox,
            encoding=encoding,
        )


@dataclass(frozen=True, slots=True)
class _ParsedLayerMetadata:
    driver: str
    geometry_type: str | None
    crs: str | None
    feature_count: int | None
    bbox: VectorBounds | None
    encoding: str | None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_text(value: object, *, field: str, layer_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise VectorInspectionError(f"OGR returned blank {field} for layer {layer_name!r}")
    return text


def _feature_count(value: object, *, layer_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise VectorInspectionError(f"invalid feature count for layer {layer_name!r}")
    try:
        count = int(cast(Any, value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise VectorInspectionError(f"invalid feature count for layer {layer_name!r}") from exc
    if count < 0:
        return None
    return count


def _bounds(value: object, *, layer_name: str) -> VectorBounds | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray)):
        raise VectorInspectionError(f"invalid bbox for layer {layer_name!r}")
    try:
        items = list(cast(Iterable[object], value))
    except TypeError as exc:
        raise VectorInspectionError(f"invalid bbox for layer {layer_name!r}") from exc
    if len(items) != 4:
        raise VectorInspectionError(f"invalid bbox for layer {layer_name!r}")
    try:
        xmin, ymin, xmax, ymax = (float(cast(Any, item)) for item in items)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VectorInspectionError(f"invalid bbox for layer {layer_name!r}") from exc
    values = (xmin, ymin, xmax, ymax)
    if not all(math.isfinite(item) for item in values):
        return None
    if xmin > xmax or ymin > ymax:
        raise VectorInspectionError(f"invalid bbox ordering for layer {layer_name!r}")
    return values
