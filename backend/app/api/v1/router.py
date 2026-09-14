from fastapi import APIRouter

from backend.app.api.v1.endpoints import (
    health,
    projects,
    source_layers,
    suitability,
    uploads,
    zoning,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(projects.router)
api_router.include_router(source_layers.router)
api_router.include_router(suitability.router)
api_router.include_router(uploads.router)
api_router.include_router(zoning.router)
