import pytest
from pydantic import ValidationError

from backend.app.models.project import Project
from backend.app.schemas.project import BoundaryMetadata, ProjectCreate, ProjectUpdate


def test_project_persistence_model_uses_explicit_working_srid() -> None:
    columns = Project.__table__.c

    assert "working_srid" in columns
    assert "working_crs" not in columns
    assert columns.working_srid.nullable is False
    assert columns.boundary_metadata.nullable is False


def test_project_create_accepts_metric_working_srid_and_boundary_metadata() -> None:
    payload = ProjectCreate(
        name="Demo project",
        working_srid=32637,
        boundary_metadata=BoundaryMetadata(
            source_srid=4326,
            geometry_type="MULTIPOLYGON",
            feature_count=1,
        ),
    )

    assert payload.working_srid == 32637
    assert payload.boundary_metadata.source_srid == 4326


def test_project_create_rejects_geographic_working_srid() -> None:
    with pytest.raises(ValidationError, match="metric operations require"):
        ProjectCreate(name="Invalid CRS", working_srid=4326)


def test_project_update_is_partial_but_rejects_null_required_fields() -> None:
    update = ProjectUpdate(name="Renamed")
    assert update.model_dump(exclude_unset=True) == {"name": "Renamed"}

    with pytest.raises(ValidationError, match="working_srid cannot be null"):
        ProjectUpdate(working_srid=None)

    with pytest.raises(ValidationError, match="name cannot be null"):
        ProjectUpdate(name=None)
