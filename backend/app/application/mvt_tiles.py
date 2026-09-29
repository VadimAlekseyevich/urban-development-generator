"""S13-T03 owner-authorized, bounded vector tile contract over LayerCatalog.

MVT requests always name one concrete dataset version or run. Geometry is
encoded in Web Mercator tile coordinates, never returned as GeoJSON. The
PostGIS adapter enforces a bounded candidate set *before* MVT geometry work.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Protocol

from backend.app.application.layer_catalog import (
    CANONICAL_LAYER_DEFINITIONS,
    LayerCatalogEntry,
    LayerDeliveryKind,
    LayerOwnerScope,
    layer_catalog_for_context,
)
from backend.app.application.vector_layers import (
    VectorLayerContext,
    VectorLayerNotFoundError,
    VectorLayerNotReadyError,
    VectorLayerQueryError,
)

MVT_SCHEMA_VERSION = "mvt-tile-v1"
MVT_MEDIA_TYPE = "application/vnd.mapbox-vector-tile"
MVT_EXTENT = 4096
MVT_BUFFER = 64
MVT_MAX_ZOOM = 16
MVT_DEFAULT_FEATURE_LIMIT = 500
MVT_MAX_FEATURE_LIMIT = 1000
MVT_MAX_TILE_BYTES = 1_048_576
MVT_STATEMENT_TIMEOUT_MS = 5000
MVT_IMMUTABLE_CACHE_CONTROL = "private, max-age=31536000, immutable"
MVT_VOLATILE_CACHE_CONTROL = "private, no-store"
# The validation report has stable integer violation indices rather than a
# table UUID/geometry; boundary and raster use their dedicated delivery APIs.
_UNSUPPORTED_TILE_LAYERS = frozenset({"validation.violations"})


class MvtTileTooLargeError(RuntimeError):
    """MVT encoding exceeds the hard byte budget; do not serve partial tiles."""


@dataclass(frozen=True, slots=True)
class MvtTile:
    layer_id: str
    bytes: bytes
    candidate_count: int
    feature_limit: int
    truncated: bool
    schema_version: str = MVT_SCHEMA_VERSION
    etag: str | None = None
    cache_control: str = MVT_VOLATILE_CACHE_CONTROL


def matches_if_none_match(header: str | None, etag: str | None) -> bool:
    """Weak GET/HEAD comparison per HTTP If-None-Match semantics.

    The server only emits a safe quoted SHA-256 validator; a malformed
    header, embedded substring or partial opaque tag cannot cause a 304.
    A wildcard only matches when this representation is cache-eligible.
    """
    if header is None or etag is None:
        return False
    return any(
        tag == "*" or tag == etag or tag == "W/" + etag
        for token in header.split(",")
        if (tag := token.strip())
    )


def _content_etag(
    *,
    entry: LayerCatalogEntry,
    working_srid: int,
    z: int,
    x: int,
    y: int,
    feature_limit: int,
    candidates: int,
    payload: bytes,
) -> str:
    """Hash owner identity, render contract, query and complete encoded bytes.

    Different owners or limits cannot reuse validators, even when both
    encode an empty tile. The renderer/schema fingerprint changes when
    the tile contract changes, without requiring a mutable DB timestamp.
    """
    identity = json.dumps(
        {
            "schema": MVT_SCHEMA_VERSION,
            "definition_version": entry.definition.definition_version,
            "instance_key": entry.instance_key,
            "working_srid": working_srid,
            "z": z,
            "x": x,
            "y": y,
            "feature_limit": feature_limit,
            "candidate_count": candidates,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return '"' + hashlib.sha256(identity + b"|" + payload).hexdigest() + '"'


class MvtTileRepository(Protocol):
    def get_context(self, entry: LayerCatalogEntry) -> VectorLayerContext | None: ...

    def encode_tile(
        self,
        *,
        entry: LayerCatalogEntry,
        working_srid: int,
        z: int,
        x: int,
        y: int,
        feature_limit: int,
    ) -> tuple[bytes, int]: ...


class MvtTileQueryService:
    """Validate exact owner scope and tile bounds before doing any spatial work."""

    def __init__(self, repository: MvtTileRepository) -> None:
        self._repository = repository

    def get_tile(
        self,
        *,
        project_id: uuid.UUID,
        layer_id: str,
        dataset_version_id: uuid.UUID | None,
        run_id: uuid.UUID | None,
        z: int,
        x: int,
        y: int,
        feature_limit: int = MVT_DEFAULT_FEATURE_LIMIT,
    ) -> MvtTile:
        definition = next(
            (item for item in CANONICAL_LAYER_DEFINITIONS if item.layer_id == layer_id),
            None,
        )
        if definition is None:
            raise VectorLayerNotFoundError("Unknown canonical layer")
        if (
            definition.delivery_kind is not LayerDeliveryKind.BBOX_GEOJSON
            or layer_id in _UNSUPPORTED_TILE_LAYERS
        ):
            raise VectorLayerQueryError("Layer has no table-backed MVT delivery")
        if definition.owner_scope is LayerOwnerScope.DATASET_VERSION:
            if dataset_version_id is None or run_id is not None:
                raise VectorLayerQueryError(
                    "source tile requires dataset_version_id only (not run_id)"
                )
        elif definition.owner_scope is LayerOwnerScope.RUN:
            if run_id is None or dataset_version_id is not None:
                raise VectorLayerQueryError(
                    "run tile requires run_id only (not dataset_version_id)"
                )
        else:
            raise VectorLayerQueryError("Layer is not dataset/run-scoped")

        for name, value in (("z", z), ("x", x), ("y", y)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise VectorLayerQueryError(f"{name} must be an integer")
        if not 0 <= z <= MVT_MAX_ZOOM:
            raise VectorLayerQueryError(f"z must be between 0 and {MVT_MAX_ZOOM}")
        if not 0 <= x < (1 << z) or not 0 <= y < (1 << z):
            raise VectorLayerQueryError("x/y must be in the zoom-level tile range")
        if (
            isinstance(feature_limit, bool)
            or not isinstance(feature_limit, int)
            or not 1 <= feature_limit <= MVT_MAX_FEATURE_LIMIT
            or feature_limit > (definition.max_features or 0)
        ):
            raise VectorLayerQueryError(
                f"feature_limit must be an integer between 1 and {MVT_MAX_FEATURE_LIMIT}"
            )

        entry = layer_catalog_for_context(
            project_id=project_id,
            dataset_version_id=dataset_version_id,
            run_id=run_id,
        ).get(layer_id)
        if entry is None:
            raise VectorLayerQueryError("Missing layer owner UUID")
        context = self._repository.get_context(entry)
        if context is None:
            raise VectorLayerNotFoundError("Layer owner not found for project")
        if not context.read_model_ready:
            raise VectorLayerNotReadyError("Run read model is not published for this layer")

        encoded, candidates = self._repository.encode_tile(
            entry=entry,
            working_srid=context.working_srid,
            z=z,
            x=x,
            y=y,
            feature_limit=feature_limit,
        )
        if len(encoded) > MVT_MAX_TILE_BYTES:
            raise MvtTileTooLargeError("Tile exceeds hard MVT response byte budget")
        if not 0 <= candidates <= feature_limit + 1:
            raise ValueError("PostGIS returned an unbounded tile candidate count")
        return MvtTile(
            layer_id=definition.layer_id,
            bytes=encoded,
            candidate_count=candidates,
            feature_limit=feature_limit,
            truncated=candidates > feature_limit,
            etag=(
                _content_etag(
                    entry=entry,
                    working_srid=context.working_srid,
                    z=z,
                    x=x,
                    y=y,
                    feature_limit=feature_limit,
                    candidates=candidates,
                    payload=encoded,
                )
                if context.immutable else None
            ),
            cache_control=(
                MVT_IMMUTABLE_CACHE_CONTROL
                if context.immutable else MVT_VOLATILE_CACHE_CONTROL
            ),
        )
