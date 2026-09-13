from __future__ import annotations

import json
import math
import re
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import numpy as np
import rasterio
from numpy.typing import NDArray
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.errors import RasterioError
from rasterio.io import DatasetReader, MemoryFile

from core.urban_generator.domain import ArtifactRef, ArtifactState, ArtifactStore

_SCHEMA_VERSION = "suitability-artifact-v1"
_STATUS_CODES = {"invalid_data": 0, "valid": 1, "hard_excluded": 2}
_ALLOWED_STATES = frozenset({"ready", "referenced"})
_NORMALIZATIONS = frozenset({"identity", "min_max", "inverted_min_max"})
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONFIG_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_SPOOL_CHUNK_BYTES = 1024 * 1024
_DEFAULT_MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
MAX_PREVIEW_DIMENSION = 4096
MIN_PREVIEW_DIMENSION = 128
DEFAULT_PREVIEW_DIMENSION = 2048


class SuitabilityLayerError(ValueError):
    """Base error for suitability map reads."""


class SuitabilityArtifactNotFoundError(LookupError):
    """Raised when the requested artifact metadata row does not exist."""


class SuitabilityArtifactUnavailableError(SuitabilityLayerError):
    """Raised when an artifact exists but is not readable as an immutable result."""


class SuitabilityArtifactContractError(SuitabilityLayerError):
    """Raised when stored bytes do not match the canonical suitability artifact contract."""


@dataclass(frozen=True, slots=True)
class SuitabilityArtifactRecord:
    """Storage-neutral persistence metadata needed to locate one suitability artifact."""

    id: uuid.UUID
    uri: str
    checksum: str
    size_bytes: int
    content_type: str | None
    state: str


@dataclass(frozen=True, slots=True)
class SuitabilityFactorMetadata:
    code: str
    version: str
    weight: float
    normalization: str
    raw_min: float | None
    raw_max: float | None


@dataclass(frozen=True, slots=True)
class SuitabilityLayerStatistics:
    total_cells: int
    valid_cells: int
    hard_excluded_cells: int
    invalid_data_cells: int
    minimum_score_threshold: float
    preferred_score_threshold: float | None
    meets_minimum_cells: int
    preferred_cells: int | None
    min_score: float | None
    max_score: float | None
    mean_score: float | None
    p05_score: float | None
    p50_score: float | None
    p95_score: float | None


@dataclass(frozen=True, slots=True)
class SuitabilityLayerMetadata:
    artifact_id: uuid.UUID
    checksum: str
    size_bytes: int
    content_type: str | None
    schema_version: str
    config_version: str
    config_fingerprint: str
    working_srid: int
    working_bounds: tuple[float, float, float, float]
    width: int
    height: int
    image_coordinates_wgs84: tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
    ]
    wgs84_bounds: tuple[float, float, float, float]
    statistics: SuitabilityLayerStatistics
    factors: tuple[SuitabilityFactorMetadata, ...]
    hard_exclusion_source_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SuitabilityPreview:
    content: bytes
    width: int
    height: int
    source_checksum: str


class SuitabilityArtifactRepository(Protocol):
    """Read-only persistence port for suitability artifact metadata."""

    def get(self, *, artifact_id: uuid.UUID) -> SuitabilityArtifactRecord | None: ...


