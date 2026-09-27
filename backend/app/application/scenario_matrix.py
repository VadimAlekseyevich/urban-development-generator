"""Bounded deterministic seed x full-config expansion for existing ScenarioBatch runs.

This is application metadata, never a second Stage or a parallel job scheduler.
The matrix order is canonical: variant name, then ascending explicit seed.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from core.urban_generator.domain import RunMode

MIN_MATRIX_RUNS = 3
MAX_MATRIX_RUNS = 10
_MAX_PERSISTED_SEED = (1 << 63) - 1
_VARIANT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class ScenarioMatrixError(ValueError):
    """A requested batch matrix has invalid or non-reproducible inputs."""


@dataclass(frozen=True, slots=True)
class ScenarioConfigVariant:
    """One named full config snapshot, protected from caller mapping mutations."""

    name: str
    canonical_config_json: str

    @classmethod
    def from_config(
        cls, *, name: str, config_json: Mapping[str, object]
    ) -> ScenarioConfigVariant:
        if not isinstance(config_json, Mapping):
            raise ScenarioMatrixError("variant config must be a JSON object")
        try:
            normalized = json.dumps(
                dict(config_json),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ScenarioMatrixError("variant config must be finite valid JSON") from exc
        return cls(name=name, canonical_config_json=normalized)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _VARIANT_RE.fullmatch(self.name) is None:
            raise ScenarioMatrixError("variant name must be a stable lowercase token")
        if not isinstance(self.canonical_config_json, str):
            raise ScenarioMatrixError("variant canonical_config_json must be a string")
        try:
            config = json.loads(self.canonical_config_json)
        except (ValueError, TypeError) as exc:
            raise ScenarioMatrixError("variant config must be valid JSON") from exc
        if not isinstance(config, dict):
            raise ScenarioMatrixError("variant config must be a JSON object")
        try:
            normalized = json.dumps(
                config, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ScenarioMatrixError("variant config must be valid JSON") from exc
        if normalized != self.canonical_config_json:
            raise ScenarioMatrixError("variant config must use canonical JSON encoding")

    @property
    def config_json(self) -> dict[str, object]:
        """Return a fresh independent JSON value for one new GenerationRun."""

        value: dict[str, object] = json.loads(self.canonical_config_json)
        return value


@dataclass(frozen=True, slots=True)
class ScenarioMatrixSpec:
    """Exact run input/template provenance shared by one bounded Cartesian product."""

    project_id: uuid.UUID
    mode: RunMode
    seeds: tuple[int, ...]
    variants: tuple[ScenarioConfigVariant, ...]
    dataset_version_ids: tuple[uuid.UUID, ...]
    config_schema_version: str
    commit_sha: str
    concurrency_limit: int

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, uuid.UUID):
            raise ScenarioMatrixError("project_id must be UUID")
        if not isinstance(self.mode, RunMode):
            raise ScenarioMatrixError("mode must be canonical RunMode")
        if not isinstance(self.seeds, tuple) or not isinstance(self.variants, tuple):
            raise ScenarioMatrixError("seeds and variants must be immutable tuples")
        if not self.seeds or not self.variants:
            raise ScenarioMatrixError("matrix requires seeds and config variants")
        if len(self.seeds) * len(self.variants) not in range(
            MIN_MATRIX_RUNS, MAX_MATRIX_RUNS + 1
        ):
            raise ScenarioMatrixError("matrix must expand to 3–10 child runs")
        if any(
            isinstance(seed, bool) or not isinstance(seed, int)
            or not 0 <= seed <= _MAX_PERSISTED_SEED
            for seed in self.seeds
        ):
            raise ScenarioMatrixError("seed must fit the signed PostgreSQL BIGINT range")
        if len(set(self.seeds)) != len(self.seeds):
            raise ScenarioMatrixError("matrix seeds must be distinct")
        if any(not isinstance(variant, ScenarioConfigVariant) for variant in self.variants):
            raise ScenarioMatrixError("variants must contain ScenarioConfigVariant values")
        names = tuple(variant.name for variant in self.variants)
        if len(set(names)) != len(names):
            raise ScenarioMatrixError("matrix variant names must be distinct")
        configs = tuple(variant.canonical_config_json for variant in self.variants)
        if len(set(configs)) != len(configs):
            raise ScenarioMatrixError("matrix full config snapshots must be distinct")
        if not isinstance(self.dataset_version_ids, tuple):
            raise ScenarioMatrixError("dataset_version_ids must be an immutable tuple")
        if any(not isinstance(value, uuid.UUID) for value in self.dataset_version_ids):
            raise ScenarioMatrixError("dataset version IDs must be UUID")
        if len(set(self.dataset_version_ids)) != len(self.dataset_version_ids):
            raise ScenarioMatrixError("dataset version IDs must be distinct")
        if (
            not isinstance(self.config_schema_version, str)
            or not 0 < len(self.config_schema_version) <= 64
            or not self.config_schema_version.strip()
        ):
            raise ScenarioMatrixError("config_schema_version must be a non-empty token")
        if not isinstance(self.commit_sha, str) or _COMMIT_RE.fullmatch(self.commit_sha) is None:
            raise ScenarioMatrixError("commit_sha must be a real lowercase 40-hex SHA")
        if (
            isinstance(self.concurrency_limit, bool)
            or not isinstance(self.concurrency_limit, int)
            or not 1 <= self.concurrency_limit <= len(self.seeds) * len(self.variants)
        ):
            raise ScenarioMatrixError("concurrency_limit must fit child count")


@dataclass(frozen=True, slots=True)
class ScenarioMatrixChild:
    """One deterministic matrix row; UUID and DB timestamps are assigned separately."""

    position: int
    variant_name: str
    seed: int
    canonical_config_json: str

    @property
    def config_json(self) -> dict[str, object]:
        value: dict[str, object] = json.loads(self.canonical_config_json)
        return value


def expand_scenario_matrix(spec: ScenarioMatrixSpec) -> tuple[ScenarioMatrixChild, ...]:
    """Return stable variant-major/seed-minor order independent of caller permutation."""

    if not isinstance(spec, ScenarioMatrixSpec):
        raise ScenarioMatrixError("spec must be ScenarioMatrixSpec")
    return tuple(
        ScenarioMatrixChild(
            position=position,
            variant_name=variant.name,
            seed=seed,
            canonical_config_json=variant.canonical_config_json,
        )
        for position, (variant, seed) in enumerate(
            (variant, seed)
            for variant in sorted(spec.variants, key=lambda item: item.name)
            for seed in sorted(spec.seeds)
        )
    )
