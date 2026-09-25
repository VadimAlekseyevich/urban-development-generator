from __future__ import annotations

import uuid

import pytest

from backend.app.application.generation_execution import GenerationExecutionReport
from worker.tasks import GenerationTaskConfigurationError, run_generation


class FakeGenerationExecutionService:
    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    def run(self, *, run_id: uuid.UUID) -> GenerationExecutionReport:
        self.calls.append(run_id)
        return GenerationExecutionReport(
            run_id=run_id,
            status="succeeded",
            executed_stages=("root", "child"),
            reused_stages=("checkpointed",),
            skipped_stages=("optional",),
        )


@pytest.mark.asyncio
async def test_run_generation_executes_configured_application_service() -> None:
    run_id = uuid.uuid4()
    service = FakeGenerationExecutionService()

    result = await run_generation(
        {"generation_execution_service": service},
        str(run_id),
    )

    assert service.calls == [run_id]
    assert result == {
        "run_id": str(run_id),
        "status": "succeeded",
        "executed_stages": ["root", "child"],
        "reused_stages": ["checkpointed"],
        "skipped_stages": ["optional"],
    }


@pytest.mark.asyncio
async def test_run_generation_rejects_missing_runtime_configuration() -> None:
    with pytest.raises(
        GenerationTaskConfigurationError,
        match="generation_execution_service is not configured",
    ):
        await run_generation({}, str(uuid.uuid4()))
