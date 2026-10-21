"""Versioned research matrix metadata over the canonical ScenarioBatch executor.

The only database write remains SqlAlchemyScenarioBatchStore.create_matrix.
One territory is submitted per transaction; no research scheduler, Job or Stage
state machine is introduced here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.application.scenario_matrix import (
    ScenarioConfigVariant,
    ScenarioMatrixError,
    ScenarioMatrixSpec,
    expand_scenario_matrix,
)
from backend.app.db.scenario_batch import (
    ScenarioMatrixCreation,
    SqlAlchemyScenarioBatchStore,
)
from core.urban_generator.domain import RunMode

EXPERIMENT_SCHEMA_VERSION = "s15-experiment-matrix-v1"
MAX_TERRITORIES = 4
_NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,23}$"
_EXPERIMENT_ID_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"


class ExperimentMatrixError(ValueError):
    """Invalid or non-reproducible experiment manifest."""


class ExperimentTerritory(BaseModel):
    """Project and ready dataset versions owned by an ordinary GenerationRun."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str = Field(pattern=_NAME_PATTERN)
    project_id: uuid.UUID
    mode: RunMode
    dataset_version_ids: tuple[uuid.UUID, ...]


class ExperimentCell(BaseModel):
    """One explicit *full* generation config for a (config, ablation) arm."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    config_name: str = Field(pattern=_NAME_PATTERN)
    ablation_name: str = Field(pattern=_NAME_PATTERN)
    config_json: dict[str, Any]

    def canonical_variant(self) -> ScenarioConfigVariant:
        """Labels are analysis metadata; executable config is the full snapshot."""
        return ScenarioConfigVariant.from_config(
            name=f"{self.config_name}__{self.ablation_name}",
            config_json=self.config_json,
        )


class ExperimentManifest(BaseModel):
    """One bounded territory × config × ablation × seed experiment request.

    Matrix products are bounded *per territory* by the existing ScenarioBatch
    3..10 child contract. Different territories are separate transactions and
    are never represented as one project-owned ScenarioBatch.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["s15-experiment-matrix-v1"]
    experiment_id: str = Field(pattern=_EXPERIMENT_ID_PATTERN)
    territories: tuple[ExperimentTerritory, ...] = Field(min_length=1, max_length=4)
    cells: tuple[ExperimentCell, ...] = Field(min_length=1, max_length=10)
    seeds: tuple[int, ...] = Field(min_length=1, max_length=10)
    config_schema_version: str = Field(min_length=1, max_length=64)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    concurrency_limit: int = Field(ge=1, le=10)

    @model_validator(mode="after")
    def validate_matrix(self) -> Self:
        territory_names = [entry.name for entry in self.territories]
        project_ids = [entry.project_id for entry in self.territories]
        if len(set(territory_names)) != len(territory_names):
            raise ValueError("experiment territory names must be distinct")
        if len(set(project_ids)) != len(project_ids):
            raise ValueError("experiment territories must use distinct project IDs")
        if any(len(set(t.dataset_version_ids)) != len(t.dataset_version_ids)
               for t in self.territories):
            raise ValueError("territory dataset version IDs must be distinct")
        keys = [(cell.config_name, cell.ablation_name) for cell in self.cells]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate config/ablation experiment cell")
        configs = {cell.config_name for cell in self.cells}
        ablations = {cell.ablation_name for cell in self.cells}
        if set(keys) != {(config, ablation) for config in configs for ablation in ablations}:
            raise ValueError("experiment cells must form a full config × ablation grid")
        try:
            for territory in self.territories:
                self.to_scenario_spec(territory)
        except ScenarioMatrixError as exc:
            raise ValueError(f"invalid canonical ScenarioBatch matrix: {exc}") from exc
        return self

    def to_scenario_spec(self, territory: ExperimentTerritory) -> ScenarioMatrixSpec:
        """Delegate all normal child-run and admission rules to S12 matrix."""
        if territory not in self.territories:
            raise ExperimentMatrixError("territory does not belong to this experiment")
        return ScenarioMatrixSpec(
            project_id=territory.project_id,
            mode=territory.mode,
            seeds=self.seeds,
            variants=tuple(cell.canonical_variant() for cell in self.cells),
            dataset_version_ids=territory.dataset_version_ids,
            config_schema_version=self.config_schema_version,
            commit_sha=self.commit_sha,
            concurrency_limit=self.concurrency_limit,
        )

    def territory(self, name: str) -> ExperimentTerritory:
        for territory in self.territories:
            if territory.name == name:
                return territory
        raise ExperimentMatrixError(f"unknown experiment territory: {name}")


