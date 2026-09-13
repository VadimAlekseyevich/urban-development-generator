from __future__ import annotations

import math
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

import geopandas as gpd
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry.base import BaseGeometry

from backend.app.db.source_layer_writer import (
    CanonicalSourceLayer,
    SourceLayerWriteResult,
    SqlAlchemySourceLayerBatchWriter,
)
from backend.app.services.osm_mapping import MappedOsmBatch, OsmMappedLayer
from backend.app.services.vector_normalization import (
    NormalizedVectorBatch,
    VectorBatchDiagnostics,
)
from core.urban_generator.domain import DataError, WorkingCRS


class OsmCanonicalWriterError(DataError):
    """Raised when mapped OSM batches violate the canonical writer contract."""


class SourceLayerBatchWriter(Protocol):
    def replace(
        self,
        *,
        dataset_version_id: uuid.UUID,
        layer: CanonicalSourceLayer,
        batches: Iterable[NormalizedVectorBatch],
    ) -> SourceLayerWriteResult: ...


@dataclass(frozen=True, slots=True)
class OsmCanonicalWriteResult:
    dataset_version_id: uuid.UUID
    target_layer: OsmMappedLayer
    mapping_version: str
    source_batches: int
    source_features: int
    persistence: SourceLayerWriteResult


@dataclass(slots=True)
class _WriteCounters:
    batches: int = 0
    features: int = 0


