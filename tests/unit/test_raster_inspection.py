from collections.abc import Mapping
from pathlib import Path

import pytest
import rasterio
from rasterio.transform import from_origin

from backend.app.services.raster_inspection import (
    RasterInspectionError,
    RasterInspector,
    RasterMetadataBackend,
    RasterSource,
    RasterioMetadataBackend,
)


class FakeRasterMetadataBackend(RasterMetadataBackend):
    def __init__(self, metadata: Mapping[str, object]) -> None:
        self.metadata = metadata
        self.calls: list[RasterSource] = []

    def read_metadata(self, source: RasterSource) -> Mapping[str, object]:
        self.calls.append(source)
        return self.metadata


def _metadata(**overrides: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "driver": "GTiff",
        "width": 4,
        "height": 3,
        "band_count": 1,
        "dtypes": ("float32",),
        "crs": "EPSG:32637",
        "transform": (10.0, 0.0, 500000.0, 0.0, -20.0, 6200000.0),
        "nodata_values": (-9999.0,),
    }
    metadata.update(overrides)
    return metadata


def test_inspector_returns_georeferencing_metadata_without_pixels() -> None:
    backend = FakeRasterMetadataBackend(_metadata())

    inspection = RasterInspector(backend=backend).inspect(Path("dem.tif"))

    assert inspection.driver == "GTiff"
    assert inspection.width == 4
    assert inspection.height == 3
    assert inspection.band_count == 1
    assert inspection.dtypes == ("float32",)
    assert inspection.crs == "EPSG:32637"
    assert inspection.transform == (10.0, 0.0, 500000.0, 0.0, -20.0, 6200000.0)
    assert inspection.nodata_values == (-9999.0,)
    assert inspection.resolution == pytest.approx((10.0, 20.0))
    assert inspection.extent == pytest.approx((500000.0, 6199940.0, 500040.0, 6200000.0))
    assert backend.calls == [Path("dem.tif")]


def test_inspector_accepts_missing_crs_and_nan_nodata() -> None:
    backend = FakeRasterMetadataBackend(
        _metadata(crs=None, nodata_values=(float("nan"),))
    )

    inspection = RasterInspector(backend=backend).inspect("unreferenced.tif")

    assert inspection.crs is None
    assert inspection.nodata_values[0] is not None
    assert inspection.nodata_values[0] != inspection.nodata_values[0]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"width": 0}, "width"),
        ({"height": -1}, "height"),
        ({"band_count": 0}, "band_count"),
        ({"dtypes": ()}, "dtypes"),
        ({"crs": "not-a-crs"}, "CRS"),
        ({"transform": (1.0, 0.0, 0.0, 0.0, 0.0, 0.0)}, "degenerate"),
        (
            {"transform": (float("inf"), 0.0, 0.0, 0.0, -1.0, 0.0)},
            "finite",
        ),
        ({"nodata_values": ()}, "nodata"),
        ({"nodata_values": (float("inf"),)}, "infinite"),
    ],
)
def test_inspector_rejects_invalid_metadata(
    overrides: dict[str, object],
    message: str,
) -> None:
    backend = FakeRasterMetadataBackend(_metadata(**overrides))

    with pytest.raises(RasterInspectionError, match=message):
        RasterInspector(backend=backend).inspect("bad.tif")


def test_inspector_computes_extent_for_rotated_transform() -> None:
    backend = FakeRasterMetadataBackend(
        _metadata(
            width=2,
            height=1,
            transform=(2.0, 1.0, 10.0, 1.0, -2.0, 20.0),
        )
    )

    inspection = RasterInspector(backend=backend).inspect("rotated.tif")

    assert inspection.resolution == pytest.approx((5**0.5, 5**0.5))
    assert inspection.extent == pytest.approx((10.0, 18.0, 15.0, 22.0))


def test_rasterio_backend_never_calls_dataset_read(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDataset:
        driver = "GTiff"
        width = 2
        height = 2
        count = 1
        dtypes = ("uint8",)
        crs = rasterio.crs.CRS.from_epsg(4326)
        transform = from_origin(10.0, 20.0, 0.5, 0.5)
        nodatavals = (None,)

        def __enter__(self) -> "FakeDataset":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("metadata inspection must not read raster pixels")

    fake = FakeDataset()
    monkeypatch.setattr(rasterio, "open", lambda _source: fake)

    metadata = RasterioMetadataBackend().read_metadata("metadata-only.tif")

    assert metadata["width"] == 2
    assert metadata["height"] == 2


def test_rasterio_backend_inspects_real_geotiff_metadata(tmp_path: Path) -> None:
    path = tmp_path / "dem.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=3,
        height=2,
        count=1,
        dtype="float32",
        crs="EPSG:32637",
        transform=from_origin(500000.0, 6200000.0, 10.0, 20.0),
        nodata=-9999.0,
    ):
        pass

    inspection = RasterInspector().inspect(path)

    assert inspection.driver == "GTiff"
    assert inspection.width == 3
    assert inspection.height == 2
    assert inspection.band_count == 1
    assert inspection.dtypes == ("float32",)
    assert inspection.crs == "EPSG:32637"
    assert inspection.resolution == pytest.approx((10.0, 20.0))
    assert inspection.extent == pytest.approx((500000.0, 6199960.0, 500030.0, 6200000.0))
    assert inspection.nodata_values == (-9999.0,)
