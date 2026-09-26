from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.app.adapters.pipeline_context import (
    PersistedStageConfigResolver,
    SqlAlchemyPipelineContextAdapter,
)
from backend.app.application.generation import (
    GenerationInputResolver,
    GenerationRuntime,
)
from backend.app.db.session import SessionLocal
from core.urban_generator.domain import ArtifactStore, NetworkBackend
from core.urban_generator.stages import StageRegistry


class SqlAlchemyGenerationRuntimeFactory:
    """Compose persisted core inputs with an explicitly registered canonical DAG."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        config_resolver: PersistedStageConfigResolver,
        registry: StageRegistry,
        input_resolver: GenerationInputResolver,
        session_factory: Callable[[], Session] = SessionLocal,
        network_backend: NetworkBackend | None = None,
        skip_stages: tuple[str, ...] = (),
    ) -> None:
        self._session_factory = session_factory
        self._artifact_store = artifact_store
        self._config_resolver = config_resolver
        self._registry = registry
        self._input_resolver = input_resolver
        self._network_backend = network_backend
        self._skip_stages = skip_stages

    def create(self, *, run_id: uuid.UUID) -> GenerationRuntime:
        with self._session_factory() as session:
            context = SqlAlchemyPipelineContextAdapter(
                session=session,
                artifact_store=self._artifact_store,
                config_resolver=self._config_resolver,
                network_backend=self._network_backend,
            ).assemble(run_id)
        return GenerationRuntime(
            context=context,
            registry=self._registry,
            input_resolver=self._input_resolver,
            skip_stages=self._skip_stages,
        )
