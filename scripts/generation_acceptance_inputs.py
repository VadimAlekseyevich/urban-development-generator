"""Generate source bytes only for the real API/worker acceptance test.

No database access, fixture seeding or fake GenerationRun records.
The tiny synthetic geometry deliberately mirrors the existing tested spatial
chain, translated to a valid UTM location. It is not a real city benchmark.
"""

import argparse
import base64
import json

import numpy as np
from pyproj import Transformer
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.affinity import translate
from shapely.geometry import MultiLineString, Point, box, mapping
from shapely.ops import transform


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size-m", type=int, choices=(10, 100, 400), default=10)
    size = parser.parse_args().size_m
    srid = 32637
    x, y = 500_000, 6_000_000
    converter = Transformer.from_crs(srid, 4326, always_xy=True)

    def geographic(geometry):
        return mapping(transform(converter.transform, translate(geometry, xoff=x, yoff=y)))

    def feature(geometry, properties):
        return {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "id": "acceptance-source",
                "geometry": geographic(geometry), "properties": properties,
            }],
        }

    boundary = box(0, 0, size, size)
    roads = MultiLineString([
        [(0, 0), (size, 0)], [(size, 0), (size, size)],
        [(size, size), (0, size)], [(0, size), (0, 0)],
    ])
    raster_size = size + 30
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff", width=raster_size, height=raster_size, count=1, dtype="float32",
            crs=f"EPSG:{srid}", transform=from_origin(x - 15, y + size + 15, 1, 1),
            nodata=-9999, compress="deflate",
        ) as raster:
            raster.write(np.full((raster_size, raster_size), 100, dtype=np.float32), 1)
        dem = base64.b64encode(memory.read()).decode("ascii")

    print(json.dumps({
        "working_srid": srid,
        "boundary": geographic(boundary),
        "roads": feature(roads, {"road_class": "local", "name": "Acceptance ring"}),
        "landuse": feature(boundary, {"landuse_class": "residential", "landuse": "residential"}),
        "facilities": feature(Point(0, 0), {"facility_class": "clinic", "amenity": "clinic",
                                           "capacity": 1}),
        "dem_base64": dem,
    }, allow_nan=False))


if __name__ == "__main__":
    main()