@dataclass(frozen=True, slots=True)
class ExperimentRunInput:
    position: int
    config_name: str
    ablation_name: str
    seed: int
    config_json: dict[str, object]


@dataclass(frozen=True, slots=True)
class ExperimentTerritoryPlan:
    name: str
    project_id: str
    mode: str
    dataset_version_ids: tuple[str, ...]
    runs: tuple[ExperimentRunInput, ...]


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    schema_version: str
    experiment_id: str
    commit_sha: str
    config_schema_version: str
    concurrency_limit: int
    territories: tuple[ExperimentTerritoryPlan, ...]
    plan_sha256: str

    def as_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExperimentSubmission:
    """Receipt referring only to canonical batch and child run IDs."""

    experiment_id: str
    plan_sha256: str
    territory_name: str
    batch_id: str
    runs: tuple[dict[str, object], ...]

    def as_json(self) -> dict[str, object]:
        return asdict(self)


def build_experiment_plan(manifest: ExperimentManifest) -> ExperimentPlan:
    """Canonical plan hash ignores caller ordering but captures all run inputs."""
    territories: list[ExperimentTerritoryPlan] = []
    for territory in sorted(manifest.territories, key=lambda item: item.name):
        spec = manifest.to_scenario_spec(territory)
        runs = tuple(
            ExperimentRunInput(
                position=child.position,
                config_name=child.variant_name.split("__", 1)[0],
                ablation_name=child.variant_name.split("__", 1)[1],
                seed=child.seed,
                config_json=child.config_json,
            )
            for child in expand_scenario_matrix(spec)
        )
        territories.append(
            ExperimentTerritoryPlan(
                name=territory.name,
                project_id=str(territory.project_id),
                mode=territory.mode.value,
                dataset_version_ids=tuple(
                    sorted(str(version_id) for version_id in territory.dataset_version_ids)
                ),
                runs=runs,
            )
        )
    fields: dict[str, object] = {
        "schema_version": manifest.schema_version,
        "experiment_id": manifest.experiment_id,
        "commit_sha": manifest.commit_sha,
        "config_schema_version": manifest.config_schema_version,
        "concurrency_limit": manifest.concurrency_limit,
        "territories": [asdict(territory) for territory in territories],
    }
    canonical = json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ExperimentPlan(
        schema_version=manifest.schema_version,
        experiment_id=manifest.experiment_id,
        commit_sha=manifest.commit_sha,
        config_schema_version=manifest.config_schema_version,
        concurrency_limit=manifest.concurrency_limit,
        territories=tuple(territories),
        plan_sha256=f"sha256:{digest}",
    )


class ExperimentMatrixRunner:
    """Thin adapter; normal S12 ScenarioBatch owns all writes, retries and claims."""

    def __init__(self, *, batch_store: SqlAlchemyScenarioBatchStore) -> None:
        self._batch_store = batch_store

    def submit_territory(
        self, *, manifest: ExperimentManifest, territory_name: str
    ) -> ExperimentSubmission:
        plan = build_experiment_plan(manifest)
        territory = manifest.territory(territory_name)
        spec = manifest.to_scenario_spec(territory)
        created: ScenarioMatrixCreation = self._batch_store.create_matrix(spec=spec)
        selected = next(item for item in plan.territories if item.name == territory_name)
        return ExperimentSubmission(
            experiment_id=manifest.experiment_id,
            plan_sha256=plan.plan_sha256,
            territory_name=territory_name,
            batch_id=str(created.batch_id),
            runs=tuple(
                {
                    "run_id": str(run_id),
                    "position": entry.position,
                    "config_name": entry.config_name,
                    "ablation_name": entry.ablation_name,
                    "seed": entry.seed,
                }
                for run_id, entry in zip(created.run_ids, selected.runs, strict=True)
            ),
        )
