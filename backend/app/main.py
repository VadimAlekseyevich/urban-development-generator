from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from backend.app.api.v1.endpoints import operational_metrics
from backend.app.api.v1.router import api_router
from backend.app.core.config import settings
from backend.app.core.logging import configure_logging
from backend.app.db.job_admission import RETRY_AFTER_SECONDS, JobCapacityExceeded
from backend.app.middleware.request_context import RequestContextMiddleware

configure_logging("DEBUG" if settings.app_debug else "INFO")

app = FastAPI(
    title="Urban Development Generator API",
    version="0.1.0",
    default_response_class=ORJSONResponse,
)

app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # No cross-origin cookie/session contract exists; do not grant credentialed CORS.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(JobCapacityExceeded)
async def job_admission_backpressure(
    _request: Request, exc: JobCapacityExceeded
) -> ORJSONResponse:
    """One stable 429 contract for all background-job producers."""
    return ORJSONResponse(
        status_code=429,
        content={"detail": str(exc), "scope": exc.scope, "limit": exc.limit},
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


app.include_router(api_router, prefix=settings.api_v1_prefix)
app.include_router(operational_metrics.router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "urban-development-generator", "docs": "/docs"}
