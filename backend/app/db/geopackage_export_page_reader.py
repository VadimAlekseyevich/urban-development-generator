from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.app.application.geopackage_exports import GeoPackageExportJobContext
from backend.app.application.layer_catalog import CANONICAL_LAYER_DEFINITIONS, LayerOwnerScope
from backend.app.application.vector_layers import VectorLayerQueryService, VectorPage
from backend.app.db.session import SessionLocal
from backend.app.db.vector_layer_query_repository import SqlAlchemyVectorLayerRepository

_DEFINITION_BY_ID = {
    definition.layer_id: definition for definition in CANONICAL_LAYER_DEFINITIONS
}


class SqlAlchemyGeoPackageExportPageReader:
    """Open one bounded DB session per layer keyset page."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._session_factory = session_factory

    def get_page(
        self,
        *,
        context: GeoPackageExportJobContext,
        layer_id: str,
        after: uuid.UUID | None,
        limit: int,
    ) -> VectorPage:
        definition = _DEFINITION_BY_ID[layer_id]
        dataset_version_id = (
            context.spec.dataset_version_id
            if definition.owner_scope is LayerOwnerScope.DATASET_VERSION
            else None
        )
        run_id = (
            context.spec.run_id
            if definition.owner_scope is LayerOwnerScope.RUN
            else None
        )
        with self._session_factory() as session:
            return VectorLayerQueryService(
                SqlAlchemyVectorLayerRepository(session)
            ).get_geojson(
                project_id=context.project_id,
                layer_id=layer_id,
                dataset_version_id=dataset_version_id,
                run_id=run_id,
                bbox_text=",".join(str(value) for value in context.spec.bbox),
                limit=limit,
                after=after,
                presentation_srid=4326,
                simplify_m=0.0,
            )
