"""Pure contract tests for project-scoped canonical provenance export."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

import pytest

from backend.app.application.provenance_exports import (
    ProvenanceExportDataError,
    ProvenanceExportProjectNotFoundError,
    ProvenanceExportRunNotFoundError,
    ProvenanceExportRunRecord,
    ProvenanceExportService,
    ProvenanceExportUnavailableError,
)

PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
RUN_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
DATASET_VERSION_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


@dataclass(frozen=True, slots=True)
class FakeManifest:
    run_id: uuid.UUID
    content: bytes
    checksum: str


class FakeRepository:
    def __init__(
        self,
        record: ProvenanceExportRunRecord | None,
        *,
        project_exists: bool = True,
    ) -> None:
        self.record = record
        self.exists = project_exists

    def project_exists(self, *, project_id):
        return self.exists

    def get_run(self, *, project_id, run_id):
        return self.record


class FakeBuilder:
    def __init__(self, manifest: FakeManifest) -> None:
        self.manifest = manifest
        self.calls = 0

    def build(self, *, run_id):
        self.calls += 1
        return self.manifest


def _manifest(
    *,
    project_id: uuid.UUID = PROJECT_ID,
    run_id: uuid.UUID = RUN_ID,
    seed: object = 42,
    working_srid: object = 32637,
    config: object | None = None,
    datasets: object | None = None,
) -> FakeManifest:
    payload = {
        "schema_version": "1",
        "run": {
            "id": str(run_id),
            "project_id": str(project_id),
            "mode": "EXPANSION",
            "seed": seed,
            "working_srid": working_srid,
            "status": "succeeded",
            "rerun_source_id": None,
        },
        "code": {"commit_sha": "a" * 40},
        "config": config
        if config is not None
        else {
            "schema_version": "fixture-v1",
            "checksum": f"sha256:{'b' * 64}",
            "value": {"density": {"target": 1.25}},
        },
        "datasets": datasets
        if datasets is not None
        else [{"dataset_version_id": str(DATASET_VERSION_ID)}],
        "stages": [],
        "evaluation": {},
    }
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return FakeManifest(
        run_id=run_id,
        content=content,
        checksum="sha256:" + hashlib.sha256(content).hexdigest(),
    )


def _service(
    *,
    status: str = "succeeded",
    manifest: FakeManifest | None = None,
    project_exists: bool = True,
):
    builder = FakeBuilder(manifest or _manifest())
    repository = FakeRepository(
        ProvenanceExportRunRecord(id=RUN_ID, status=status),
        project_exists=project_exists,
    )
    return ProvenanceExportService(repository, builder), builder


def test_export_returns_exact_canonical_manifest_bytes_and_metadata() -> None:
    service, builder = _service()

    result = service.export(project_id=PROJECT_ID, run_id=RUN_ID)

    assert result.content == _manifest().content
    assert result.checksum == _manifest().checksum
    assert result.schema_version == "1"
    assert result.filename == f"provenance-{RUN_ID}.json"
    assert builder.calls == 1

    payload = json.loads(result.content)
    assert payload["run"]["seed"] == 42
    assert payload["run"]["working_srid"] == 32637
    assert payload["config"]["value"] == {"density": {"target": 1.25}}
    assert payload["datasets"][0]["dataset_version_id"] == str(DATASET_VERSION_ID)


def test_missing_project_or_run_is_project_scoped_and_does_not_build_manifest() -> None:
    builder = FakeBuilder(_manifest())
    service = ProvenanceExportService(
        FakeRepository(None, project_exists=False),
        builder,
    )
    with pytest.raises(ProvenanceExportProjectNotFoundError):
        service.export(project_id=PROJECT_ID, run_id=RUN_ID)
    assert builder.calls == 0

    service = ProvenanceExportService(FakeRepository(None), builder)
    with pytest.raises(ProvenanceExportRunNotFoundError):
        service.export(project_id=PROJECT_ID, run_id=RUN_ID)
    assert builder.calls == 0


def test_non_successful_run_is_not_exportable() -> None:
    service, builder = _service(status="running")

    with pytest.raises(ProvenanceExportUnavailableError, match="successful"):
        service.export(project_id=PROJECT_ID, run_id=RUN_ID)

    assert builder.calls == 0


@pytest.mark.parametrize(
    "manifest",
    [
        _manifest(project_id=uuid.uuid4()),
        _manifest(run_id=uuid.uuid4()),
        _manifest(seed=True),
        _manifest(working_srid=0),
        _manifest(config={"schema_version": "1", "checksum": "x", "value": []}),
        _manifest(datasets=[{"dataset_id": str(uuid.uuid4())}]),
    ],
)
def test_export_fails_closed_when_manifest_loses_required_reproducibility_refs(
    manifest: FakeManifest,
) -> None:
    service, _ = _service(manifest=manifest)

    with pytest.raises(ProvenanceExportDataError):
        service.export(project_id=PROJECT_ID, run_id=RUN_ID)
