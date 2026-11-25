"""Deterministic, read-only manifest of one successful run's persisted provenance.

This is a projection of DB-authoritative records, not a replacement for the
generation Stage contract or the artifact publication lifecycle. It never
modifies a completed run or claims that referenced blobs have been rehashed.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from backend.app.application.metric_dashboard import (
    MetricDashboardDataError,
    MetricDashboardQueryService,
    MetricDashboardUnavailableError,
)
from backend.app.application.provenance_exports import ProvenanceExportDataError
from backend.app.application.scenario_matrix import (
    ScenarioConfigVariant,
    ScenarioMatrixError,
)
from backend.app.db.metric_dashboard_query_repository import (
    SqlAlchemyMetricDashboardQueryRepository,
)
from backend.app.db.session import SessionLocal
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from backend.app.models.dataset import DatasetVersion
from backend.app.models.generation_run import RUN_SUCCESS_STATUS, GenerationRun
from backend.app.models.run_stage_result import RunStageResult
from core.urban_generator.domain.benchmarking import CANONICAL_METRIC_REGISTRY

MANIFEST_SCHEMA_VERSION = "1"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_BARE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class ProvenanceManifestError(ProvenanceExportDataError):
    """A complete trustworthy manifest cannot be derived from persisted records."""


@dataclass(frozen=True, slots=True)
class ProvenanceManifest:
    run_id: uuid.UUID
    content: bytes
    checksum: str

    def as_dict(self) -> dict[str, Any]:
        """Return a fresh JSON value; mutating it cannot modify the manifest."""
        value: dict[str, Any] = json.loads(self.content)
        return value


class SqlAlchemyProvenanceManifestService:
    """Read one consistent PostgreSQL snapshot and emit canonical UTF-8 JSON."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._session_factory = session_factory

    def build(self, *, run_id: uuid.UUID) -> ProvenanceManifest:
        if not isinstance(run_id, uuid.UUID):
            raise TypeError("run_id must be UUID")
        with self._session_factory() as session:
            with session.begin():
                # selectinload performs several SELECTs: use one immutable snapshot,
                # not a collection of independently visible committed states.
                session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                )
                run = session.scalar(
                    select(GenerationRun)
                    .where(GenerationRun.id == run_id)
                    .options(
                        selectinload(GenerationRun.dataset_versions).selectinload(
                            DatasetVersion.dataset
                        ),
                        selectinload(GenerationRun.stage_results).selectinload(
                            RunStageResult.artifacts
                        ),
                    )
                )
                if run is None or run.status != RUN_SUCCESS_STATUS:
                    raise ProvenanceManifestError(
                        "manifest requires an existing successful generation run"
                    )
                if run.commit_sha is None or _COMMIT_SHA.fullmatch(run.commit_sha) is None:
                    raise ProvenanceManifestError("successful run has no valid code commit")
                try:
                    config = ScenarioConfigVariant.from_config(
                        name="provenance", config_json=run.config_json
                    )
                except ScenarioMatrixError as exc:
                    raise ProvenanceManifestError(
                        "run config is not canonical finite JSON"
                    ) from exc
                if not run.config_schema_version:
                    raise ProvenanceManifestError("run config schema version is missing")
                config_bytes = config.canonical_config_json.encode("utf-8")
                datasets = self._datasets(session, run)
                stages = self._stages(run)
                evaluation = self._evaluation(session, run)
                payload: dict[str, Any] = {
                    "schema_version": MANIFEST_SCHEMA_VERSION,
                    "run": {
                        "id": str(run.id),
                        "project_id": str(run.project_id),
                        "rerun_source_id": (
                            str(run.rerun_source_id) if run.rerun_source_id else None
                        ),
                        "mode": run.mode,
                        "seed": run.seed,
                        "working_srid": run.working_srid,
                        "status": run.status,
                    },
                    "code": {"commit_sha": run.commit_sha},
                    "config": {
                        "schema_version": run.config_schema_version,
                        "checksum": _checksum(config_bytes),
                        "value": config.config_json,
                    },
                    "datasets": datasets,
                    "stages": stages,
                    "evaluation": evaluation,
                }
                try:
                    encoded = _canonical_json(payload)
                except (TypeError, ValueError) as exc:
                    raise ProvenanceManifestError(
                        "persisted provenance is not finite canonical JSON"
                    ) from exc
                return ProvenanceManifest(
                    run_id=run_id, content=encoded, checksum=_checksum(encoded)
                )

    @staticmethod
    def _datasets(session: Session, run: GenerationRun) -> list[dict[str, Any]]:
        versions = run.dataset_versions
        ids = [item.id for item in versions]
        artifacts = session.scalars(
            select(Artifact).where(
                Artifact.owner_type == "dataset_version",
                Artifact.owner_id.in_(ids),
            )
        ).all() if ids else []
        by_owner: dict[uuid.UUID, list[Artifact]] = {}
        for artifact in artifacts:
            if artifact.owner_id is not None:
                by_owner.setdefault(artifact.owner_id, []).append(artifact)

        result: list[dict[str, Any]] = []
        for version in versions:
            if version.dataset.project_id != run.project_id:
                raise ProvenanceManifestError("run references a foreign-project dataset")
            if version.status != "ready":
                raise ProvenanceManifestError(
                    f"dataset version is not ready: {version.id}"
                )
            if (
                version.checksum_sha256 is None
                or _BARE_SHA256.fullmatch(version.checksum_sha256) is None
            ):
                raise ProvenanceManifestError(
                    f"dataset version has no valid checksum: {version.id}"
                )
            rows = by_owner.get(version.id, [])
            if len(rows) != 1:
                raise ProvenanceManifestError(
                    f"dataset version must have one referenced source artifact: {version.id}"
                )
            artifact = rows[0]
            key = version.source_metadata.get("artifact_key")
            if not isinstance(key, str) or artifact.uri != f"artifact://{key}":
                raise ProvenanceManifestError(
                    f"dataset version source artifact key disagrees with ownership: {version.id}"
                )
            if artifact.checksum != f"sha256:{version.checksum_sha256}":
                raise ProvenanceManifestError(
                    f"dataset version source artifact checksum disagrees: {version.id}"
                )
            result.append(
                {
                    "dataset_id": str(version.dataset_id),
                    "kind": version.dataset.kind,
                    "dataset_version_id": str(version.id),
                    "version": version.version,
                    "checksum": f"sha256:{version.checksum_sha256}",
                    "source_artifact": _artifact(
                        artifact, owner_type="dataset_version", owner_id=version.id
                    ),
                }
            )
        return sorted(
            result,
            key=lambda item: (
                item["kind"],
                item["dataset_id"],
                item["version"],
                item["dataset_version_id"],
            ),
        )

    @staticmethod
    def _stages(run: GenerationRun) -> list[dict[str, Any]]:
        if not run.stage_results:
            raise ProvenanceManifestError("successful run has no persisted stage results")
        result: list[dict[str, Any]] = []
        for stage in run.stage_results:
            if stage.status not in {"succeeded", "skipped"}:
                raise ProvenanceManifestError(
                    f"nonterminal stage in successful run: {stage.stage_name}"
                )
            if (
                _SHA256.fullmatch(stage.input_hash) is None
                or _SHA256.fullmatch(stage.config_hash) is None
            ):
                raise ProvenanceManifestError(
                    f"stage has invalid input/config provenance: {stage.stage_name}"
                )
            if stage.status == "succeeded":
                if (
                    stage.output_fingerprint is None
                    or _SHA256.fullmatch(stage.output_fingerprint) is None
                ):
                    raise ProvenanceManifestError(
                        f"successful stage has no output fingerprint: {stage.stage_name}"
                    )
            elif stage.output_fingerprint is not None or stage.artifacts:
                raise ProvenanceManifestError(
                    f"skipped stage has outputs: {stage.stage_name}"
                )
            artifacts = [
                _artifact(
                    artifact, owner_type="run_stage_result", owner_id=stage.id
                )
                for artifact in stage.artifacts
            ]
            result.append(
                {
                    "name": stage.stage_name,
                    "version": stage.stage_version,
                    "status": stage.status,
                    "input_hash": stage.input_hash,
                    "config_hash": stage.config_hash,
                    "output_fingerprint": stage.output_fingerprint,
                    "artifacts": sorted(
                        artifacts, key=lambda item: (item["uri"], item["id"])
                    ),
                }
            )
        return sorted(result, key=lambda item: item["name"])

    @staticmethod
    def _evaluation(session: Session, run: GenerationRun) -> dict[str, Any]:
        dashboard = MetricDashboardQueryService(
            SqlAlchemyMetricDashboardQueryRepository(session)
        )
        try:
            persisted = dashboard.get_dashboard(
                project_id=run.project_id, run_id=run.id
            )
        except (MetricDashboardUnavailableError, MetricDashboardDataError) as exc:
            raise ProvenanceManifestError(
                "run has no valid canonical persisted evaluation"
            ) from exc
        by_id = {metric.metric_id: metric for metric in persisted.metrics}
        raw_metrics = [
            {
                "metric_id": metric_id.value,
                "unit": CANONICAL_METRIC_REGISTRY.get(metric_id).unit,
                "definition_version": CANONICAL_METRIC_REGISTRY.get(metric_id).version,
                "raw_value": by_id[metric_id].raw_value,
            }
            for metric_id in CANONICAL_METRIC_REGISTRY.metric_ids
            if metric_id in by_id
        ]
        return {
            "schema_version": "1",
            "score_config": {
                "id": persisted.score_config_id,
                "version": persisted.score_config_version,
            },
            "normalization_profile": {
                "id": persisted.normalization_profile_id,
                "version": persisted.normalization_profile_version,
            },
            "composite_score": persisted.composite_score,
            "raw_metrics": raw_metrics,
        }


def _artifact(
    artifact: Artifact, *, owner_type: str, owner_id: uuid.UUID
) -> dict[str, Any]:
    if (
        artifact.state != ArtifactLifecycleState.REFERENCED.value
        or artifact.owner_type != owner_type
        or artifact.owner_id != owner_id
        or not artifact.uri.startswith("artifact://")
        or _SHA256.fullmatch(artifact.checksum) is None
        or artifact.size_bytes < 0
    ):
        raise ProvenanceManifestError(
            f"artifact is not an immutable referenced {owner_type} output: {artifact.id}"
        )
    return {
        "id": str(artifact.id),
        "uri": artifact.uri,
        "checksum": artifact.checksum,
        "size_bytes": artifact.size_bytes,
        "content_type": artifact.content_type,
    }


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _checksum(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()
