from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from backend.app.services.osm_pbf_reader import (
    DEFAULT_RELEVANT_OSM_TAG_KEYS,
    OsmFeatureCategory,
    OsmPbfBackend,
    OsmPbfLimitError,
    OsmPbfReadError,
    OsmPbfReadLimits,
    OsmPbfReader,
    OsmPbfSource,
)

FIXTURE = Path("tests/fixtures/osm/minimal.pbf")


class FakeOsmBackend(OsmPbfBackend):
    def __init__(self, layers: dict[str, gpd.GeoDataFrame]) -> None:
        self.layers = layers
        self.read_calls: list[tuple[str, int, int]] = []
        self.count_calls: list[str] = []

    def list_layers(self, source: OsmPbfSource) -> tuple[tuple[str, str | None], ...]:
        del source
        return tuple((name, "LineString") for name in self.layers)

    def feature_count(self, source: OsmPbfSource, *, layer: str) -> int:
        del source
        self.count_calls.append(layer)
        return len(self.layers[layer])

    def read_chunk(
        self,
        source: OsmPbfSource,
        *,
        layer: str,
        offset: int,
        limit: int,
    ) -> gpd.GeoDataFrame:
        del source
        self.read_calls.append((layer, offset, limit))
        return self.layers[layer].iloc[offset : offset + limit].reset_index(drop=True)


def _line_frame(rows: list[dict[str, object]], *, crs: str = "EPSG:4326") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


def test_reader_chunks_and_preserves_relevant_tags() -> None:
    backend = FakeOsmBackend(
        {
            "lines": _line_frame(
                [
                    {
                        "osm_id": 10,
                        "highway": "residential",
                        "name": "Main",
                        "other_tags": {
                            "surface": "asphalt",
                            "amenity": "school",
                            "foo": "drop",
                        },
                        "geometry": LineString([(0, 0), (1, 0)]),
                    },
                    {
                        "osm_id": 11,
                        "waterway": "stream",
                        "name": "Creek",
                        "geometry": LineString([(0, 1), (1, 1)]),
                    },
                    {
                        "osm_id": 12,
                        "barrier": "fence",
                        "geometry": LineString([(0, 2), (1, 2)]),
                    },
                ]
            )
        }
    )
    reader = OsmPbfReader(
        backend=backend,
        limits=OsmPbfReadLimits(batch_size=2, max_features_per_layer=10),
    )

    batches = list(reader.iter_batches("territory.pbf"))

    assert backend.count_calls == ["lines"]
    assert backend.read_calls == [("lines", 0, 2), ("lines", 2, 1)]
    assert [(batch.category, batch.start_feature) for batch in batches] == [
        (OsmFeatureCategory.ROADS, 0),
        (OsmFeatureCategory.POI, 0),
        (OsmFeatureCategory.WATER, 0),
    ]
    road = batches[0].features[0]
    assert road.source_feature_id == "osm:way:10"
    assert dict(road.tags) == {
        "amenity": "school",
        "highway": "residential",
        "name": "Main",
        "surface": "asphalt",
    }
    assert "foo" not in road.tags
    assert batches[1].features[0] is road
    assert batches[2].features[0].source_feature_id == "osm:way:11"


def test_reader_filters_categories() -> None:
    backend = FakeOsmBackend(
        {
            "lines": _line_frame(
                [
                    {
                        "osm_id": 21,
                        "highway": "service",
                        "waterway": "ditch",
                        "geometry": LineString([(0, 0), (1, 0)]),
                    }
                ]
            )
        }
    )

    batches = list(
        OsmPbfReader(backend=backend).iter_batches(
            "territory.pbf",
            categories=(OsmFeatureCategory.WATER,),
        )
    )

    assert len(batches) == 1
    assert batches[0].category is OsmFeatureCategory.WATER
    assert batches[0].features[0].source_feature_id == "osm:way:21"


def test_reader_rejects_count_before_materializing_chunks() -> None:
    backend = FakeOsmBackend(
        {
            "lines": _line_frame(
                [
                    {
                        "osm_id": index + 1,
                        "highway": "residential",
                        "geometry": LineString([(0, index), (1, index)]),
                    }
                    for index in range(3)
                ]
            )
        }
    )

    with pytest.raises(OsmPbfLimitError) as exc_info:
        list(
            OsmPbfReader(
                backend=backend,
                limits=OsmPbfReadLimits(batch_size=2, max_features_per_layer=2),
            ).iter_batches("too-large.pbf")
        )

    assert exc_info.value.limit == 2
    assert exc_info.value.observed == 3
    assert backend.read_calls == []


def test_reader_validates_crs_tags_and_required_keys() -> None:
    wrong_crs = FakeOsmBackend(
        {
            "lines": _line_frame(
                [
                    {
                        "osm_id": 1,
                        "highway": "residential",
                        "geometry": LineString([(0, 0), (1, 0)]),
                    }
                ],
                crs="EPSG:3857",
            )
        }
    )
    with pytest.raises(OsmPbfReadError, match="EPSG:4326"):
        list(OsmPbfReader(backend=wrong_crs).iter_batches("wrong-crs.pbf"))

    bad_tags = FakeOsmBackend(
        {
            "lines": _line_frame(
                [
                    {
                        "osm_id": 2,
                        "other_tags": "not-json",
                        "geometry": LineString([(0, 0), (1, 0)]),
                    }
                ]
            )
        }
    )
    with pytest.raises(OsmPbfReadError, match="invalid JSON"):
        list(OsmPbfReader(backend=bad_tags).iter_batches("bad-tags.pbf"))

    with pytest.raises(ValueError, match="classification keys"):
        OsmPbfReader(relevant_tag_keys=DEFAULT_RELEVANT_OSM_TAG_KEYS - {"highway"})


def test_pyogrio_backend_reads_real_pbf_fixture_in_bounded_chunks() -> None:
    reader = OsmPbfReader(
        limits=OsmPbfReadLimits(batch_size=2, max_features_per_layer=20)
    )

    batches = list(reader.iter_batches(FIXTURE))
    features = {
        category: [
            feature
            for batch in batches
            if batch.category is category
            for feature in batch.features
        ]
        for category in OsmFeatureCategory
    }

    assert [feature.source_feature_id for feature in features[OsmFeatureCategory.ROADS]] == [
        "osm:way:100"
    ]
    assert features[OsmFeatureCategory.ROADS][0].tags["highway"] == "residential"
    assert features[OsmFeatureCategory.ROADS][0].geometry.geom_type == "LineString"
    assert [
        feature.source_feature_id for feature in features[OsmFeatureCategory.BUILDINGS]
    ] == ["osm:way:101"]
    assert features[OsmFeatureCategory.BUILDINGS][0].tags["building"] == "yes"
    assert features[OsmFeatureCategory.BUILDINGS][0].geometry.geom_type == "MultiPolygon"
    assert [feature.source_feature_id for feature in features[OsmFeatureCategory.POI]] == [
        "osm:node:1"
    ]
    assert features[OsmFeatureCategory.POI][0].tags["amenity"] == "school"
    assert [feature.source_feature_id for feature in features[OsmFeatureCategory.LANDUSE]] == [
        "osm:way:102"
    ]
    assert {
        feature.source_feature_id for feature in features[OsmFeatureCategory.WATER]
    } == {"osm:way:103", "osm:way:104"}
    assert all(len(batch.features) <= 2 for batch in batches)
    assert all(batch.crs == "EPSG:4326" for batch in batches)
