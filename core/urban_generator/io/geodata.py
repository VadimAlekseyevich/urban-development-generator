from pathlib import Path

import geopandas as gpd
from shapely import make_valid

SUPPORTED_VECTOR_SUFFIXES = {".geojson", ".json", ".gpkg", ".shp"}
SUPPORTED_RASTER_SUFFIXES = {".tif", ".tiff"}


def read_vector(path: str | Path) -> gpd.GeoDataFrame:
    source = Path(path)
    if source.suffix.lower() not in SUPPORTED_VECTOR_SUFFIXES:
        raise ValueError(f"Unsupported vector format: {source.suffix}")
    frame = gpd.read_file(source)
    if frame.crs is None:
        raise ValueError("Input vector dataset has no CRS")
    if frame.empty:
        raise ValueError("Input vector dataset is empty")
    frame = frame.copy()
    frame.geometry = frame.geometry.map(make_valid)
    return frame


def normalize_vector_crs(frame: gpd.GeoDataFrame, target_crs: str) -> gpd.GeoDataFrame:
    if frame.crs is None:
        raise ValueError("Cannot normalize dataset without source CRS")
    return frame.to_crs(target_crs)
