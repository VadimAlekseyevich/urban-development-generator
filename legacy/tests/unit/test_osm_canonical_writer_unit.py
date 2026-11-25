import uuid
from collections.abc import Iterable

import pytest
from shapely.geometry import LineString, Point

from backend.app.db.source_layer_writer import CanonicalSourceLayer, SourceLayerWriteResult
from backend.app.services.osm_canonical_writer import (
    OsmCanonicalWriter,
    OsmCanonicalWriterError,
)
from backend.app.services.osm_mapping import (
    MappedOsmBatch,
    MappedOsmFeature,
    OsmMappedLayer,
    RoadClass,
)
from backend.app.services.osm_pbf_reader import OsmElementType, OsmFeatureCategory
from backend.app.services.vector_normalization import NormalizedVectorBatch
from core.urban_generator.domain import WorkingCRS


class FakeBatchWriter:
    def __init__(self) -> None:
        self.dataset_version_id: uuid.UUID | None = None
        self.layer: CanonicalSourceLayer | None = None
        self.batches: list[NormalizedVectorBatch] = []

    def replace(
        self,
        *,
        dataset_version_id: uuid.UUID,
        layer: CanonicalSourceLayer,
        batches: Iterable[NormalizedVectorBatch],
    ) -> SourceLayerWriteResult:
        self.dataset_version_id = dataset_version_id
        self.layer = layer
        self.batches = list(batches)
        return SourceLayerWriteResult(
            dataset_version_id=dataset_version_id,
            layer=layer,
            working_srid=3857,
            deleted_rows=0,
            inserted_rows=sum(len(batch.frame) for batch in self.batches),
            input_batches=len(self.batches),
            insert_statements=len(self.batches),
            analyzed=bool(self.batches),
        )


def _road_feature(
    *,
    osm_id: int = 10,
    tags: dict[str, str] | None = None,
) -> MappedOsmFeature:
    return MappedOsmFeature(
        source_feature_id=f"osm:way:{osm_id}",
        element_type=OsmElementType.WAY,
        osm_id=osm_id,
        target_layer=OsmMappedLayer.ROADS,
        internal_class=RoadClass.LOCAL,
        canonical_attributes={
            "road_class": "local",
            "name": "Road",
            "lanes": 2,
            "max_speed_kph": 50.0,
            "one_way": False,
            "attributes_json": {
                "osm_mapping_version": "osm-v1",
                "osm_mapping_rule": "road.local",
            },
        },
        source_tags=tags or {"highway": "residential"},
        geometry=LineString([(0.0, 0.0), (0.01, 0.01)]),
        rule_id="road.local",
        mapping_version="osm-v1",
    )


def _road_batch(*features: MappedOsmFeature, version: str = "osm-v1") -> MappedOsmBatch:
    return MappedOsmBatch(
        source_category=OsmFeatureCategory.ROADS,
        target_layer=OsmMappedLayer.ROADS,
        source_layer="lines",
        start_feature=4,
        features=features,
        mapping_version=version,
        crs="EPSG:4326",
    )


def test_writer_reprojects_bounded_batch_and_preserves_canonical_attributes() -> None:
    backend = FakeBatchWriter()
    writer = OsmCanonicalWriter(batch_writer=backend)
    version_id = uuid.uuid4()

    result = writer.replace_layer(
        dataset_version_id=version_id,
        target_layer=OsmMappedLayer.ROADS,
        working_crs=WorkingCRS(3857),
        mapping_version="osm-v1",
        batches=[_road_batch(_road_feature())],
    )

    assert result.source_batches == 1
    assert result.source_features == 1
    assert result.persistence.inserted_rows == 1
    assert backend.dataset_version_id == version_id
    assert backend.layer is CanonicalSourceLayer.ROADS
    assert len(backend.batches) == 1
    batch = backend.batches[0]
    assert batch.working_srid == 3857
    assert batch.source_crs == "EPSG:4326"
    assert batch.start_feature == 4
    assert batch.frame.crs is not None
    assert batch.frame.crs.to_epsg() == 3857
    assert batch.frame.iloc[0]["source_feature_id"] == "osm:way:10"
    assert batch.frame.iloc[0]["road_class"] == "local"
    assert batch.frame.iloc[0]["attributes_json"]["osm_mapping_version"] == "osm-v1"
    assert batch.frame.geometry.iloc[0].bounds[2] > 1000


