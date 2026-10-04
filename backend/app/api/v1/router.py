from fastapi import APIRouter

from backend.app.api.v1.endpoints import (
    blocks_parcels,
    buildings,
    compare,
    demography,
    geojson_exports,
    geopackage_exports,
    health,
    infrastructure,
    metric_csv_exports,
    metrics,
    mvt_tiles,
    projects,
    provenance_exports,
    roads,
    runs,
    source_layers,
    suitability,
    uploads,
    validation,
    vector_layers,
    zoning,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(geojson_exports.router)
api_router.include_router(geopackage_exports.router)
api_router.include_router(metric_csv_exports.router)
api_router.include_router(provenance_exports.router)
api_router.include_router(projects.router)
api_router.include_router(runs.router)
api_router.include_router(infrastructure.router)
api_router.include_router(metrics.router)
api_router.include_router(compare.router)
api_router.include_router(roads.router)
api_router.include_router(blocks_parcels.router)
api_router.include_router(buildings.router)
api_router.include_router(demography.router)
api_router.include_router(source_layers.router)
api_router.include_router(suitability.router)
api_router.include_router(uploads.router)
api_router.include_router(validation.router)
api_router.include_router(vector_layers.router)
api_router.include_router(mvt_tiles.router)
api_router.include_router(zoning.router)
