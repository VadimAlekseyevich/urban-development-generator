from fastapi import APIRouter

from backend.app.api.v1.endpoints import (
    blocks_parcels,
    buildings,
    demography,
    health,
    infrastructure,
    projects,
    roads,
    source_layers,
    suitability,
    uploads,
    validation,
    zoning,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(projects.router)
api_router.include_router(infrastructure.router)
api_router.include_router(roads.router)
api_router.include_router(blocks_parcels.router)
api_router.include_router(buildings.router)
api_router.include_router(demography.router)
api_router.include_router(source_layers.router)
api_router.include_router(suitability.router)
api_router.include_router(uploads.router)
api_router.include_router(validation.router)
api_router.include_router(zoning.router)
