import uuid
from typing import Any


async def run_generation(ctx: dict[str, Any], run_id: str) -> dict[str, str]:
    """Entry point for long-running GIS generation jobs."""
    parsed_id = uuid.UUID(run_id)
    return {"run_id": str(parsed_id), "status": "accepted"}
