"""Prepare a small REAL PostGIS/metrics fixture for browser E2E on an ephemeral stack.

This is not a user ingestion path: a successful upload only creates an Artifact.
The E2E test uses this isolated seed to expose a ready DatasetVersion and two
already-evaluated runs through the actual API, never mock fetch responses.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid

from geoalchemy2.shape import from_shape
from pyproj import Transformer
from shapely.geometry import MultiLineString
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.run_metrics_writer import composite_score_payload
from backend.app.db.session import engine
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.source_layer import SourceRoad
from core.urban_generator.domain import ValidationReport, serialize_validation_report
from core.urban_generator.domain.benchmarking import RawMetricId
from core.urban_generator.metrics.score import (
    CompositeScoreMetricResult,
    CompositeScoreResult,
)

WORKING_SRID = 32637
COMMIT_SHA = "a" * 40
BOUNDARY = (
    "MULTIPOLYGON(((37.60 55.74,37.64 55.74,37.64 55.77,"
    "37.60 55.77,37.60 55.74)))"
)


def _evaluation(score: float, road_components: float) -> dict[str, object]:
    result = CompositeScoreResult(
        score=score,
        score_config_id="browser-e2e",
        score_config_version="1",
        normalization_profile_id="browser-e2e",
        normalization_profile_version="1",
        metrics=(
            CompositeScoreMetricResult(
                metric_id=RawMetricId.LAND_DEVELOPED_AREA_M2,
                raw_value=125000.0,
                normalized_value=score,
                normalization_policy_version="1",
                configured_weight=0.5,
                normalized_weight=0.5,
                contribution=score / 2,
                was_clamped=False,
                was_missing=False,
            ),
            CompositeScoreMetricResult(
                metric_id=RawMetricId.ROADS_CONNECTED_COMPONENTS,
                raw_value=road_components,
                normalized_value=score,
                normalization_policy_version="1",
                configured_weight=0.5,
                normalized_weight=0.5,
                contribution=score / 2,
                was_clamped=False,
                was_missing=False,
            ),
        ),
    )
    return {"evaluation": composite_score_payload(result)}


def seed(project_id: uuid.UUID) -> dict[str, object]:
    if os.environ.get("UDG_E2E_FIXTURE") != "1":
        raise RuntimeError("fixture seed is disabled unless UDG_E2E_FIXTURE=1")

    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            project = session.get(Project, project_id)
            if project is None or not project.name.startswith("E2E "):
                raise ValueError("fixture project must exist and have the E2E prefix")
            if project.working_srid != WORKING_SRID:
                raise ValueError("fixture requires the 32637 metric CRS")
            if session.query(Dataset).filter_by(project_id=project_id).first() is not None:
                raise ValueError("fixture project already has a dataset")

            session.execute(
                text(
                    "UPDATE projects "
                    "SET boundary = ST_Transform(ST_GeomFromText(:wkt,4326),:srid) "
                    "WHERE id = :project_id"
                ),
                {"wkt": BOUNDARY, "srid": WORKING_SRID, "project_id": project_id},
            )
            # Refresh after a SQL update so ORM run validation sees the actual boundary.
            session.expire(project, ["boundary"])

            dataset = Dataset(project_id=project_id, kind="roads")
            session.add(dataset)
            session.flush()
            version = DatasetVersion(
                dataset_id=dataset.id,
                version=1,
                status="processing",
                source_metadata={"source": "isolated-e2e-fixture"},
            )
            session.add(version)
            session.flush()

            converter = Transformer.from_crs(4326, WORKING_SRID, always_xy=True)
            road_coordinates = [
                converter.transform(x, y)
                for x, y in [(37.605, 55.755), (37.635, 55.755)]
            ]
            session.add(
                SourceRoad(
                    dataset_version_id=version.id,
                    source_feature_id="e2e-fixture-road",
                    road_class="local",
                    name="E2E Avenue",
                    lanes=2,
                    one_way=False,
                    attributes_json={"fixture": True},
                    geometry=from_shape(
                        MultiLineString([road_coordinates]),
                        srid=WORKING_SRID,
                        extended=True,
                    ),
                )
            )
            session.flush()
            version.status = "ready"
            session.flush()

            validation = json.loads(
                serialize_validation_report(ValidationReport(results=()))
            )
            run_ids: list[str] = []
            for index, (score, components) in enumerate(((0.72, 2.0), (0.86, 1.0))):
                run = GenerationRun(
                    project_id=project_id,
                    status="running",
                    mode="EXPANSION",
                    seed=71 + index,
                    working_srid=WORKING_SRID,
                    config_json={},
                    config_schema_version="1",
                    commit_sha=COMMIT_SHA,
                    metrics_json=_evaluation(score, components),
                    validation_json=validation,
                    dataset_versions=[version],
                )
                session.add(run)
                session.flush()
                run.status = "succeeded"
                session.flush()
                run_ids.append(str(run.id))

            return {"version_id": str(version.id), "run_ids": run_ids}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=uuid.UUID, required=True)
    args = parser.parse_args()
    print(json.dumps(seed(args.project_id)))
