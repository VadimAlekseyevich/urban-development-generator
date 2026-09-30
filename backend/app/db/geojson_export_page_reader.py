from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.app.application.geojson_exports import GeoJsonExportJobContext
from backend.app.application.vector_layers import VectorLayerQueryService, VectorPage
from backend.app.db.session import SessionLocal
from backend.app.db.vector_layer_query_repository import SqlAlchemyVectorLayerRepository


class SqlAlchemyGeoJsonExportPageReader:
    """Open one bounded DB session per keyset page; no long export transaction."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._session_factory = session_factory

    def get_page(
        self,
        *,
        context: GeoJsonExportJobContext,
        after: uuid.UUID | None,
        limit: int,
    ) -> VectorPage:
        with self._session_factory() as session:
            return VectorLayerQueryService(
                SqlAlchemyVectorLayerRepository(session)
            ).get_geojson(
                project_id=context.project_id,
                layer_id=context.spec.layer_id,
                dataset_version_id=context.spec.dataset_version_id,
                run_id=context.spec.run_id,
                bbox_text=",".join(str(value) for value in context.spec.bbox),
                limit=limit,
                after=after,
                presentation_srid=4326,
                simplify_m=0.0,
            )
