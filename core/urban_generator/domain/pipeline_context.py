from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from core.urban_generator.domain.artifacts import ArtifactStore
from core.urban_generator.domain.network import NetworkBackend
from core.urban_generator.domain.run_context import ConfigRef, RunContext
from core.urban_generator.domain.stage import StageContractError, validate_stage_metadata
from core.urban_generator.domain.territory import TerritorySnapshot


class PipelineContextError(ValueError):
    """Raised when assembled core pipeline dependencies are inconsistent."""


@dataclass(frozen=True, slots=True)
class ResolvedConfigBinding:
    """One typed core config value with immutable persisted provenance."""

    stage_name: str
    source: ConfigRef
    value: object

    def __post_init__(self) -> None:
        try:
            validate_stage_metadata(self.stage_name, "pipeline-context", ())
        except StageContractError as exc:
            raise PipelineContextError(
                f"invalid resolved config stage name: {self.stage_name!r}"
            ) from exc
        if not isinstance(self.source, ConfigRef):
            raise PipelineContextError("resolved config source must be a ConfigRef")
        if self.value is None or isinstance(self.value, Mapping):
            raise PipelineContextError(
                "resolved config value must be a typed core object, not a mapping or None"
            )


@dataclass(frozen=True, slots=True)
class PipelinePorts:
    """Infrastructure-neutral ports available to orchestration."""

    artifact_store: ArtifactStore
    network_backend: NetworkBackend | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_store, ArtifactStore):
            raise PipelineContextError(
                "artifact_store must implement the canonical ArtifactStore port"
            )
        if self.network_backend is not None and not isinstance(
            self.network_backend,
            NetworkBackend,
        ):
            raise PipelineContextError(
                "network_backend must implement the canonical NetworkBackend port or be None"
            )


@dataclass(frozen=True, slots=True)
class PipelineContext:
    """Immutable core inputs assembled for one persisted generation run."""

    run: RunContext
    snapshot: TerritorySnapshot
    configs: tuple[ResolvedConfigBinding, ...]
    ports: PipelinePorts

    def __post_init__(self) -> None:
        if not isinstance(self.run, RunContext):
            raise PipelineContextError("run must be a RunContext")
        if not isinstance(self.snapshot, TerritorySnapshot):
            raise PipelineContextError("snapshot must be a TerritorySnapshot")
        if not isinstance(self.configs, tuple):
            raise PipelineContextError("configs must be an immutable tuple")
        if not isinstance(self.ports, PipelinePorts):
            raise PipelineContextError("ports must be PipelinePorts")

        if self.run.working_srid != self.snapshot.settings.working_srid:
            raise PipelineContextError(
                "run and territory snapshot working CRS must match"
            )

        known_refs = frozenset(self.run.config_refs)
        stage_names: set[str] = set()
        for binding in self.configs:
            if not isinstance(binding, ResolvedConfigBinding):
                raise PipelineContextError(
                    "configs must contain only ResolvedConfigBinding values"
                )
            if binding.stage_name in stage_names:
                raise PipelineContextError(
                    f"duplicate resolved config stage: {binding.stage_name}"
                )
            if binding.source not in known_refs:
                raise PipelineContextError(
                    f"resolved config source is not present in RunContext: {binding.source.name}"
                )
            stage_names.add(binding.stage_name)

        network_backend = self.ports.network_backend
        if (
            network_backend is not None
            and network_backend.snapshot.working_crs.srid != self.run.working_srid
        ):
            raise PipelineContextError(
                "network backend working CRS must match pipeline working CRS"
            )

    @property
    def configured_stage_names(self) -> tuple[str, ...]:
        """Return configured stage names in immutable assembly order."""

        return tuple(binding.stage_name for binding in self.configs)

    def require_config[T](self, stage_name: str, expected_type: type[T]) -> T:
        """Return one resolved stage config narrowed to the expected core type."""

        for binding in self.configs:
            if binding.stage_name != stage_name:
                continue
            if not isinstance(binding.value, expected_type):
                raise PipelineContextError(
                    f"resolved config {stage_name} must be {expected_type.__name__}, "
                    f"got {type(binding.value).__name__}"
                )
            return binding.value
        raise PipelineContextError(
            f"resolved config is not available for stage: {stage_name}"
        )
