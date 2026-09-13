from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Final, cast

from backend.app.application.ingest import IngestJobContext, IngestPipelineResult
from backend.app.db.source_layer_writer import (
    CanonicalSourceLayer,
    SourceLayerWriteResult,
    SqlAlchemySourceLayerBatchWriter,
)
from backend.app.services.raster_normalization import (
    RasterNormalizationConfig,
    RasterNormalizer,
    RasterResampling,
)
from backend.app.services.shapefile_zip import ShapefileZipError, ShapefileZipExtractor
from backend.app.services.vector_inspection import (
    VectorDatasetInspection,
    VectorInspectionError,
    VectorInspector,
    VectorLayerInspection,
)
from backend.app.services.vector_normalization import (
    GeometryFamily,
    VectorNormalizationConfig,
    VectorNormalizer,
)
from core.urban_generator.domain import (
    ArtifactRef,
    ArtifactStat,
    ArtifactState,
    ArtifactStore,
    WorkingCRS,
)
from core.urban_generator.domain.errors import ConfigError, DataError, TransientError

_MATERIALIZE_CHUNK_BYTES: Final = 1024 * 1024


class IngestSourceFormat(StrEnum):
    GEOJSON = "geojson"
    GPKG = "gpkg"
    SHAPEFILE_ZIP = "shapefile_zip"
    GEOTIFF = "geotiff"


@dataclass(frozen=True, slots=True)
class IngestManifest:
    """Immutable DatasetVersion source_metadata contract consumed by S03-T09."""

    artifact_key: str
    source_format: IngestSourceFormat
    layer: str | None = None
    geometry_family: GeometryFamily | None = None
    target_resolution_m: float | None = None
    clip_bounds: tuple[float, float, float, float] | None = None
    resampling: RasterResampling = RasterResampling.NEAREST

    @classmethod
    def from_metadata(cls, metadata: dict[str, object]) -> IngestManifest:
        artifact_key = _required_text(metadata.get("artifact_key"), field="artifact_key")
        try:
            ArtifactRef(key=artifact_key, state=ArtifactState.READY)
        except ValueError as exc:
            raise DataError("source_metadata artifact_key is invalid") from exc

        format_text = _required_text(metadata.get("format"), field="format").lower()
        try:
            source_format = IngestSourceFormat(format_text)
        except ValueError as exc:
            raise DataError(
                "source_metadata format is unsupported",
                details={"format": format_text},
            ) from exc

        layer = _optional_text(metadata.get("layer"))
        geometry_family = _geometry_family(metadata.get("geometry_family"))
        target_resolution_m = _optional_positive_float(
            metadata.get("target_resolution_m"),
            field="target_resolution_m",
        )
        clip_bounds = _optional_bounds(metadata.get("clip_bounds"))
        resampling = _resampling(metadata.get("resampling"))

        if source_format is IngestSourceFormat.GEOTIFF:
            if layer is not None or geometry_family is not None:
                raise DataError("raster ingest metadata must not declare vector layer options")
        elif (
            "target_resolution_m" in metadata
            or "clip_bounds" in metadata
            or "resampling" in metadata
        ):
            raise DataError("vector ingest metadata must not declare raster options")

        return cls(
            artifact_key=artifact_key,
            source_format=source_format,
            layer=layer,
            geometry_family=geometry_family,
            target_resolution_m=target_resolution_m,
            clip_bounds=clip_bounds,
            resampling=resampling,
        )


