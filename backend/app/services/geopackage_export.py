from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO, Protocol, cast

import geopandas as gpd
import numpy as np
import pyogrio
from shapely.geometry import shape

from backend.app.application.geopackage_exports import (
    GEOPACKAGE_EXPORT_CONTENT_TYPE,
    GEOPACKAGE_EXPORT_SCHEMA_VERSION,
    GeoPackageExportJobContext,
    GeoPackageExportPipelineResult,
    GeoPackageLayerCount,
    geopackage_export_filename,
    geopackage_layer_name,
)
from backend.app.application.vector_layers import MAX_VECTOR_LIMIT, VectorFeature, VectorPage
from core.urban_generator.domain import (
    ArtifactContractError,
    ArtifactRef,
    ArtifactStat,
    ArtifactState,
    ArtifactStore,
)
from core.urban_generator.domain.errors import DataError


class GeoPackageExportPageReader(Protocol):
    def get_page(
        self,
        *,
        context: GeoPackageExportJobContext,
        layer_id: str,
        after: uuid.UUID | None,
        limit: int,
    ) -> VectorPage: ...


class StreamingGeoPackageExportPipeline:
    """Batch-read immutable vector pages and append them into one GeoPackage."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        page_reader: GeoPackageExportPageReader,
    ) -> None:
        self._store = store
        self._page_reader = page_reader

    def execute(
        self,
        context: GeoPackageExportJobContext,
    ) -> GeoPackageExportPipelineResult:
        filename = geopackage_export_filename(context.job_id)
        key = f"exports/{context.project_id}/{context.job_id}/{filename}"
        temporary_ref = ArtifactRef(key)
        ready_ref = ArtifactRef(key, ArtifactState.READY)

        existing = _ready_stat(self._store, ready_ref)
        if existing is not None:
            counts = _inspect_ready_artifact(
                self._store,
                existing,
                context=context,
            )
            return GeoPackageExportPipelineResult(
                artifact=existing,
                layer_counts=counts,
            )

        with tempfile.TemporaryDirectory(prefix="urban-gpkg-") as directory:
            path = Path(directory) / filename
            counts = self._write_geopackage(path, context=context)
            with path.open("rb") as source:
                try:
                    temporary = self._store.put(
                        temporary_ref,
                        cast(BinaryIO, source),
                        content_type=GEOPACKAGE_EXPORT_CONTENT_TYPE,
                    )
                    ready = self._store.promote(temporary_ref)
                except ArtifactContractError:
                    recovered = _ready_stat(self._store, ready_ref)
                    if recovered is None:
                        raise
                    recovered_counts = _inspect_ready_artifact(
                        self._store,
                        recovered,
                        context=context,
                    )
                    return GeoPackageExportPipelineResult(
                        artifact=recovered,
                        layer_counts=recovered_counts,
                    )

            if ready.content_type != GEOPACKAGE_EXPORT_CONTENT_TYPE:
                raise ArtifactContractError(
                    "ArtifactStore metadata has invalid GeoPackage content type"
                )
            if (
                ready.checksum != temporary.checksum
                or ready.size_bytes != temporary.size_bytes
            ):
                raise ArtifactContractError(
                    "GeoPackage artifact metadata changed during promotion"
                )
            return GeoPackageExportPipelineResult(
                artifact=ready,
                layer_counts=counts,
            )

    def _write_geopackage(
        self,
        path: Path,
        *,
        context: GeoPackageExportJobContext,
    ) -> tuple[GeoPackageLayerCount, ...]:
        total_count = 0
        counts: list[GeoPackageLayerCount] = []

        for layer_id in context.spec.layer_ids:
            layer_name = geopackage_layer_name(layer_id)
            layer_count = 0
            after: uuid.UUID | None = None
            layer_created = False

            while True:
                remaining_layer = context.spec.max_features_per_layer - layer_count
                remaining_total = context.spec.max_total_features - total_count
                if remaining_layer <= 0 or remaining_total <= 0:
                    probe = self._page_reader.get_page(
                        context=context,
                        layer_id=layer_id,
                        after=after,
                        limit=1,
                    )
                    if probe.features:
                        cap = (
                            "per-layer"
                            if remaining_layer <= 0
                            else "total"
                        )
                        raise DataError(
                            f"GeoPackage export {cap} feature cap exceeded",
                            details={
                                "layer_id": layer_id,
                                "max_features_per_layer": str(
                                    context.spec.max_features_per_layer
                                ),
                                "max_total_features": str(
                                    context.spec.max_total_features
                                ),
                            },
                        )
                    if not layer_created:
                        _write_batch(
                            path,
                            layer_id=layer_id,
                            layer_name=layer_name,
                            features=(),
                            append=False,
                        )
                        layer_created = True
                    break

                page = self._page_reader.get_page(
                    context=context,
                    layer_id=layer_id,
                    after=after,
                    limit=min(
                        MAX_VECTOR_LIMIT,
                        remaining_layer,
                        remaining_total,
                    ),
                )
                if page.features:
                    _write_batch(
                        path,
                        layer_id=layer_id,
                        layer_name=layer_name,
                        features=page.features,
                        append=layer_created,
                    )
                    layer_created = True
                    layer_count += len(page.features)
                    total_count += len(page.features)
                elif not layer_created:
                    _write_batch(
                        path,
                        layer_id=layer_id,
                        layer_name=layer_name,
                        features=(),
                        append=False,
                    )
                    layer_created = True

                if not page.truncated:
                    break
                if (
                    layer_count >= context.spec.max_features_per_layer
                    or total_count >= context.spec.max_total_features
                ):
                    cap = (
                        "per-layer"
                        if layer_count >= context.spec.max_features_per_layer
                        else "total"
                    )
                    raise DataError(
                        f"GeoPackage export {cap} feature cap exceeded",
                        details={"layer_id": layer_id},
                    )
                if page.next_after is None or page.next_after == after:
                    raise DataError("GeoPackage keyset pagination did not advance")
                after = page.next_after

            counts.append(
                GeoPackageLayerCount(
                    layer_id=layer_id,
                    feature_count=layer_count,
                )
            )

        return tuple(counts)


def _write_batch(
    path: Path,
    *,
    layer_id: str,
    layer_name: str,
    features: tuple[VectorFeature, ...],
    append: bool,
) -> None:
    if features:
        frame = gpd.GeoDataFrame(
            {
                "feature_id": [str(feature.id) for feature in features],
                "properties_json": [
                    json.dumps(
                        feature.properties,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    for feature in features
                ],
            },
            geometry=[shape(feature.geometry) for feature in features],
            crs="EPSG:4326",
        )
    else:
        frame = gpd.GeoDataFrame(
            {
                "feature_id": np.array([], dtype=object),
                "properties_json": np.array([], dtype=object),
            },
            geometry=gpd.GeoSeries([], crs="EPSG:4326"),
            crs="EPSG:4326",
        )

    pyogrio.write_dataframe(
        frame,
        path,
        driver="GPKG",
        layer=layer_name,
        geometry_type="Unknown",
        append=append,
        layer_metadata={
            "canonical_layer_id": layer_id,
            "export_schema_version": GEOPACKAGE_EXPORT_SCHEMA_VERSION,
        },
    )


def _ready_stat(store: ArtifactStore, ref: ArtifactRef) -> ArtifactStat | None:
    try:
        stat = store.stat(ref)
    except KeyError:
        return None
    if stat.content_type != GEOPACKAGE_EXPORT_CONTENT_TYPE:
        raise DataError("existing GeoPackage artifact has invalid content type")
    return stat


def _inspect_ready_artifact(
    store: ArtifactStore,
    stat: ArtifactStat,
    *,
    context: GeoPackageExportJobContext,
) -> tuple[GeoPackageLayerCount, ...]:
    filename = geopackage_export_filename(context.job_id)
    with tempfile.TemporaryDirectory(prefix="urban-gpkg-recover-") as directory:
        path = Path(directory) / filename
        with store.open(stat.ref) as source, path.open("wb") as target:
            shutil.copyfileobj(source, target)
        actual_layers = {str(row[0]) for row in pyogrio.list_layers(path)}
        expected_layers = {
            geopackage_layer_name(layer_id) for layer_id in context.spec.layer_ids
        }
        if actual_layers != expected_layers:
            raise DataError(
                "existing GeoPackage artifact layer set does not match immutable request"
            )

        counts: list[GeoPackageLayerCount] = []
        total = 0
        for layer_id in context.spec.layer_ids:
            info = pyogrio.read_info(path, layer=geopackage_layer_name(layer_id))
            raw_count = info.get("features")
            if isinstance(raw_count, bool) or not isinstance(raw_count, int):
                raise DataError("GeoPackage driver did not return a feature count")
            if raw_count < 0 or raw_count > context.spec.max_features_per_layer:
                raise DataError("existing GeoPackage artifact violates per-layer cap")
            total += raw_count
            counts.append(
                GeoPackageLayerCount(
                    layer_id=layer_id,
                    feature_count=raw_count,
                )
            )
        if total > context.spec.max_total_features:
            raise DataError("existing GeoPackage artifact violates total feature cap")
        return tuple(counts)
