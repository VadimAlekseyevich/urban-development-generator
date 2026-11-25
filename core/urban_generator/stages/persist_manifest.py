"""Final canonical stage: admit valid completed calculations and seal provenance."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from core.urban_generator.domain import (
    RunContext,
    StageDiagnostic,
    StageResult,
    TerritorySnapshot,
    ValidationReport,
    build_stage_fingerprint,
    require_stage_input,
)
from core.urban_generator.domain.errors import DomainError
from core.urban_generator.stages.catalog import METRICS_STAGE, PERSIST_MANIFEST_STAGE

MAX_MANIFEST_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PersistManifestStageConfig:
    schema_version: Literal["1"] = "1"


@dataclass(frozen=True, slots=True)
class PersistManifestStageInput:
    validation: ValidationReport
    content: bytes


@dataclass(frozen=True, slots=True)
class PersistManifestStageOutput:
    content: bytes
    checksum: str


class PersistManifestStage:
    """The DB adapter supplies a canonical projection of prior completed stages."""

    name = PERSIST_MANIFEST_STAGE
    version = "1.0.0"
    dependencies = (METRICS_STAGE,)

    def validate_input(self, value: object) -> PersistManifestStageInput:
        return require_stage_input(value, PersistManifestStageInput, stage_name=self.name)

    def execute(
        self, *, snapshot: TerritorySnapshot, context: RunContext,
        stage_input: PersistManifestStageInput, config: PersistManifestStageConfig,
    ) -> StageResult[PersistManifestStageOutput]:
        value = self.validate_input(stage_input)
        if not isinstance(config, PersistManifestStageConfig) or config.schema_version != "1":
            raise ValueError("unsupported manifest stage config")
        if snapshot.snapshot_id != context.run_id:
            raise ValueError("manifest run identity mismatch")
        if not isinstance(value.validation, ValidationReport):
            raise TypeError("manifest requires a canonical validation report")
        if not value.validation.is_valid:
            raise DomainError(
                "generation has hard validation failures",
                details={"hard_failure_count": str(len(value.validation.hard_failures))},
            )
        if not isinstance(value.content, bytes) or not 0 < len(value.content) <= MAX_MANIFEST_BYTES:
            raise ValueError("manifest content missing or exceeds byte budget")
        checksum = "sha256:" + hashlib.sha256(value.content).hexdigest()
        return StageResult(
            output=PersistManifestStageOutput(content=value.content, checksum=checksum),
            fingerprint=build_stage_fingerprint(self.name, self.version, checksum),
            diagnostics=(StageDiagnostic(
                code="manifest.prepared",
                message="Prior stage provenance prepared for transactional publication",
            ),),
        )