class OsmCanonicalWriter:
    """Persist one mapped OSM target layer through the canonical batch writer.

    The caller supplies exactly one target layer per invocation. That keeps processing bounded:
    mapped PBF batches are reprojected and forwarded one at a time, while T06 owns the single
    PostgreSQL replace transaction for that target layer.
    """

    def __init__(self, *, batch_writer: SourceLayerBatchWriter | None = None) -> None:
        self._batch_writer = batch_writer or SqlAlchemySourceLayerBatchWriter()

    def replace_layer(
        self,
        *,
        dataset_version_id: uuid.UUID,
        target_layer: OsmMappedLayer,
        working_crs: WorkingCRS,
        mapping_version: str,
        batches: Iterable[MappedOsmBatch],
    ) -> OsmCanonicalWriteResult:
        if not isinstance(dataset_version_id, uuid.UUID):
            raise TypeError("dataset_version_id must be UUID")
        if not isinstance(target_layer, OsmMappedLayer):
            raise TypeError("target_layer must be OsmMappedLayer")
        if not isinstance(working_crs, WorkingCRS):
            raise TypeError("working_crs must be WorkingCRS")
        version = mapping_version.strip()
        if not version:
            raise ValueError("mapping_version must not be blank")
        if len(version) > 64:
            raise ValueError("mapping_version must be at most 64 characters")

        canonical_layer = CanonicalSourceLayer(target_layer.value)
        counters = _WriteCounters()
        normalized_batches = self._iter_normalized_batches(
            batches,
            target_layer=target_layer,
            working_crs=working_crs,
            mapping_version=version,
            counters=counters,
        )
        persistence = self._batch_writer.replace(
            dataset_version_id=dataset_version_id,
            layer=canonical_layer,
            batches=normalized_batches,
        )
        return OsmCanonicalWriteResult(
            dataset_version_id=dataset_version_id,
            target_layer=target_layer,
            mapping_version=version,
            source_batches=counters.batches,
            source_features=counters.features,
            persistence=persistence,
        )

    def _iter_normalized_batches(
        self,
        batches: Iterable[MappedOsmBatch],
        *,
        target_layer: OsmMappedLayer,
        working_crs: WorkingCRS,
        mapping_version: str,
        counters: _WriteCounters,
    ) -> Iterable[NormalizedVectorBatch]:
        for batch in batches:
            counters.batches += 1
            self._validate_batch(
                batch,
                target_layer=target_layer,
                mapping_version=mapping_version,
            )
            counters.features += len(batch.features)
            if not batch.features:
                continue
            yield self._to_normalized_batch(batch, working_crs=working_crs)

    @staticmethod
    def _validate_batch(
        batch: MappedOsmBatch,
        *,
        target_layer: OsmMappedLayer,
        mapping_version: str,
    ) -> None:
        if not isinstance(batch, MappedOsmBatch):
            raise TypeError("batches must contain MappedOsmBatch values")
        if batch.target_layer is not target_layer:
            raise OsmCanonicalWriterError(
                "mapped OSM batch targets a different canonical layer",
                details={
                    "expected_layer": target_layer.value,
                    "observed_layer": batch.target_layer.value,
                },
            )
        if batch.mapping_version != mapping_version:
            raise OsmCanonicalWriterError(
                "mapped OSM batch uses a different mapping version",
                details={
                    "expected_version": mapping_version,
                    "observed_version": batch.mapping_version,
                },
            )
        try:
            source_crs = CRS.from_user_input(batch.crs)
        except CRSError as exc:
            raise OsmCanonicalWriterError("mapped OSM batch has invalid CRS") from exc
        if source_crs != CRS.from_epsg(4326):
            raise OsmCanonicalWriterError(
                "mapped OSM batch must be EPSG:4326 before canonical persistence",
                details={"observed_crs": source_crs.to_string()},
            )

        for feature in batch.features:
            if feature.target_layer is not target_layer:
                raise OsmCanonicalWriterError(
                    "mapped OSM feature target layer does not match its batch"
                )
            if feature.mapping_version != mapping_version:
                raise OsmCanonicalWriterError(
                    "mapped OSM feature mapping version does not match its batch"
                )
            if not feature.source_feature_id.strip():
                raise OsmCanonicalWriterError("mapped OSM source_feature_id must not be blank")
            if len(feature.source_feature_id) > 255:
                raise OsmCanonicalWriterError(
                    "mapped OSM source_feature_id must be at most 255 characters"
                )
            if not isinstance(feature.geometry, BaseGeometry) or feature.geometry.is_empty:
                raise OsmCanonicalWriterError(
                    "mapped OSM feature must contain a non-empty geometry"
                )
            forbidden = {"geometry", "source_feature_id"}.intersection(
                feature.canonical_attributes
            )
            if forbidden:
                fields = ", ".join(sorted(forbidden))
                raise OsmCanonicalWriterError(
                    f"mapped OSM canonical attributes contain reserved fields: {fields}"
                )

    @staticmethod
    def _to_normalized_batch(
        batch: MappedOsmBatch,
        *,
        working_crs: WorkingCRS,
    ) -> NormalizedVectorBatch:
        records: list[dict[str, object]] = []
        for feature in batch.features:
            row = dict(feature.canonical_attributes)
            row["source_feature_id"] = feature.source_feature_id
            row["geometry"] = feature.geometry
            records.append(row)

        try:
            frame = gpd.GeoDataFrame(records, geometry="geometry", crs=batch.crs)
            frame = frame.to_crs(working_crs.authority)
        except (CRSError, ValueError) as exc:
            raise OsmCanonicalWriterError(
                "unable to reproject mapped OSM batch to project working CRS",
                details={"working_srid": str(working_crs.srid)},
            ) from exc

        for geometry in frame.geometry:
            if not isinstance(geometry, BaseGeometry) or geometry.is_empty or not geometry.is_valid:
                raise OsmCanonicalWriterError(
                    "reprojected OSM geometry is empty or invalid"
                )
            if any(not math.isfinite(float(value)) for value in geometry.bounds):
                raise OsmCanonicalWriterError(
                    "reprojected OSM geometry has non-finite bounds"
                )

        feature_count = len(frame)
        return NormalizedVectorBatch(
            layer_name=f"osm:{batch.target_layer.value}:{batch.source_layer}",
            start_feature=batch.start_feature,
            source_crs=batch.crs,
            working_srid=working_crs.srid,
            frame=frame,
            diagnostics=VectorBatchDiagnostics(
                input_features=feature_count,
                output_features=feature_count,
                repaired_features=0,
                dropped_empty_features=0,
                dropped_type_features=0,
                filtered_collection_features=0,
            ),
        )
