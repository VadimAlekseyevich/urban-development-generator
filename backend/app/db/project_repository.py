import math
import uuid

from geoalchemy2.shape import from_shape
from pyproj import Transformer
from pyproj.exceptions import ProjError
from shapely.geometry import MultiPolygon
from shapely.ops import transform
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.projects import ProjectBoundaryLockedError
from backend.app.models.generation_run import GenerationRun
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

    def set_boundary(self, project_id: uuid.UUID, geometry: MultiPolygon) -> Project | None:
        """Atomically publish a fixed metric boundary only before the first run.

        Locking the project row conflicts with the FK key-share acquired by
        concurrent run insertion. A run can therefore never be based on a
        boundary that changed between the run's creation and this commit.
        """
        try:
            project = self._session.scalar(
                select(Project).where(Project.id == project_id).with_for_update()
            )
            if project is None:
                self._session.rollback()
                return None
            existing_run_id = self._session.scalar(
                select(GenerationRun.id)
                .where(GenerationRun.project_id == project_id)
                .limit(1)
            )
            if existing_run_id is not None:
                raise ProjectBoundaryLockedError(
                    "project boundary cannot change after any generation run exists"
                )
            try:
                project_to_working = Transformer.from_crs(
                    4326, project.working_srid, always_xy=True
                )
                projected = transform(project_to_working.transform, geometry)
            except (ProjError, ValueError) as exc:
                raise ValueError("unable to transform boundary into project CRS") from exc
            if (
                projected.is_empty or not projected.is_valid or projected.area <= 0
                or not all(math.isfinite(value) for value in projected.bounds)
            ):
                raise ValueError("boundary is invalid in project working CRS")
            project.boundary = from_shape(
                projected, srid=project.working_srid, extended=True
            )
            project.boundary_metadata = {
                "source_srid": 4326,
                "geometry_type": "MULTIPOLYGON",
                "feature_count": len(geometry.geoms),
            }
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(project)
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
