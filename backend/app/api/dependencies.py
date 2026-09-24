from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.adapters import LocalArtifactStore
from backend.app.application.block_parcel_layers import BlockParcelLayerQueryService
from backend.app.application.building_layers import BuildingLayerQueryService
from backend.app.application.demography_layers import DemographyLayerQueryService
from backend.app.application.infrastructure_layers import InfrastructureLayerQueryService
from backend.app.application.projects import ProjectService
from backend.app.application.road_layers import RoadLayerQueryService
from backend.app.application.source_layers import SourceLayerQueryService
from backend.app.application.suitability_layers import SuitabilityLayerService
from backend.app.application.uploads import UploadService
from backend.app.application.validation_layers import ValidationLayerQueryService
from backend.app.application.zoning_layers import ZoningLayerQueryService
from backend.app.core.config import settings
from backend.app.db.artifact_repository import SqlAlchemyArtifactRepository
from backend.app.db.block_parcel_layer_query_repository import (
    SqlAlchemyBlockParcelLayerQueryRepository,
)
from backend.app.db.building_layer_query_repository import (
    SqlAlchemyBuildingLayerQueryRepository,
)
from backend.app.db.demography_layer_query_repository import (
    SqlAlchemyDemographyLayerQueryRepository,
)
from backend.app.db.infrastructure_layer_query_repository import (
    SqlAlchemyInfrastructureLayerQueryRepository,
)
from backend.app.db.project_repository import SqlAlchemyProjectRepository
from backend.app.db.road_layer_query_repository import SqlAlchemyRoadLayerQueryRepository
from backend.app.db.session import get_db
from backend.app.db.source_layer_query_repository import (
    SqlAlchemySourceLayerQueryRepository,
)
from backend.app.db.suitability_artifact_repository import (
    SqlAlchemySuitabilityArtifactRepository,
)
from backend.app.db.validation_layer_query_repository import (
    SqlAlchemyValidationLayerQueryRepository,
)
from backend.app.db.zoning_layer_query_repository import (
    SqlAlchemyZoningLayerQueryRepository,
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


def get_road_layer_query_service(db: DbSession) -> RoadLayerQueryService:
    """Compose the generated-road viewport and diagnostics query service."""

    return RoadLayerQueryService(SqlAlchemyRoadLayerQueryRepository(db))


RoadLayerQueryServiceDep = Annotated[
    RoadLayerQueryService,
    Depends(get_road_layer_query_service),
]


def get_block_parcel_layer_query_service(
    db: DbSession,
) -> BlockParcelLayerQueryService:
    """Compose the generated block/parcel viewport query service."""

    return BlockParcelLayerQueryService(
        SqlAlchemyBlockParcelLayerQueryRepository(db)
    )


BlockParcelLayerQueryServiceDep = Annotated[
    BlockParcelLayerQueryService,
    Depends(get_block_parcel_layer_query_service),
]


def get_building_layer_query_service(
    db: DbSession,
) -> BuildingLayerQueryService:
    """Compose the generated-building viewport query service."""

    return BuildingLayerQueryService(SqlAlchemyBuildingLayerQueryRepository(db))


BuildingLayerQueryServiceDep = Annotated[
    BuildingLayerQueryService,
    Depends(get_building_layer_query_service),
]


def get_demography_layer_query_service(
    db: DbSession,
) -> DemographyLayerQueryService:
    """Compose the S09 demographic metrics and choropleth query service."""

    return DemographyLayerQueryService(
        SqlAlchemyDemographyLayerQueryRepository(db)
    )


DemographyLayerQueryServiceDep = Annotated[
    DemographyLayerQueryService,
    Depends(get_demography_layer_query_service),
]


def get_infrastructure_layer_query_service(
    db: DbSession,
) -> InfrastructureLayerQueryService:
    """Compose the S10 run-scoped infrastructure viewport query service."""

    return InfrastructureLayerQueryService(
        SqlAlchemyInfrastructureLayerQueryRepository(db)
    )


InfrastructureLayerQueryServiceDep = Annotated[
    InfrastructureLayerQueryService,
    Depends(get_infrastructure_layer_query_service),
]


def get_validation_layer_query_service(
    db: DbSession,
) -> ValidationLayerQueryService:
    """Compose the S11 run-scoped validation layer query service."""

    return ValidationLayerQueryService(
        SqlAlchemyValidationLayerQueryRepository(db)
    )


ValidationLayerQueryServiceDep = Annotated[
    ValidationLayerQueryService,
    Depends(get_validation_layer_query_service),
]


def get_zoning_layer_query_service(db: DbSession) -> ZoningLayerQueryService:
    """Compose the bounded zoning-layer viewport query service."""

    return ZoningLayerQueryService(SqlAlchemyZoningLayerQueryRepository(db))


ZoningLayerQueryServiceDep = Annotated[
    ZoningLayerQueryService,
    Depends(get_zoning_layer_query_service),
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


def get_suitability_layer_service(
    db: DbSession,
    store: ArtifactStoreDep,
) -> SuitabilityLayerService:
    """Compose read-only suitability artifact inspection and preview rendering."""

    return SuitabilityLayerService(
        store,
        SqlAlchemySuitabilityArtifactRepository(db),
    )


SuitabilityLayerServiceDep = Annotated[
    SuitabilityLayerService,
    Depends(get_suitability_layer_service),
]
