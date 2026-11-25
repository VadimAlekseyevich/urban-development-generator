"""Project-scoped synchronous export of the canonical successful-run provenance manifest."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Protocol

PROVENANCE_EXPORT_MEDIA_TYPE = "application/json"
PROVENANCE_EXPORT_SCHEMA_VERSION = "1"


class ProvenanceExportQueryError(ValueError):
    """The requested provenance export is not well formed."""


class ProvenanceExportProjectNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID) -> None:
        super().__init__(f"project {project_id} was not found")


class ProvenanceExportRunNotFoundError(LookupError):
    """The requested run is missing or belongs to another project."""


class ProvenanceExportUnavailableError(RuntimeError):
    """The requested run is not an immutable successful run."""


class ProvenanceExportDataError(ValueError):
    """Persisted canonical provenance is incomplete or malformed."""


@dataclass(frozen=True, slots=True)
class ProvenanceExportRunRecord:
    id: uuid.UUID
    status: str


class ProvenanceExportRepository(Protocol):
    def project_exists(self, *, project_id: uuid.UUID) -> bool: ...

    def get_run(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> ProvenanceExportRunRecord | None: ...


class ProvenanceManifestDocument(Protocol):
    @property
    def run_id(self) -> uuid.UUID: ...

    @property
    def content(self) -> bytes: ...

    @property
    def checksum(self) -> str: ...


class ProvenanceManifestBuilder(Protocol):
    def build(self, *, run_id: uuid.UUID) -> ProvenanceManifestDocument: ...


@dataclass(frozen=True, slots=True)
class ProvenanceExportResult:
    schema_version: str
    project_id: uuid.UUID
    run_id: uuid.UUID
    checksum: str
    filename: str
    content: bytes


class ProvenanceExportService:
    """Expose the existing S12 manifest without creating a second provenance model."""

    def __init__(
        self,
        repository: ProvenanceExportRepository,
        manifest_builder: ProvenanceManifestBuilder,
    ) -> None:
        self._repository = repository
        self._manifest_builder = manifest_builder

    def export(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> ProvenanceExportResult:
        _require_request(project_id=project_id, run_id=run_id)

        record = self._repository.get_run(project_id=project_id, run_id=run_id)
        if record is None:
            if not self._repository.project_exists(project_id=project_id):
                raise ProvenanceExportProjectNotFoundError(project_id)
            raise ProvenanceExportRunNotFoundError(
                f"run {run_id} was not found in project {project_id}"
            )
        if record.status != "succeeded":
            raise ProvenanceExportUnavailableError(
                "provenance export requires an immutable successful generation run"
            )

        manifest = self._manifest_builder.build(run_id=run_id)
        if manifest.run_id != run_id:
            raise ProvenanceExportDataError("manifest run identity does not match the request")

        payload = _decode_manifest(manifest.content)
        _validate_export_payload(payload, project_id=project_id, run_id=run_id)

        return ProvenanceExportResult(
            schema_version=PROVENANCE_EXPORT_SCHEMA_VERSION,
            project_id=project_id,
            run_id=run_id,
            checksum=manifest.checksum,
            filename=f"provenance-{run_id}.json",
            content=manifest.content,
        )


def _require_request(*, project_id: uuid.UUID, run_id: uuid.UUID) -> None:
    if not isinstance(project_id, uuid.UUID) or not isinstance(run_id, uuid.UUID):
        raise ProvenanceExportQueryError("project_id and run_id must be UUIDs")


def _decode_manifest(content: bytes) -> dict[str, object]:
    if not isinstance(content, bytes):
        raise ProvenanceExportDataError("manifest content must be UTF-8 bytes")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceExportDataError("manifest content is not valid canonical JSON") from exc
    if not isinstance(value, dict):
        raise ProvenanceExportDataError("manifest root must be a JSON object")
    return value


def _validate_export_payload(
    payload: dict[str, object],
    *,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    if payload.get("schema_version") != PROVENANCE_EXPORT_SCHEMA_VERSION:
        raise ProvenanceExportDataError("manifest schema version is unsupported")

    run = payload.get("run")
    config = payload.get("config")
    datasets = payload.get("datasets")
    if not isinstance(run, dict) or not isinstance(config, dict) or not isinstance(datasets, list):
        raise ProvenanceExportDataError(
            "manifest must contain run, config and datasets provenance"
        )
    if run.get("id") != str(run_id) or run.get("project_id") != str(project_id):
        raise ProvenanceExportDataError("manifest project/run scope does not match the request")

    seed = run.get("seed")
    working_srid = run.get("working_srid")
    if (
        isinstance(seed, bool)
        or not isinstance(seed, int)
        or isinstance(working_srid, bool)
        or not isinstance(working_srid, int)
        or working_srid <= 0
    ):
        raise ProvenanceExportDataError("manifest must contain canonical seed and working_srid")

    if (
        not isinstance(config.get("schema_version"), str)
        or not isinstance(config.get("checksum"), str)
        or not isinstance(config.get("value"), dict)
    ):
        raise ProvenanceExportDataError("manifest must contain normalized config provenance")

    for dataset in datasets:
        if not isinstance(dataset, dict) or not isinstance(
            dataset.get("dataset_version_id"), str
        ):
            raise ProvenanceExportDataError(
                "manifest dataset entries must contain dataset_version_id"
            )
