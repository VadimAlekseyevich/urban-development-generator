"""Run-scoped composition of the complete canonical production generation DAG."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.app.adapters.pipeline_context import SqlAlchemyPipelineContextAdapter
from backend.app.adapters.spatial_generation_chain import (
    SpatialGenerationInputResolver,
    build_spatial_registry,
)
from backend.app.adapters.spatial_output_publisher import TransactionalSpatialMaterializer
from backend.app.adapters.stage_config_resolver import (
    COMPLETE_CONFIG_SCHEMA_VERSION,
    CanonicalStageConfigResolver,
    PersistedStageConfigError,
)
from backend.app.application.generation import GenerationJobService, GenerationRuntime
from backend.app.core.generation_revision import (
    GenerationRevisionError,
    require_generation_revision,
)
from backend.app.db.generation_state import SqlAlchemyGenerationStateStore
from backend.app.db.session import SessionLocal
from backend.app.models.generation_run import GenerationRun
from core.urban_generator.constraints import ConstraintRegistry, RegisteredConstraintEngine
from core.urban_generator.domain import ArtifactStore
from core.urban_generator.domain.errors import ConfigError
from core.urban_generator.stages.catalog import CANONICAL_STAGE_ORDER


class CompleteGenerationRuntimeFactory:
    """Build a fresh typed registry for each persisted v5 run."""

    def __init__(
        self, *, artifact_store: ArtifactStore,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._store = artifact_store
        self._session_factory = session_factory

    def create(self, *, run_id: uuid.UUID) -> GenerationRuntime:
        try:
            with self._session_factory() as session:
                run = session.get(GenerationRun, run_id)
                if run is not None:
                    require_generation_revision(run.commit_sha)
                context = SqlAlchemyPipelineContextAdapter(
                    session=session, artifact_store=self._store,
                    config_resolver=CanonicalStageConfigResolver(
                        schema_version=COMPLETE_CONFIG_SCHEMA_VERSION,
                    ),
                ).assemble(run_id)
        except (PersistedStageConfigError, GenerationRevisionError) as exc:
            raise ConfigError(str(exc)) from exc
        registry = build_spatial_registry(
            context=context,
            constraint_engine=RegisteredConstraintEngine(ConstraintRegistry()),
            session_factory=self._session_factory,
        )
        if tuple(stage.name for stage in registry.topological_stages) != CANONICAL_STAGE_ORDER:
            raise ConfigError("production generation requires all twelve canonical stages")
        return GenerationRuntime(
            context=context, registry=registry,
            input_resolver=SpatialGenerationInputResolver(session_factory=self._session_factory),
        )


def build_complete_generation_service(
    *, artifact_store: ArtifactStore, session_factory: Callable[[], Session] = SessionLocal,
) -> GenerationJobService:
    return GenerationJobService(
        state_store=SqlAlchemyGenerationStateStore(
            session_factory=session_factory,
            materializer=TransactionalSpatialMaterializer(artifact_store=artifact_store),
            require_complete_manifest=True,
        ),
        runtime_factory=CompleteGenerationRuntimeFactory(
            artifact_store=artifact_store, session_factory=session_factory,
        ),
    )
