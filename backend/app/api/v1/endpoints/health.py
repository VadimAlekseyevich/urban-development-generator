from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import get_db

router = APIRouter(prefix="/health", tags=["health"])
DbSession = Annotated[Session, Depends(get_db)]


def _probe_redis() -> None:
    client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=1,
        socket_timeout=1,
        decode_responses=True,
    )
    try:
        client.ping()
    finally:
        client.close()


@router.get("/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def readiness(db: DbSession) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unavailable", "dependency": "database"},
        ) from exc

    try:
        _probe_redis()
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "unavailable", "dependency": "redis"},
        ) from exc

    return {"status": "ok", "database": "ok", "redis": "ok"}
