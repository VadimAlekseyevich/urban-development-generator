"""Verify the actual SQL version catalog against Postgres ownership and paging."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.dataset_version_query_repository import SqlAlchemyDatasetVersionReader
from backend.app.db.session import engine
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.project import Project


@pytest.fixture()
def session():
    command.upgrade(Config("alembic.ini"), "head")
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    with Session(engine, expire_on_commit=False) as db:
        yield db
        db.rollback()
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def test_dataset_versions_sql_catalog_is_bounded_project_scoped_and_sorted(
    session: Session,
) -> None:
    project_a = Project(name="A", working_srid=32637, boundary_metadata={})
    project_b = Project(name="B", working_srid=32637, boundary_metadata={})
    session.add_all([project_a, project_b])
    session.flush()

    roads = Dataset(project_id=project_a.id, kind="roads")
    buildings = Dataset(project_id=project_b.id, kind="buildings")
    session.add_all([roads, buildings])
    session.flush()

    now = datetime(2026, 10, 10, tzinfo=UTC)
    first = DatasetVersion(
        dataset_id=roads.id, version=1, status="ready",
        source_metadata={}, created_at=now - timedelta(days=1),
    )
    latest = DatasetVersion(
        dataset_id=roads.id, version=2, status="processing",
        source_metadata={}, created_at=now,
    )
    other = DatasetVersion(
        dataset_id=buildings.id, version=1, status="ready",
        source_metadata={}, created_at=now + timedelta(days=1),
    )
    session.add_all([first, latest, other])
    session.commit()

    reader = SqlAlchemyDatasetVersionReader(session)
    page_one = reader.list_versions(project_id=project_a.id, limit=1, offset=0)
    assert page_one is not None
    assert page_one.truncated is True
    assert len(page_one.versions) == 1
    assert page_one.versions[0].id == latest.id
    assert page_one.versions[0].status == "processing"
    assert page_one.versions[0].dataset_kind == "roads"

    page_two = reader.list_versions(project_id=project_a.id, limit=1, offset=1)
    assert page_two is not None
    assert page_two.truncated is False
    assert [version.id for version in page_two.versions] == [first.id]
    assert reader.list_versions(project_id=uuid.uuid4(), limit=1, offset=0) is None

    foreign_page = reader.list_versions(project_id=project_b.id, limit=20, offset=0)
    assert foreign_page is not None
    assert [version.id for version in foreign_page.versions] == [other.id]
