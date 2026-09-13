from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.adapters import LocalArtifactStore
from backend.app.application.projects import ProjectService
from backend.app.application.source_layers import SourceLayerQueryService
from backend.app.application.uploads import UploadService
from backend.app.core.config import settings
from backend.app.db.artifact_repository import SqlAlchemyArtifactRepository
from backend.app.db.project_repository import SqlAlchemyProjectRepository
from backend.app.db.session import get_db
from backend.app.db.source_layer_query_repository import (
    SqlAlchemySourceLayerQueryRepository,
)
from core.urban_generator.domain import ArtifactStore

DbSession = Annotated[Session, Depends(get_db)]


def get_project_service(db: DbSession) -> ProjectService:
    """Compose the project application service for one request-scoped DB session."""

    return ProjectService(SqlAlchemyProjectRepository(db))


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


def get_source_layer_query_service(db: DbSession) -> SourceLayerQueryService:
    """Compose the bounded source-layer viewport query service."""

    return SourceLayerQueryService(SqlAlchemySourceLayerQueryRepository(db))


SourceLayerQueryServiceDep = Annotated[
    SourceLayerQueryService,
    Depends(get_source_layer_query_service),
]


def get_artifact_store() -> ArtifactStore:
    """Compose the configured artifact storage adapter."""

    return LocalArtifactStore(settings.storage_root)


ArtifactStoreDep = Annotated[ArtifactStore, Depends(get_artifact_store)]


def get_upload_service(
    db: DbSession,
    store: ArtifactStoreDep,
) -> UploadService:
    """Compose bounded upload orchestration for one request."""

    return UploadService(
        store,
        SqlAlchemyArtifactRepository(db),
        max_size_bytes=settings.max_upload_size_mb * 1024 * 1024,
    )


UploadServiceDep = Annotated[UploadService, Depends(get_upload_service)]