def test_writer_normalizes_osm_crossing_and_direction_semantics() -> None:
    backend = FakeBatchWriter()
    feature = _road_feature(
        tags={
            "highway": "primary",
            "oneway": "-1",
            "bridge": "yes",
            "tunnel": "no",
            "layer": "2",
        }
    )

    OsmCanonicalWriter(batch_writer=backend).replace_layer(
        dataset_version_id=uuid.uuid4(),
        target_layer=OsmMappedLayer.ROADS,
        working_crs=WorkingCRS(3857),
        mapping_version="osm-v1",
        batches=[_road_batch(feature)],
    )

    row = backend.batches[0].frame.iloc[0]
    assert row["one_way"] is True or bool(row["one_way"]) is True
    assert row["one_way_direction"] == "reverse"
    assert row["bridge"] is True or bool(row["bridge"]) is True
    assert row["tunnel"] is False or bool(row["tunnel"]) is False
    assert row["layer"] == 2


def test_writer_streams_multiple_batches_without_materializing_layer() -> None:
    backend = FakeBatchWriter()
    writer = OsmCanonicalWriter(batch_writer=backend)
    observed: list[int] = []

    def batches():
        for osm_id in (1, 2, 3):
            observed.append(osm_id)
            yield _road_batch(_road_feature(osm_id=osm_id))

    result = writer.replace_layer(
        dataset_version_id=uuid.uuid4(),
        target_layer=OsmMappedLayer.ROADS,
        working_crs=WorkingCRS(3857),
        mapping_version="osm-v1",
        batches=batches(),
    )

    assert observed == [1, 2, 3]
    assert result.source_batches == 3
    assert result.source_features == 3
    assert len(backend.batches) == 3


def test_writer_allows_empty_replace_without_fabricating_batch() -> None:
    backend = FakeBatchWriter()
    result = OsmCanonicalWriter(batch_writer=backend).replace_layer(
        dataset_version_id=uuid.uuid4(),
        target_layer=OsmMappedLayer.ROADS,
        working_crs=WorkingCRS(3857),
        mapping_version="osm-v1",
        batches=(),
    )

    assert result.source_batches == 0
    assert result.source_features == 0
    assert backend.batches == []


def test_writer_rejects_target_layer_mapping_version_and_crs_mismatch() -> None:
    feature = _road_feature()
    backend = FakeBatchWriter()
    writer = OsmCanonicalWriter(batch_writer=backend)

    wrong_layer = MappedOsmBatch(
        source_category=OsmFeatureCategory.ROADS,
        target_layer=OsmMappedLayer.BUILDINGS,
        source_layer="lines",
        start_feature=0,
        features=(),
        mapping_version="osm-v1",
    )
    with pytest.raises(OsmCanonicalWriterError, match="different canonical layer"):
        writer.replace_layer(
            dataset_version_id=uuid.uuid4(),
            target_layer=OsmMappedLayer.ROADS,
            working_crs=WorkingCRS(3857),
            mapping_version="osm-v1",
            batches=[wrong_layer],
        )

    with pytest.raises(OsmCanonicalWriterError, match="different mapping version"):
        writer.replace_layer(
            dataset_version_id=uuid.uuid4(),
            target_layer=OsmMappedLayer.ROADS,
            working_crs=WorkingCRS(3857),
            mapping_version="osm-v2",
            batches=[_road_batch(feature)],
        )

    wrong_crs = MappedOsmBatch(
        source_category=OsmFeatureCategory.ROADS,
        target_layer=OsmMappedLayer.ROADS,
        source_layer="lines",
        start_feature=0,
        features=(feature,),
        mapping_version="osm-v1",
        crs="EPSG:3857",
    )
    with pytest.raises(OsmCanonicalWriterError, match="EPSG:4326"):
        writer.replace_layer(
            dataset_version_id=uuid.uuid4(),
            target_layer=OsmMappedLayer.ROADS,
            working_crs=WorkingCRS(3857),
            mapping_version="osm-v1",
            batches=[wrong_crs],
        )


def test_writer_rejects_feature_level_contract_drift() -> None:
    feature = MappedOsmFeature(
        source_feature_id="osm:node:1",
        element_type=OsmElementType.NODE,
        osm_id=1,
        target_layer=OsmMappedLayer.ROADS,
        internal_class=RoadClass.LOCAL,
        canonical_attributes={"road_class": "local", "geometry": Point(0, 0)},
        source_tags={"highway": "residential"},
        geometry=Point(0, 0),
        rule_id="road.local",
        mapping_version="osm-v1",
    )

    with pytest.raises(OsmCanonicalWriterError, match="reserved fields"):
        OsmCanonicalWriter(batch_writer=FakeBatchWriter()).replace_layer(
            dataset_version_id=uuid.uuid4(),
            target_layer=OsmMappedLayer.ROADS,
            working_crs=WorkingCRS(3857),
            mapping_version="osm-v1",
            batches=[_road_batch(feature)],
        )
