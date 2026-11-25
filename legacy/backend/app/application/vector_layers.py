"""S13-T02 generic, read-only bbox/keyset query contract over LayerCatalog.

The canonical LayerCatalog selects a logical layer; the repository decides its
fixed persistence binding. No arbitrary table/column names or GIS work in HTTP.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Literal, Protocol

from backend.app.application.layer_catalog import (
    CANONICAL_LAYER_DEFINITIONS,
    LayerCatalogEntry,
    LayerDeliveryKind,
    LayerOwnerScope,
    layer_catalog_for_context,
)
from backend.app.application.source_layers import SourceLayerBbox, SourceLayerQueryError
from core.urban_generator.domain.crs import CRSContractError, require_working_crs

VECTOR_API_SCHEMA_VERSION = "bounded-vector-v1"
VECTOR_PRESENTATION_SRID = 4326
VECTOR_PRESENTATION_CRS: Literal["EPSG:4326"] = "EPSG:4326"
DEFAULT_VECTOR_LIMIT = 1000
MAX_VECTOR_LIMIT = 5000
MAX_SIMPLIFY_METRES = 100.0
# Validation violations have stable integer report indices, not UUID row keys.
# The canonical validation read API remains authoritative until an explicit
# keyset projection for that persisted JSON codec is designed.
_UNSUPPORTED_VECTOR_IDS = frozenset({"validation.violations"})


class VectorLayerQueryError(ValueError):
    """Invalid owner, bbox, projection, simplification or page request."""


class VectorLayerNotFoundError(LookupError):
    """Unknown catalog ID or owner not found in the requested project."""


class VectorLayerNotReadyError(RuntimeError):
    """A run-owned derived projection has no published read model."""


@dataclass(frozen=True, slots=True)
class VectorLayerContext:
    working_srid: int
    read_model_ready: bool = True
    # Only the MVT adapter may attest published DB-backed cache immutability.
    # The generalized GeoJSON adapter never makes that caching claim.
    immutable: bool = False


@dataclass(frozen=True, slots=True)
class VectorFeature:
    id: uuid.UUID
    geometry: dict[str, object]
    properties: dict[str, object]
    type: Literal["Feature"] = field(init=False, default="Feature")


@dataclass(frozen=True, slots=True)
class VectorPage:
    project_id: uuid.UUID
    layer_id: str
    definition_version: str
    dataset_version_id: uuid.UUID | None
    run_id: uuid.UUID | None
    working_srid: int
    query_bbox: tuple[float, float, float, float]
    presentation_crs: Literal["EPSG:4326"]
    simplify_m: float
    limit: int
    truncated: bool
    next_after: uuid.UUID | None
    features: tuple[VectorFeature, ...]
    schema_version: str = VECTOR_API_SCHEMA_VERSION
    type: Literal["FeatureCollection"] = field(init=False, default="FeatureCollection")


class VectorLayerRepository(Protocol):
    def get_context(self, entry: LayerCatalogEntry) -> VectorLayerContext | None: ...

    def list_features(
        self,
        *,
        entry: LayerCatalogEntry,
        working_srid: int,
        bbox: SourceLayerBbox,
        after: uuid.UUID | None,
        limit: int,
        simplify_m: float,
    ) -> list[VectorFeature]: ...


class VectorLayerQueryService:
    """Bounded query policy and exact catalog owner selection without SQL."""

    def __init__(self, repository: VectorLayerRepository) -> None:
        self._repository = repository

    def get_geojson(
        self,
        *,
        project_id: uuid.UUID,
        layer_id: str,
        dataset_version_id: uuid.UUID | None,
        run_id: uuid.UUID | None,
        bbox_text: str,
        limit: int = DEFAULT_VECTOR_LIMIT,
        after: uuid.UUID | None = None,
        presentation_srid: int = VECTOR_PRESENTATION_SRID,
        simplify_m: float = 0.0,
    ) -> VectorPage:
        known = next(
            (value for value in CANONICAL_LAYER_DEFINITIONS if value.layer_id == layer_id),
            None,
        )
        if known is None:
            raise VectorLayerNotFoundError("Unknown canonical layer")
        if (
            known.delivery_kind is not LayerDeliveryKind.BBOX_GEOJSON
            or layer_id in _UNSUPPORTED_VECTOR_IDS
        ):
            raise VectorLayerQueryError(
                "Layer has no table-backed UUID keyset viewport; use its canonical read API"
            )
        if known.owner_scope is LayerOwnerScope.DATASET_VERSION:
            if dataset_version_id is None or run_id is not None:
                raise VectorLayerQueryError(
                    "source layer requires dataset_version_id only (not run_id)"
                )
        elif known.owner_scope is LayerOwnerScope.RUN:
            if run_id is None or dataset_version_id is not None:
                raise VectorLayerQueryError(
                    "run layer requires run_id only (not dataset_version_id)"
                )
        else:
            raise VectorLayerQueryError("Layer is not dataset/run-scoped")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise VectorLayerQueryError("limit must be an integer")
        if limit < 1 or limit > min(MAX_VECTOR_LIMIT, known.max_features or 0):
            raise VectorLayerQueryError(
                f"limit must be between 1 and {min(MAX_VECTOR_LIMIT, known.max_features or 0)}"
            )
        if after is not None and not isinstance(after, uuid.UUID):
            raise VectorLayerQueryError("after must be a UUID")
        if (
            isinstance(presentation_srid, bool)
            or not isinstance(presentation_srid, int)
            or presentation_srid != VECTOR_PRESENTATION_SRID
        ):
            raise VectorLayerQueryError(
                "GeoJSON presentation_srid must be EPSG:4326 (RFC 7946)"
            )
        if (
            isinstance(simplify_m, bool)
            or not isinstance(simplify_m, (int, float))
            or not math.isfinite(simplify_m)
            or not 0.0 <= simplify_m <= MAX_SIMPLIFY_METRES
        ):
            raise VectorLayerQueryError(
                f"simplify_m must be a finite metre tolerance between 0 and {MAX_SIMPLIFY_METRES:g}"
            )
        try:
            bbox = SourceLayerBbox.parse(bbox_text)
        except SourceLayerQueryError as exc:
            raise VectorLayerQueryError(str(exc)) from exc

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
        if simplify_m > 0:
            try:
                require_working_crs(context.working_srid)
            except CRSContractError as exc:
                raise VectorLayerQueryError(
                    "simplify_m requires a projected metre-unit working CRS"
                ) from exc

        rows = self._repository.list_features(
            entry=entry,
            working_srid=context.working_srid,
            bbox=bbox,
            after=after,
            limit=limit + 1,
            simplify_m=float(simplify_m),
        )
        truncated = len(rows) > limit
        features = tuple(rows[:limit])
        return VectorPage(
            project_id=project_id,
            layer_id=layer_id,
            definition_version=entry.definition.definition_version,
            dataset_version_id=dataset_version_id,
            run_id=run_id,
            working_srid=context.working_srid,
            query_bbox=bbox.tuple,
            presentation_crs=VECTOR_PRESENTATION_CRS,
            simplify_m=float(simplify_m),
            limit=limit,
            truncated=truncated,
            next_after=features[-1].id if truncated else None,
            features=features,
        )
