import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.project import Project


class SqlAlchemyProjectRepository:
    """SQLAlchemy adapter for project persistence and transaction boundaries."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, *, limit: int, offset: int) -> list[Project]:
        statement = (
            select(Project)
            .order_by(Project.created_at.desc(), Project.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(statement).all())

    def get(self, project_id: uuid.UUID) -> Project | None:
        return self._session.get(Project, project_id)

    def create(self, project: Project) -> Project:
        self._session.add(project)
        self._commit_and_refresh(project)
        return project

    def save(self, project: Project) -> Project:
        self._commit_and_refresh(project)
        return project

    def delete(self, project: Project) -> None:
        self._session.delete(project)
        self._session.commit()

    def _commit_and_refresh(self, project: Project) -> None:
        self._session.commit()
        self._session.refresh(project)
