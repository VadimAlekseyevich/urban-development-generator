from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.application.projects import ProjectService
from backend.app.db.project_repository import SqlAlchemyProjectRepository
from backend.app.db.session import get_db

DbSession = Annotated[Session, Depends(get_db)]


def get_project_service(db: DbSession) -> ProjectService:
    """Compose the project application service for one request-scoped DB session."""

    return ProjectService(SqlAlchemyProjectRepository(db))


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]