class SuitabilityLayerService:
    """Inspect and render canonical suitability artifacts without leaking storage paths."""

    def __init__(
        self,
        store: ArtifactStore,
        repository: SuitabilityArtifactRepository,
        *,
        spool_chunk_bytes: int = _DEFAULT_SPOOL_CHUNK_BYTES,
        max_artifact_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> None:
        _require_positive_int("spool_chunk_bytes", spool_chunk_bytes)
        _require_positive_int("max_artifact_bytes", max_artifact_bytes)
        self._store = store
        self._repository = repository
        self._spool_chunk_bytes = spool_chunk_bytes
        self._max_artifact_bytes = max_artifact_bytes

    def get_metadata(self, *, artifact_id: uuid.UUID) -> SuitabilityLayerMetadata:
        record = self._require_record(artifact_id)
        with self._spooled_artifact(record) as path:
            try:
                with rasterio.open(path) as dataset:
                    return _read_metadata(record=record, dataset=dataset)
            except SuitabilityLayerError:
                raise
            except (RasterioError, OSError, ValueError, TypeError) as exc:
                raise SuitabilityArtifactContractError(
                    "artifact is not a readable canonical suitability GeoTIFF"
                ) from exc

    def render_preview(
        self,
        *,
        artifact_id: uuid.UUID,
        max_dimension: int = DEFAULT_PREVIEW_DIMENSION,
    ) -> SuitabilityPreview:
        if (
            isinstance(max_dimension, bool)
            or not isinstance(max_dimension, int)
            or max_dimension < MIN_PREVIEW_DIMENSION
            or max_dimension > MAX_PREVIEW_DIMENSION
        ):
            raise SuitabilityLayerError(
                f"max_dimension must be between {MIN_PREVIEW_DIMENSION} "
                f"and {MAX_PREVIEW_DIMENSION}"
            )

        record = self._require_record(artifact_id)
        with self._spooled_artifact(record) as path:
            try:
                with rasterio.open(path) as dataset:
                    _read_metadata(record=record, dataset=dataset)
                    output_width, output_height = _preview_shape(
                        width=dataset.width,
                        height=dataset.height,
                        max_dimension=max_dimension,
                    )
                    scores = np.asarray(
                        dataset.read(
                            1,
                            out_shape=(output_height, output_width),
                            resampling=Resampling.bilinear,
                        ),
                        dtype=np.float32,
                    )
                    statuses = np.asarray(
                        dataset.read(
                            2,
                            out_shape=(output_height, output_width),
                            resampling=Resampling.nearest,
                        ),
                        dtype=np.float32,
                    )
                    rgba = _render_rgba(scores=scores, statuses=statuses)
                content = _encode_png(rgba)
            except SuitabilityLayerError:
                raise
            except (RasterioError, OSError, ValueError, TypeError) as exc:
                raise SuitabilityArtifactContractError(
                    "failed to render canonical suitability preview"
                ) from exc

        return SuitabilityPreview(
            content=content,
            width=output_width,
            height=output_height,
            source_checksum=record.checksum,
        )

    def _require_record(self, artifact_id: uuid.UUID) -> SuitabilityArtifactRecord:
        if not isinstance(artifact_id, uuid.UUID):
            raise TypeError("artifact_id must be a UUID")
        record = self._repository.get(artifact_id=artifact_id)
        if record is None:
            raise SuitabilityArtifactNotFoundError(artifact_id)
        if record.state not in _ALLOWED_STATES:
            raise SuitabilityArtifactUnavailableError(
                f"artifact state must be ready or referenced, got {record.state!r}"
            )
        if (
            isinstance(record.size_bytes, bool)
            or not isinstance(record.size_bytes, int)
            or record.size_bytes < 0
        ):
            raise SuitabilityArtifactContractError("artifact size metadata is invalid")
        if record.size_bytes > self._max_artifact_bytes:
            raise SuitabilityArtifactUnavailableError(
                "suitability artifact exceeds configured read limit: "
                f"{record.size_bytes} > {self._max_artifact_bytes}"
            )
        if _SHA256_RE.fullmatch(record.checksum) is None:
            raise SuitabilityArtifactContractError("artifact checksum metadata is invalid")
        return record

    @contextmanager
    def _spooled_artifact(self, record: SuitabilityArtifactRecord) -> Iterator[Path]:
        ref = _artifact_ref_from_uri(record.uri)
        try:
            with tempfile.TemporaryDirectory(prefix="urban-suitability-read-") as temp_dir:
                path = Path(temp_dir) / "suitability.tif"
                observed = 0
                with self._store.open(ref) as source, path.open("wb") as target:
                    while True:
                        chunk = source.read(self._spool_chunk_bytes)
                        if not chunk:
                            break
                        if not isinstance(chunk, bytes):
                            raise SuitabilityArtifactContractError(
                                "artifact store stream must yield bytes"
                            )
                        observed += len(chunk)
                        if observed > record.size_bytes:
                            raise SuitabilityArtifactContractError(
                                "artifact stream exceeds persisted size metadata"
                            )
                        target.write(chunk)
                if observed != record.size_bytes:
                    raise SuitabilityArtifactContractError(
                        "artifact stream size does not match persisted metadata"
                    )
                yield path
        except SuitabilityLayerError:
            raise
        except (KeyError, OSError) as exc:
            raise SuitabilityArtifactUnavailableError(
                "artifact blob is unavailable in the configured store"
            ) from exc


def _read_metadata(
    *,
    record: SuitabilityArtifactRecord,
    dataset: DatasetReader,
) -> SuitabilityLayerMetadata:
    if dataset.count != 2:
        raise SuitabilityArtifactContractError("canonical suitability GeoTIFF must have 2 bands")
    if tuple(dataset.dtypes) != ("float32", "float32"):
        raise SuitabilityArtifactContractError(
            "canonical suitability GeoTIFF bands must both use float32"
        )
    if dataset.descriptions != ("suitability_score", "cell_status"):
        raise SuitabilityArtifactContractError("canonical suitability band descriptions mismatch")
    if dataset.crs is None:
        raise SuitabilityArtifactContractError("canonical suitability GeoTIFF requires a CRS")

    tags = dataset.tags()
    if tags.get("schema_version") != _SCHEMA_VERSION:
        raise SuitabilityArtifactContractError(
            f"artifact schema_version must be {_SCHEMA_VERSION!r}"
        )
    status_codes = _json_object_tag(tags, "status_codes_json")
    if status_codes != _STATUS_CODES:
        raise SuitabilityArtifactContractError("canonical suitability status codes mismatch")

    statistics = _parse_statistics(_json_object_tag(tags, "statistics_json"))
    provenance = _json_object_tag(tags, "provenance_json")
    config_version = _require_non_empty_str(provenance.get("config_version"), "config_version")
    config_fingerprint = _require_non_empty_str(
        provenance.get("config_fingerprint"), "config_fingerprint"
    )
    if _CONFIG_FINGERPRINT_RE.fullmatch(config_fingerprint) is None:
        raise SuitabilityArtifactContractError("config_fingerprint must be lowercase SHA-256 hex")

    grid_payload = _require_dict(provenance.get("grid"), "grid")
    working_srid = _require_positive_int_value(grid_payload.get("working_srid"), "working_srid")
    width = _require_positive_int_value(grid_payload.get("width"), "width")
    height = _require_positive_int_value(grid_payload.get("height"), "height")
    bounds_list = _require_list(grid_payload.get("bounds"), "bounds")
    if len(bounds_list) != 4:
        raise SuitabilityArtifactContractError("grid bounds must contain four values")
    working_bounds = cast(
        tuple[float, float, float, float],
        tuple(_require_finite_float(item, "bounds") for item in bounds_list),
    )
    if not (
        working_bounds[0] < working_bounds[2]
        and working_bounds[1] < working_bounds[3]
    ):
        raise SuitabilityArtifactContractError("grid bounds must have positive width and height")

    if width != dataset.width or height != dataset.height:
        raise SuitabilityArtifactContractError("provenance grid dimensions mismatch GeoTIFF")
    epsg = dataset.crs.to_epsg()
    if epsg != working_srid:
        raise SuitabilityArtifactContractError("provenance working_srid mismatch GeoTIFF CRS")
    raster_bounds = (
        float(dataset.bounds.left),
        float(dataset.bounds.bottom),
        float(dataset.bounds.right),
        float(dataset.bounds.top),
    )
    if not all(
        math.isclose(expected, actual, rel_tol=1e-10, abs_tol=1e-6)
        for expected, actual in zip(working_bounds, raster_bounds, strict=True)
    ):
        raise SuitabilityArtifactContractError("provenance bounds mismatch GeoTIFF extent")

    if statistics.total_cells != width * height:
        raise SuitabilityArtifactContractError("statistics total_cells mismatch raster dimensions")

    factors = _parse_factors(provenance.get("factors"))
    hard_sources = _parse_unique_strings(
        provenance.get("hard_exclusion_source_codes"),
        "hard_exclusion_source_codes",
    )
    coordinates = _wgs84_image_coordinates(dataset=dataset)
    west = min(point[0] for point in coordinates)
    south = min(point[1] for point in coordinates)
    east = max(point[0] for point in coordinates)
    north = max(point[1] for point in coordinates)

    return SuitabilityLayerMetadata(
        artifact_id=record.id,
        checksum=record.checksum,
        size_bytes=record.size_bytes,
        content_type=record.content_type,
        schema_version=_SCHEMA_VERSION,
        config_version=config_version,
        config_fingerprint=config_fingerprint,
        working_srid=working_srid,
        working_bounds=working_bounds,
        width=width,
        height=height,
        image_coordinates_wgs84=coordinates,
        wgs84_bounds=(west, south, east, north),
        statistics=statistics,
        factors=factors,
        hard_exclusion_source_codes=hard_sources,
    )


def _parse_statistics(payload: dict[str, object]) -> SuitabilityLayerStatistics:
    total_cells = _require_non_negative_int(payload.get("total_cells"), "total_cells")
    valid_cells = _require_non_negative_int(payload.get("valid_cells"), "valid_cells")
    hard_cells = _require_non_negative_int(
        payload.get("hard_excluded_cells"), "hard_excluded_cells"
    )
    invalid_cells = _require_non_negative_int(
        payload.get("invalid_data_cells"), "invalid_data_cells"
    )
    if valid_cells + hard_cells + invalid_cells != total_cells:
        raise SuitabilityArtifactContractError("statistics cell counts must sum to total_cells")

    minimum = _require_unit_interval(
        payload.get("minimum_score_threshold"), "minimum_score_threshold"
    )
    preferred = _optional_unit_interval(
        payload.get("preferred_score_threshold"), "preferred_score_threshold"
    )
    if preferred is not None and preferred < minimum:
        raise SuitabilityArtifactContractError(
            "preferred_score_threshold must be >= minimum_score_threshold"
        )
    meets_minimum = _require_non_negative_int(
        payload.get("meets_minimum_cells"), "meets_minimum_cells"
    )
    if meets_minimum > valid_cells:
        raise SuitabilityArtifactContractError("meets_minimum_cells exceeds valid_cells")

    preferred_raw = payload.get("preferred_cells")
    if preferred is None:
        if preferred_raw is not None:
            raise SuitabilityArtifactContractError(
                "preferred_cells must be null when preferred threshold is null"
            )
        preferred_cells = None
    else:
        preferred_cells = _require_non_negative_int(preferred_raw, "preferred_cells")
        if preferred_cells > valid_cells:
            raise SuitabilityArtifactContractError("preferred_cells exceeds valid_cells")

    score_fields = (
        "min_score",
        "max_score",
        "mean_score",
        "p05_score",
        "p50_score",
        "p95_score",
    )
    score_values = tuple(_optional_unit_interval(payload.get(key), key) for key in score_fields)
    if valid_cells == 0 and any(value is not None for value in score_values):
        raise SuitabilityArtifactContractError("empty valid raster must have null score statistics")
    if valid_cells > 0 and any(value is None for value in score_values):
        raise SuitabilityArtifactContractError("valid raster requires score statistics")

    min_score, max_score, mean_score, p05, p50, p95 = score_values
    if min_score is not None and max_score is not None and min_score > max_score:
        raise SuitabilityArtifactContractError("min_score must be <= max_score")
    if p05 is not None and p50 is not None and p95 is not None and not (p05 <= p50 <= p95):
        raise SuitabilityArtifactContractError("score quantiles must be ordered")

    return SuitabilityLayerStatistics(
        total_cells=total_cells,
        valid_cells=valid_cells,
        hard_excluded_cells=hard_cells,
        invalid_data_cells=invalid_cells,
        minimum_score_threshold=minimum,
        preferred_score_threshold=preferred,
        meets_minimum_cells=meets_minimum,
        preferred_cells=preferred_cells,
        min_score=min_score,
        max_score=max_score,
        mean_score=mean_score,
        p05_score=p05,
        p50_score=p50,
        p95_score=p95,
    )


def _parse_factors(value: object) -> tuple[SuitabilityFactorMetadata, ...]:
    items = _require_list(value, "factors")
    factors: list[SuitabilityFactorMetadata] = []
    codes: set[str] = set()
    for item in items:
        payload = _require_dict(item, "factor")
        code = _require_non_empty_str(payload.get("code"), "factor code")
        version = _require_non_empty_str(payload.get("version"), "factor version")
        if code in codes:
            raise SuitabilityArtifactContractError("factor codes must be unique")
        codes.add(code)
        weight = _require_finite_float(payload.get("weight"), "factor weight")
        if weight <= 0:
            raise SuitabilityArtifactContractError("factor weight must be positive")
        normalization = _require_non_empty_str(
            payload.get("normalization"), "factor normalization"
        )
        if normalization not in _NORMALIZATIONS:
            raise SuitabilityArtifactContractError("factor normalization is unsupported")
        raw_min = _optional_finite_float(payload.get("raw_min"), "factor raw_min")
        raw_max = _optional_finite_float(payload.get("raw_max"), "factor raw_max")
        if normalization == "identity":
            if raw_min is not None or raw_max is not None:
                raise SuitabilityArtifactContractError(
                    "identity factor must not declare raw normalization bounds"
                )
        elif raw_min is None or raw_max is None or raw_max <= raw_min:
            raise SuitabilityArtifactContractError(
                "min/max factor requires increasing raw normalization bounds"
            )
        factors.append(
            SuitabilityFactorMetadata(
                code=code,
                version=version,
                weight=weight,
                normalization=normalization,
                raw_min=raw_min,
                raw_max=raw_max,
            )
        )
    if not factors:
        raise SuitabilityArtifactContractError("canonical suitability provenance requires factors")
    return tuple(factors)


def _wgs84_image_coordinates(
    *,
    dataset: DatasetReader,
) -> tuple[
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
]:
    if dataset.crs is None:
        raise SuitabilityArtifactContractError("canonical suitability GeoTIFF requires a CRS")
    transformer = Transformer.from_crs(dataset.crs, 4326, always_xy=True)
    source_points = (
        (dataset.bounds.left, dataset.bounds.top),
        (dataset.bounds.right, dataset.bounds.top),
        (dataset.bounds.right, dataset.bounds.bottom),
        (dataset.bounds.left, dataset.bounds.bottom),
    )
    transformed: list[tuple[float, float]] = []
    for x, y in source_points:
        longitude, latitude = transformer.transform(x, y)
        lon = float(longitude)
        lat = float(latitude)
        if not math.isfinite(lon) or not math.isfinite(lat):
            raise SuitabilityArtifactContractError("artifact extent cannot be transformed to WGS84")
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            raise SuitabilityArtifactContractError("artifact WGS84 extent is outside valid bounds")
        transformed.append((lon, lat))
    return cast(
        tuple[
            tuple[float, float],
            tuple[float, float],
            tuple[float, float],
            tuple[float, float],
        ],
        tuple(transformed),
    )


def _preview_shape(*, width: int, height: int, max_dimension: int) -> tuple[int, int]:
    largest = max(width, height)
    if largest <= max_dimension:
        return width, height
    scale = max_dimension / largest
    return max(1, int(math.floor(width * scale))), max(1, int(math.floor(height * scale)))


def _render_rgba(
    *,
    scores: NDArray[np.float32],
    statuses: NDArray[np.float32],
) -> NDArray[np.uint8]:
    if scores.shape != statuses.shape or scores.ndim != 2:
        raise SuitabilityArtifactContractError("preview bands must have the same 2D shape")
    rounded_status = np.rint(statuses).astype(np.int16)
    if not np.all(np.isclose(statuses, rounded_status, atol=1e-6)):
        raise SuitabilityArtifactContractError("cell_status band contains non-integer values")
    known_status = np.isin(rounded_status, tuple(_STATUS_CODES.values()))
    if not bool(np.all(known_status)):
        raise SuitabilityArtifactContractError("cell_status band contains unknown codes")

    valid = rounded_status == _STATUS_CODES["valid"]
    hard = rounded_status == _STATUS_CODES["hard_excluded"]
    if np.any(~np.isfinite(scores[valid])):
        raise SuitabilityArtifactContractError("valid suitability scores must be finite")
    if np.any(scores[valid] < 0.0) or np.any(scores[valid] > 1.0):
        raise SuitabilityArtifactContractError("valid suitability scores must stay inside 0..1")

    clipped = np.clip(scores, 0.0, 1.0).astype(np.float64, copy=False)
    low = np.array([185.0, 28.0, 28.0], dtype=np.float64)
    middle = np.array([245.0, 158.0, 11.0], dtype=np.float64)
    high = np.array([21.0, 128.0, 61.0], dtype=np.float64)
    lower_t = np.clip(clipped * 2.0, 0.0, 1.0)[..., None]
    upper_t = np.clip((clipped - 0.5) * 2.0, 0.0, 1.0)[..., None]
    lower_rgb = low + (middle - low) * lower_t
    upper_rgb = middle + (high - middle) * upper_t
    rgb = np.where((clipped <= 0.5)[..., None], lower_rgb, upper_rgb)

    rgba = np.zeros((*scores.shape, 4), dtype=np.uint8)
    rgba[..., :3] = np.rint(rgb).astype(np.uint8)
    rgba[..., 3][valid] = 255
    rgba[..., :3][hard] = np.array([30, 41, 59], dtype=np.uint8)
    rgba[..., 3][hard] = 210
    return rgba


def _encode_png(rgba: NDArray[np.uint8]) -> bytes:
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise SuitabilityArtifactContractError("RGBA preview must have shape height x width x 4")
    height, width, _channels = rgba.shape
    bands = np.moveaxis(rgba, 2, 0)
    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="PNG",
            width=width,
            height=height,
            count=4,
            dtype="uint8",
        ) as output:
            output.write(bands)
        return bytes(memory_file.read())


