"""Token-guarded Prometheus scrape; never exposed on the public API v1 router."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import PlainTextResponse
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.operational_metrics import read_operational_snapshot
from backend.app.db.session import get_db
from worker.queues import LEGACY_QUEUE, REDIS_QUEUES

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]


def _redis_queue_depths() -> dict[str, int]:
    client = Redis.from_url(
        settings.redis_url, socket_connect_timeout=1, socket_timeout=1,
        decode_responses=True,
    )
    try:
        # ARQ stores queued jobs in sorted sets, not Redis lists.
        queue_names = {**REDIS_QUEUES, "legacy": LEGACY_QUEUE}
        pipeline = client.pipeline(transaction=False)
        for key in queue_names.values():
            pipeline.zcard(key)
        counts = pipeline.execute()
        depths = dict(zip(queue_names, counts, strict=True))
        if any(type(value) is not int or value < 0 for value in depths.values()):
            raise RedisError("Redis returned invalid ARQ queue sizes")
        return depths
    finally:
        client.close()


def _require_metrics_access(authorization: str | None) -> None:
    configured = settings.metrics_bearer_token
    if configured is None:
        # Fail closed: a new deployment without a separate token must not
        # quietly publish operational data to the public API.
        raise HTTPException(status_code=404, detail="Not Found")
    secret = configured.get_secret_value()
    if (
        authorization is None
        or not authorization.startswith("Bearer ")
        or not hmac.compare_digest(authorization[7:].encode(), secret.encode())
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid metrics bearer token",
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
        )


@router.get(
    "/internal/metrics",
    response_class=PlainTextResponse,
    include_in_schema=False,
    tags=["internal"],
)
def prometheus_metrics(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> PlainTextResponse:
    _require_metrics_access(authorization)
    try:
        snapshot = read_operational_snapshot(db)
        queues = _redis_queue_depths()
    except (SQLAlchemyError, RedisError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="metrics dependencies unavailable",
            headers={"Cache-Control": "no-store"},
        ) from exc
    return PlainTextResponse(
        snapshot.render(redis_queue_depths=queues),
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )
