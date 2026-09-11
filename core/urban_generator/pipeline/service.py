from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.urban_generator.models.config import GenerationConfig
from core.urban_generator.pipeline.stages import PipelineStage

StageHandler = Callable[[dict[str, Any], GenerationConfig], dict[str, Any]]


@dataclass(slots=True)
class GenerationPipeline:
    """Orchestrates algorithm stages without depending on HTTP or persistence."""

    handlers: dict[PipelineStage, StageHandler] = field(default_factory=dict)

    def register(self, stage: PipelineStage, handler: StageHandler) -> None:
        self.handlers[stage] = handler

    def run(self, context: dict[str, Any], config: GenerationConfig) -> dict[str, Any]:
        result = dict(context)
        for stage in PipelineStage:
            handler = self.handlers.get(stage)
            if handler is None:
                raise RuntimeError(f"Pipeline stage is not configured: {stage}")
            result = handler(result, config)
        return result