def _artifact_ref_from_uri(uri: str) -> ArtifactRef:
    if not isinstance(uri, str) or not uri.startswith("artifact://"):
        raise SuitabilityArtifactContractError("artifact URI must use artifact:// scheme")
    key = uri.removeprefix("artifact://")
    try:
        return ArtifactRef(key=key, state=ArtifactState.READY)
    except ValueError as exc:
        raise SuitabilityArtifactContractError(
            "artifact URI contains an invalid logical key"
        ) from exc


def _json_object_tag(tags: dict[str, str], key: str) -> dict[str, object]:
    raw = tags.get(key)
    if raw is None:
        raise SuitabilityArtifactContractError(f"missing GeoTIFF tag {key!r}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SuitabilityArtifactContractError(f"GeoTIFF tag {key!r} is not valid JSON") from exc
    return _require_dict(value, key)


def _require_dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise SuitabilityArtifactContractError(f"{label} must be a JSON object")
    return cast(dict[str, object], value)


def _require_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise SuitabilityArtifactContractError(f"{label} must be a JSON array")
    return cast(list[object], value)


def _parse_unique_strings(value: object, label: str) -> tuple[str, ...]:
    items = _require_list(value, label)
    strings = tuple(_require_non_empty_str(item, label) for item in items)
    if len(strings) != len(set(strings)):
        raise SuitabilityArtifactContractError(f"{label} values must be unique")
    return strings


def _require_non_empty_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SuitabilityArtifactContractError(f"{label} must be a non-empty string")
    return value


def _require_finite_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SuitabilityArtifactContractError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise SuitabilityArtifactContractError(f"{label} must be a finite number")
    return result


def _optional_finite_float(value: object, label: str) -> float | None:
    if value is None:
        return None
    return _require_finite_float(value, label)


def _require_unit_interval(value: object, label: str) -> float:
    result = _require_finite_float(value, label)
    if result < 0.0 or result > 1.0:
        raise SuitabilityArtifactContractError(f"{label} must be inside 0..1")
    return result


def _optional_unit_interval(value: object, label: str) -> float | None:
    if value is None:
        return None
    return _require_unit_interval(value, label)


def _require_non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SuitabilityArtifactContractError(f"{label} must be a non-negative integer")
    return value


def _require_positive_int_value(value: object, label: str) -> int:
    result = _require_non_negative_int(value, label)
    if result <= 0:
        raise SuitabilityArtifactContractError(f"{label} must be positive")
    return result


def _require_positive_int(label: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
