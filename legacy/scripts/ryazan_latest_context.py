from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from backend.app.application.source_layers import SourceLayerBbox, SourceLayerName
from backend.app.core.config import settings
from backend.app.db.session import SessionLocal
from backend.app.db.source_layer_query_repository import SqlAlchemySourceLayerQueryRepository
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.project import Project

RYAZAN_BBOX = SourceLayerBbox(39.50, 54.50, 39.95, 54.75)


def main() -> None:
    with SessionLocal() as session:
        statement = (
            select(Project.id, DatasetVersion.id)
            .select_from(DatasetVersion)
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .join(Project, Project.id == Dataset.project_id)
            .where(
                Project.name.like("Ryazan%"),
                DatasetVersion.status == "ready",
            )
            .order_by(DatasetVersion.created_at.desc())
            .limit(1)
        )
        row = session.execute(statement).one_or_none()
        if row is None:
            raise RuntimeError("no READY Ryazan dataset version found")

        project_id, dataset_version_id = row
        repository = SqlAlchemySourceLayerQueryRepository(session)
        context = repository.get_context(
            project_id=project_id,
            dataset_version_id=dataset_version_id,
        )
        if context is None:
            raise RuntimeError("latest Ryazan dataset is not attached to its project")

        boundary = repository.get_project_boundary(project_id=project_id)
        if boundary is None or boundary.geometry is None:
            raise RuntimeError("latest Ryazan project has no map boundary")

        buildings = repository.list_features(
            dataset_version_id=dataset_version_id,
            layer=SourceLayerName.BUILDINGS,
            working_srid=context.working_srid,
            bbox=RYAZAN_BBOX,
            limit=1,
        )
        if not buildings:
            raise RuntimeError("latest Ryazan dataset has no buildings inside the import bbox")

    url = (
        "http://localhost:5173/"
        f"?project_id={project_id}"
        f"&dataset_version_id={dataset_version_id}"
    )
    output_dir = Path(settings.storage_root) / "imports"
    output_dir.mkdir(parents=True, exist_ok=True)
    context_file = output_dir / "last_ryazan_context.env"
    context_file.write_text(
        "\n".join(
            (
                f"PROJECT_ID={project_id}",
                f"DATASET_VERSION_ID={dataset_version_id}",
                f"OPEN_URL={url}",
                "",
            )
        ),
        encoding="utf-8",
    )

    print("Viewer preflight passed:")
    print(f"  project: {project_id}")
    print(f"  dataset version: {dataset_version_id}")
    print("  boundary: available")
    print("  buildings in Ryazan bbox: available")
    print(f"  OPEN={url}")


if __name__ == "__main__":
    main()
