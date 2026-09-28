"""S12-T12: one canonical manifest from immutable PostgreSQL provenance."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable

import pytest
from alembic import command
from alembic.config import Config
from geoalchemy2.elements import WKTElement
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.provenance_manifest import (
    ProvenanceManifestError,
    SqlAlchemyProvenanceManifestService,
)
from backend.app.db.run_metrics_writer import composite_score_payload
from backend.app.db.session import engine
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.dataset import Dataset, DatasetVersion
from backend.app.models.generation_run import GenerationRun
from backend.app.models.project import Project
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain.benchmarking import RawMetricId
from core.urban_generator.metrics.score import (
    CompositeScoreMetricResult,
    CompositeScoreResult,
)

SessionFactory: Callable[[], Session] = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False
)
WORKING_SRID = 32637


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE projects, artifacts CASCADE"))


def _evaluation() -> dict[str, object]:
    result = CompositeScoreResult(
        score=0.4,
        score_config_id="fixture",
        score_config_version="1",
        normalization_profile_id="fixture",
        normalization_profile_version="1",
        metrics=(
            CompositeScoreMetricResult(
                metric_id=RawMetricId.LAND_DEVELOPED_AREA_M2,
                raw_value=250.0,
                normalized_value=0.4,
                normalization_policy_version="1",
                configured_weight=1.0,
                normalized_weight=1.0,
                contribution=0.4,
                was_clamped=False,
                was_missing=False,
            ),
        ),
    )
    return {"evaluation": composite_score_payload(result)}


def _seed(
    *,
    status: str = "succeeded",
    evaluation: bool = True,
    fingerprint: bool = True,
    bad_stage_owner: bool = False,
    bad_dataset_checksum: bool = False,
) -> uuid.UUID:
    with SessionFactory() as session:
        project = Project(
            name="Provenance fixture",
            working_srid=WORKING_SRID,
            boundary_metadata={},
            boundary=WKTElement(
                "MULTIPOLYGON(((0 0, 10 0, 10 10, 0 10, 0 0)))",
                srid=WORKING_SRID,
            ),
        )
        session.add(project)
        session.flush()
        dataset = Dataset(project_id=project.id, kind="roads")
        session.add(dataset)
        session.flush()
        version = DatasetVersion(
            dataset_id=dataset.id,
            version=1,
            status="ready",
            checksum_sha256="a" * 64,
            source_metadata={
                "artifact_key": f"uploads/{dataset.id.hex}/source.geojson",
                "untrusted_filename": "/private/input/source.geojson",
            },
        )
        session.add(version)
        session.flush()
        session.add(
            Artifact(
                uri=f"artifact://uploads/{dataset.id.hex}/source.geojson",
                checksum=f"sha256:{'b' * 64}" if bad_dataset_checksum else f"sha256:{'a' * 64}",
                size_bytes=123,
                content_type="application/geo+json",
                state=ArtifactLifecycleState.REFERENCED.value,
                owner_type="dataset_version",
                owner_id=version.id,
            )
        )
        run = GenerationRun(
            project_id=project.id,
            mode="EXPANSION",
            status="running",
            seed=42,
            working_srid=WORKING_SRID,
            config_json={"б": "тест", "a": {"z": 2, "x": 1}},
            config_schema_version="fixture-v1",
            commit_sha="c" * 40,
            dataset_versions=[version],
            metrics_json=_evaluation() if evaluation else None,
        )
        session.add(run)
        session.flush()
        # Deliberately add stages in reverse lexical order. The manifest's
        # order must not follow SQL insertion or the relation load order.
        zoning = RunStageResult(
            run_id=run.id,
            stage_name="zoning",
            stage_version="1",
            status="skipped",
            progress_percent=100,
            input_hash=f"sha256:{'d' * 64}",
            config_hash=f"sha256:{'e' * 64}",
            output_fingerprint=None,
            diagnostics_json=[],
            artifact_refs_json=[],
        )
        roads = RunStageResult(
            run_id=run.id,
            stage_name="roads",
            stage_version="1",
            status="succeeded",
            progress_percent=100,
            input_hash=f"sha256:{'f' * 64}",
            config_hash=f"sha256:{'0' * 64}",
            output_fingerprint=f"sha256:{'1' * 64}" if fingerprint else None,
            diagnostics_json=[],
            artifact_refs_json=[],
        )
        session.add_all([zoning, roads])
        session.flush()
        produced = Artifact(
            uri=f"artifact://runs/{run.id}/stages/roads/result.geojson",
            checksum=f"sha256:{'2' * 64}",
            size_bytes=30,
            content_type="application/geo+json",
            state=ArtifactLifecycleState.REFERENCED.value,
            owner_type="run_stage_result",
            owner_id=uuid.uuid4() if bad_stage_owner else roads.id,
        )
        session.add(produced)
        roads.artifacts.append(produced)
        if status == "succeeded":
            run.status = "succeeded"
        elif status != "running":
            raise ValueError("unsupported fixture status")
        session.commit()
        return run.id


def _service() -> SqlAlchemyProvenanceManifestService:
    return SqlAlchemyProvenanceManifestService(session_factory=SessionFactory)


def test_manifest_is_canonical_reproducible_and_does_not_mutate_run() -> None:
    run_id = _seed()
    manifest = _service().build(run_id=run_id)
    replay = _service().build(run_id=run_id)

    assert manifest.run_id == run_id
    assert manifest.content == replay.content
    assert manifest.checksum == replay.checksum
    assert manifest.checksum == "sha256:" + hashlib.sha256(manifest.content).hexdigest()
    assert manifest.content == json.dumps(
        manifest.as_dict(),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    value = manifest.as_dict()
    assert value["schema_version"] == "1"
    assert value["run"]["id"] == str(run_id)
    assert value["run"]["seed"] == 42
    assert value["run"]["working_srid"] == WORKING_SRID
    assert value["code"] == {"commit_sha": "c" * 40}
    config_bytes = '{"a":{"x":1,"z":2},"б":"тест"}'.encode("utf-8")
    assert value["config"]["checksum"] == "sha256:" + hashlib.sha256(
        config_bytes
    ).hexdigest()
    assert value["config"]["value"] == {"a": {"x": 1, "z": 2}, "б": "тест"}
    assert len(value["datasets"]) == 1
    source = value["datasets"][0]
    assert source["checksum"] == f"sha256:{'a' * 64}"
    assert source["source_artifact"]["checksum"] == source["checksum"]
    assert "untrusted_filename" not in manifest.content.decode("utf-8")
    assert [stage["name"] for stage in value["stages"]] == ["roads", "zoning"]
    assert value["stages"][0]["output_fingerprint"] == f"sha256:{'1' * 64}"
    assert value["stages"][0]["artifacts"][0]["checksum"] == f"sha256:{'2' * 64}"
    assert value["stages"][1]["output_fingerprint"] is None
    assert value["stages"][1]["artifacts"] == []
    assert value["evaluation"]["raw_metrics"] == [
        {
            "metric_id": "land.developed_area_m2",
            "definition_version": "1",
            "unit": "m2",
            "raw_value": 250.0,
        }
    ]
    value["stages"].clear()
    assert len(manifest.as_dict()["stages"]) == 2
    with SessionFactory() as session:
        run = session.get(GenerationRun, run_id)
        assert run is not None
        assert run.status == "succeeded"
        assert run.metrics_json == _evaluation()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"evaluation": False}, "canonical persisted evaluation"),
        ({"fingerprint": False}, "output fingerprint"),
        ({"bad_stage_owner": True}, "referenced run_stage_result"),
        ({"bad_dataset_checksum": True}, "checksum disagrees"),
        ({"status": "running"}, "successful generation run"),
    ],
)
def test_manifest_rejects_incomplete_or_misowned_provenance(
    kwargs: dict[str, object], expected: str
) -> None:
    run_id = _seed(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(ProvenanceManifestError, match=expected):
        _service().build(run_id=run_id)


def test_manifest_rejects_missing_run_without_side_effects() -> None:
    with pytest.raises(ProvenanceManifestError, match="successful generation run"):
        _service().build(run_id=uuid.uuid4())