class DatasetIngestPipeline:
    """Execute vector or raster normalization for one immutable DatasetVersion."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        vector_inspector: VectorInspector | None = None,
        vector_normalizer: VectorNormalizer | None = None,
        vector_writer: SqlAlchemySourceLayerBatchWriter | None = None,
        shapefile_extractor: ShapefileZipExtractor | None = None,
        raster_normalizer: RasterNormalizer | None = None,
    ) -> None:
        self._store = store
        self._vector_inspector = vector_inspector or VectorInspector()
        self._vector_normalizer = vector_normalizer or VectorNormalizer()
        self._vector_writer = vector_writer or SqlAlchemySourceLayerBatchWriter()
        self._shapefile_extractor = shapefile_extractor or ShapefileZipExtractor()
        self._raster_normalizer = raster_normalizer or RasterNormalizer()

    def execute(self, context: IngestJobContext) -> IngestPipelineResult:
        manifest = IngestManifest.from_metadata(context.source_metadata)
        source_ref = ArtifactRef(key=manifest.artifact_key, state=ArtifactState.READY)
        source_stat = self._stat_source(source_ref)
        self._validate_checksum(context, source_stat.checksum)

        try:
            working_crs = WorkingCRS(context.working_srid)
        except ValueError as exc:
            raise ConfigError(
                "project working_srid is invalid for ingest",
                details={"working_srid": str(context.working_srid)},
            ) from exc

        if manifest.source_format is IngestSourceFormat.GEOTIFF:
            return self._ingest_raster(
                context,
                manifest=manifest,
                source_ref=source_ref,
                source_stat=source_stat,
                working_crs=working_crs,
            )
        return self._ingest_vector(
            context,
            manifest=manifest,
            source_ref=source_ref,
            source_stat=source_stat,
            working_crs=working_crs,
        )

    def _ingest_vector(
        self,
        context: IngestJobContext,
        *,
        manifest: IngestManifest,
        source_ref: ArtifactRef,
        source_stat: ArtifactStat,
        working_crs: WorkingCRS,
    ) -> IngestPipelineResult:
        try:
            canonical_layer = CanonicalSourceLayer(context.dataset_kind)
        except ValueError as exc:
            raise DataError(
                "vector Dataset.kind is not a canonical source layer",
                details={"dataset_kind": context.dataset_kind},
            ) from exc

        suffix = {
            IngestSourceFormat.GEOJSON: ".geojson",
            IngestSourceFormat.GPKG: ".gpkg",
            IngestSourceFormat.SHAPEFILE_ZIP: ".zip",
        }[manifest.source_format]

        with tempfile.TemporaryDirectory(prefix="urban-ingest-vector-") as temp_dir:
            materialized = Path(temp_dir) / f"source{suffix}"
            self._materialize(
                source_ref,
                destination=materialized,
                expected_size=source_stat.size_bytes,
            )
            try:
                if manifest.source_format is IngestSourceFormat.SHAPEFILE_ZIP:
                    with materialized.open("rb") as archive_stream:
                        with self._shapefile_extractor.extract(archive_stream) as extracted:
                            source_path = self._select_shapefile(
                                extracted.shapefiles,
                                layer=manifest.layer,
                            )
                            extracted_path = extracted.path_for(source_path)
                            inspection = self._select_single_spatial_layer(
                                self._vector_inspector.inspect(extracted_path),
                                layer=None,
                            )
                            result = self._write_vector_layer(
                                extracted_path,
                                context=context,
                                canonical_layer=canonical_layer,
                                inspection=inspection,
                                manifest=manifest,
                                working_crs=working_crs,
                            )
                else:
                    dataset_inspection = self._vector_inspector.inspect(materialized)
                    inspection = self._select_single_spatial_layer(
                        dataset_inspection,
                        layer=manifest.layer,
                    )
                    result = self._write_vector_layer(
                        materialized,
                        context=context,
                        canonical_layer=canonical_layer,
                        inspection=inspection,
                        manifest=manifest,
                        working_crs=working_crs,
                    )
            except (VectorInspectionError, ShapefileZipError) as exc:
                raise DataError(
                    "vector source cannot be inspected safely",
                    details={"error_type": type(exc).__name__},
                ) from exc

        return IngestPipelineResult(
            source_artifact=source_stat,
            details={
                "kind": "vector",
                "canonical_layer": canonical_layer.value,
                "inserted_rows": result.inserted_rows,
                "deleted_rows": result.deleted_rows,
                "input_batches": result.input_batches,
                "analyzed": result.analyzed,
            },
        )

    def _write_vector_layer(
        self,
        source: Path,
        *,
        context: IngestJobContext,
        canonical_layer: CanonicalSourceLayer,
        inspection: VectorLayerInspection,
        manifest: IngestManifest,
        working_crs: WorkingCRS,
    ) -> SourceLayerWriteResult:
        geometry_family = self._geometry_family_for_layer(
            canonical_layer,
            inspection=inspection,
            requested=manifest.geometry_family,
        )
        batches = self._vector_normalizer.iter_normalized_batches(
            source,
            inspection=inspection,
            working_crs=working_crs,
            config=VectorNormalizationConfig(geometry_family=geometry_family),
        )
        return self._vector_writer.replace(
            dataset_version_id=context.dataset_version_id,
            layer=canonical_layer,
            batches=batches,
        )

    def _ingest_raster(
        self,
        context: IngestJobContext,
        *,
        manifest: IngestManifest,
        source_ref: ArtifactRef,
        source_stat: ArtifactStat,
        working_crs: WorkingCRS,
    ) -> IngestPipelineResult:
        output_ref = ArtifactRef(
            key=f"datasets/{context.dataset_version_id}/normalized/raster.tif",
            state=ArtifactState.TEMPORARY,
        )
        ready_ref = output_ref.as_ready()
        reused = False
        try:
            normalized_stat = self._store.stat(ready_ref)
            reused = True
        except KeyError:
            normalized = self._raster_normalizer.normalize(
                self._store,
                source_ref=source_ref,
                output_ref=output_ref,
                working_crs=working_crs,
                config=RasterNormalizationConfig(
                    target_resolution_m=manifest.target_resolution_m,
                    clip_bounds=manifest.clip_bounds,
                    resampling=manifest.resampling,
                ),
            )
            normalized_stat = normalized.stat

        return IngestPipelineResult(
            source_artifact=source_stat,
            derived_artifacts=(normalized_stat,),
            details={
                "kind": "raster",
                "normalized_artifact_key": ready_ref.key,
                "reused_ready_artifact": reused,
                "working_srid": context.working_srid,
            },
        )

    def _stat_source(self, source_ref: ArtifactRef) -> ArtifactStat:
        try:
            return self._store.stat(source_ref)
        except KeyError as exc:
            raise DataError(
                "DatasetVersion source artifact does not exist",
                details={"artifact_key": source_ref.key},
            ) from exc
        except OSError as exc:
            raise TransientError(
                "unable to read source artifact metadata",
                details={"artifact_key": source_ref.key},
            ) from exc

    @staticmethod
    def _validate_checksum(context: IngestJobContext, store_checksum: str) -> None:
        if context.checksum_sha256 is None:
            return
        digest = store_checksum.removeprefix("sha256:")
        if digest != context.checksum_sha256:
            raise DataError(
                "DatasetVersion checksum does not match source artifact",
                details={"dataset_version_id": str(context.dataset_version_id)},
            )

    def _materialize(
        self,
        ref: ArtifactRef,
        *,
        destination: Path,
        expected_size: int,
    ) -> None:
        observed = 0
        try:
            with self._store.open(ref) as source, destination.open("wb") as target:
                while True:
                    chunk = source.read(_MATERIALIZE_CHUNK_BYTES)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise TypeError("artifact source must yield bytes")
                    observed += len(chunk)
                    if observed > expected_size:
                        raise DataError("source artifact stream exceeds declared size")
                    target.write(chunk)
        except KeyError as exc:
            raise DataError(
                "DatasetVersion source artifact disappeared during ingest",
                details={"artifact_key": ref.key},
            ) from exc
        except OSError as exc:
            raise TransientError(
                "unable to materialize source artifact",
                details={"artifact_key": ref.key},
            ) from exc
        if observed != expected_size:
            raise DataError("source artifact stream size does not match metadata")

    @staticmethod
    def _select_shapefile(
        shapefiles: tuple[PurePosixPath, ...],
        *,
        layer: str | None,
    ) -> PurePosixPath:
        if not shapefiles:
            raise DataError("Shapefile ZIP contains no .shp files")
        if layer is None:
            if len(shapefiles) != 1:
                raise DataError(
                    "Shapefile ZIP contains multiple layers; source_metadata.layer is required"
                )
            return shapefiles[0]

        normalized = layer.casefold()
        matches = [
            candidate
            for candidate in shapefiles
            if candidate.as_posix().casefold() == normalized
            or candidate.stem.casefold() == normalized
        ]
        if len(matches) != 1:
            raise DataError(
                "source_metadata.layer does not identify exactly one Shapefile layer",
                details={"layer": layer},
            )
        return matches[0]

    @staticmethod
    def _select_single_spatial_layer(
        dataset_inspection: VectorDatasetInspection,
        *,
        layer: str | None,
    ) -> VectorLayerInspection:
        spatial = tuple(
            candidate
            for candidate in dataset_inspection.layers
            if candidate.geometry_type is not None
        )
        if layer is not None:
            matches = tuple(candidate for candidate in spatial if candidate.name == layer)
            if len(matches) != 1:
                raise DataError(
                    "source_metadata.layer does not identify one spatial layer",
                    details={"layer": layer},
                )
            return matches[0]
        if len(spatial) != 1:
            raise DataError(
                "vector source must contain exactly one spatial layer when layer is omitted",
                details={"spatial_layers": str(len(spatial))},
            )
        return spatial[0]

    @staticmethod
    def _geometry_family_for_layer(
        canonical_layer: CanonicalSourceLayer,
        *,
        inspection: VectorLayerInspection,
        requested: GeometryFamily | None,
    ) -> GeometryFamily:
        fixed_family = {
            CanonicalSourceLayer.ROADS: GeometryFamily.LINE,
            CanonicalSourceLayer.BUILDINGS: GeometryFamily.POLYGON,
            CanonicalSourceLayer.LANDUSE: GeometryFamily.POLYGON,
        }.get(canonical_layer)
        advertised = _family_from_geometry_type(inspection.geometry_type)

        if fixed_family is not None:
            if requested is not None and requested is not fixed_family:
                raise DataError(
                    "source_metadata.geometry_family conflicts with canonical layer",
                    details={"canonical_layer": canonical_layer.value},
                )
            if advertised is not None and advertised is not fixed_family:
                raise DataError(
                    "vector layer geometry type conflicts with canonical layer",
                    details={
                        "canonical_layer": canonical_layer.value,
                        "geometry_type": str(inspection.geometry_type),
                    },
                )
            return fixed_family

        family = requested or advertised
        if family is None:
            raise DataError(
                "geometry_family is required when OGR cannot infer a canonical geometry family",
                details={"canonical_layer": canonical_layer.value},
            )
        if advertised is not None and advertised is not family:
            raise DataError(
                "source_metadata.geometry_family conflicts with inspected geometry type",
                details={"geometry_type": str(inspection.geometry_type)},
            )
        return family


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataError(f"source_metadata.{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DataError("optional source_metadata text values must be non-empty strings")
    return value.strip()


def _geometry_family(value: object) -> GeometryFamily | None:
    if value is None:
        return None
    try:
        return GeometryFamily(str(value).strip().lower())
    except ValueError as exc:
        raise DataError(
            "source_metadata.geometry_family is unsupported",
            details={"geometry_family": str(value)},
        ) from exc


def _optional_positive_float(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DataError(f"source_metadata.{field} must be a positive finite number")
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataError(f"source_metadata.{field} must be a positive finite number") from exc
    if not math.isfinite(number) or number <= 0:
        raise DataError(f"source_metadata.{field} must be a positive finite number")
    return number


def _optional_bounds(value: object) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise DataError("source_metadata.clip_bounds must contain four numbers")
    try:
        left, bottom, right, top = (float(item) for item in value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataError("source_metadata.clip_bounds must contain finite numbers") from exc
    if not all(math.isfinite(item) for item in (left, bottom, right, top)):
        raise DataError("source_metadata.clip_bounds must contain finite numbers")
    if left >= right or bottom >= top:
        raise DataError("source_metadata.clip_bounds must satisfy left < right and bottom < top")
    return (left, bottom, right, top)


def _resampling(value: object) -> RasterResampling:
    if value is None:
        return RasterResampling.NEAREST
    try:
        return RasterResampling(str(value).strip().lower())
    except ValueError as exc:
        raise DataError(
            "source_metadata.resampling is unsupported",
            details={"resampling": str(value)},
        ) from exc


def _family_from_geometry_type(value: str | None) -> GeometryFamily | None:
    if value is None:
        return None
    normalized = value.replace(" ", "").casefold()
    if normalized in {"point", "multipoint", "pointz", "multipointz"}:
        return GeometryFamily.POINT
    if normalized in {
        "linestring",
        "multilinestring",
        "linestringz",
        "multilinestringz",
    }:
        return GeometryFamily.LINE
    if normalized in {"polygon", "multipolygon", "polygonz", "multipolygonz"}:
        return GeometryFamily.POLYGON
    return None
