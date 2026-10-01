from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from typing import BinaryIO, Protocol, cast

from backend.app.application.geojson_exports import (
    GEOJSON_EXPORT_CONTENT_TYPE,
    GeoJsonExportJobContext,
    GeoJsonExportPipelineResult,
    geojson_export_filename,
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

_SPOOL_MEMORY_BYTES = 8 * 1024 * 1024


class GeoJsonExportPageReader(Protocol):
    def get_page(
        self,
        *,
        context: GeoJsonExportJobContext,
        after: uuid.UUID | None,
        limit: int,
    ) -> VectorPage: ...


class StreamingGeoJsonExportPipeline:
    """Keyset-read one immutable layer and stream a deterministic RFC 7946 artifact."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        page_reader: GeoJsonExportPageReader,
    ) -> None:
        self._store = store
        self._page_reader = page_reader

    def execute(self, context: GeoJsonExportJobContext) -> GeoJsonExportPipelineResult:
        filename = geojson_export_filename(context.spec.layer_id, context.job_id)
        key = f"exports/{context.project_id}/{context.job_id}/{filename}"
        temporary_ref = ArtifactRef(key)
        ready_ref = ArtifactRef(key, ArtifactState.READY)

        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY_BYTES, mode="w+b") as output:
            digest = hashlib.sha256()
            size_bytes = 0

            def write(payload: bytes) -> None:
                nonlocal size_bytes
                output.write(payload)
                digest.update(payload)
                size_bytes += len(payload)

            write(b'{"type":"FeatureCollection","features":[')
            count = 0
            after: uuid.UUID | None = None
            first = True
            while True:
                remaining = context.spec.max_features - count
                if remaining <= 0:
                    raise DataError(
                        "GeoJSON export feature cap exceeded",
                        details={"max_features": str(context.spec.max_features)},
                    )
                page = self._page_reader.get_page(
                    context=context,
                    after=after,
                    limit=min(MAX_VECTOR_LIMIT, remaining),
                )
                for feature in page.features:
                    if not first:
                        write(b",")
                    write(_feature_json(feature))
                    first = False
                    count += 1
                if not page.truncated:
                    break
                if count >= context.spec.max_features:
                    raise DataError(
                        "GeoJSON export feature cap exceeded",
                        details={"max_features": str(context.spec.max_features)},
                    )
                if page.next_after is None or page.next_after == after:
                    raise DataError("GeoJSON keyset pagination did not advance")
                after = page.next_after
            write(b"]}")

            checksum = "sha256:" + digest.hexdigest()
            existing = _ready_stat(self._store, ready_ref)
            if existing is not None:
                _require_same_artifact(
                    existing,
                    checksum=checksum,
                    size_bytes=size_bytes,
                )
                return GeoJsonExportPipelineResult(
                    artifact=existing,
                    feature_count=count,
                )

            output.seek(0)
            try:
                temporary = self._store.put(
                    temporary_ref,
                    cast(BinaryIO, output),
                    content_type=GEOJSON_EXPORT_CONTENT_TYPE,
                )
                if temporary.checksum != checksum or temporary.size_bytes != size_bytes:
                    raise ArtifactContractError(
                        "ArtifactStore metadata differs from streamed GeoJSON bytes"
                    )
                ready = self._store.promote(temporary_ref)
            except ArtifactContractError:
                recovered = _ready_stat(self._store, ready_ref)
                if recovered is None:
                    raise
                _require_same_artifact(
                    recovered,
                    checksum=checksum,
                    size_bytes=size_bytes,
                )
                ready = recovered
            return GeoJsonExportPipelineResult(
                artifact=ready,
                feature_count=count,
            )


def _feature_json(feature: VectorFeature) -> bytes:
    payload = {
        "type": "Feature",
        "id": str(feature.id),
        "geometry": feature.geometry,
        "properties": feature.properties,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _ready_stat(store: ArtifactStore, ref: ArtifactRef) -> ArtifactStat | None:
    try:
        return store.stat(ref)
    except KeyError:
        return None


def _require_same_artifact(
    stat: ArtifactStat,
    *,
    checksum: str,
    size_bytes: int,
) -> None:
    if (
        stat.checksum != checksum
        or stat.size_bytes != size_bytes
        or stat.content_type != GEOJSON_EXPORT_CONTENT_TYPE
    ):
        raise DataError(
            "existing export artifact does not match immutable export request"
        )
