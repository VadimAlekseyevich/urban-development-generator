"""S13-T02 bounded LayerCatalog vector contract without a real database."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from backend.app.application.layer_catalog import LayerCatalogEntry
from backend.app.application.source_layers import SourceLayerBbox
from backend.app.application.vector_layers import (
    MAX_VECTOR_LIMIT,
    VectorFeature,
    VectorLayerContext,
    VectorLayerNotFoundError,
    VectorLayerNotReadyError,
    VectorLayerQueryError,
    VectorLayerQueryService,
)

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
DATASET = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")
BBOX = "-0.2,51.4,0.2,51.6"


@dataclass
class FakeRepository:
    context: VectorLayerContext | None = VectorLayerContext(working_srid=3857)
    returned: list[VectorFeature] = field(default_factory=list)
    last_entry: LayerCatalogEntry | None = None
    last_bbox: SourceLayerBbox | None = None
    last_after: uuid.UUID | None = None
    last_limit: int | None = None
    last_simplify: float | None = None

    def get_context(self, entry: LayerCatalogEntry) -> VectorLayerContext | None:
        self.last_entry = entry
        return self.context

    def list_features(
        self,
        *,
        entry: LayerCatalogEntry,
        working_srid: int,
        bbox: SourceLayerBbox,
        after: uuid.UUID | None,
        limit: int,
        simplify_m: float,
    ) -> list[VectorFeature]:
        assert self.last_entry == entry
        assert working_srid == 3857
        self.last_bbox = bbox
        self.last_after = after
        self.last_limit = limit
        self.last_simplify = simplify_m
        return self.returned[:limit]


def _feature(number: int) -> VectorFeature:
    return VectorFeature(
        id=uuid.UUID(int=number),
        geometry={"type": "Point", "coordinates": [0, 0]},
        properties={"fixture": number},
    )


def _read(
    service: VectorLayerQueryService,
    **overrides: object,
) -> object:
    kwargs: dict[str, object] = dict(
        project_id=PROJECT,
        layer_id="source.roads",
        dataset_version_id=DATASET,
        run_id=None,
        bbox_text=BBOX,
    )
    kwargs.update(overrides)
    return service.get_geojson(**kwargs)  # type: ignore[arg-type]


def test_service_binds_only_exact_catalog_owner_and_pages_without_offset() -> None:
    repository = FakeRepository(returned=[_feature(1), _feature(2), _feature(3)])
    service = VectorLayerQueryService(repository)
    page = _read(service, limit=2, after=uuid.UUID(int=0), simplify_m=20)
    assert page.schema_version == "bounded-vector-v1"
    assert page.type == "FeatureCollection"
    assert page.layer_id == "source.roads"
    assert page.definition_version == "1"
    assert page.project_id == PROJECT
    assert page.dataset_version_id == DATASET
    assert page.run_id is None
    assert page.working_srid == 3857
    assert page.presentation_crs == "EPSG:4326"
    assert page.query_bbox == (-0.2, 51.4, 0.2, 51.6)
    assert page.truncated is True
    assert page.next_after == uuid.UUID(int=2)
    assert [feature.id for feature in page.features] == [
        uuid.UUID(int=1), uuid.UUID(int=2),
    ]
    assert repository.last_entry is not None
    assert repository.last_entry.owner.dataset_version_id == DATASET
    assert repository.last_entry.owner.run_id is None
    assert repository.last_after == uuid.UUID(int=0)
    assert repository.last_bbox == SourceLayerBbox(-0.2, 51.4, 0.2, 51.6)
    assert repository.last_limit == 3
    assert repository.last_simplify == 20

    repository.returned = [_feature(3)]
    last = _read(service, limit=2, after=uuid.UUID(int=2))
    assert last.truncated is False
    assert last.next_after is None


def test_run_scope_and_unpublished_derived_read_model() -> None:
    repo = FakeRepository()
    service = VectorLayerQueryService(repo)
    page = _read(
        service, layer_id="generated.roads", dataset_version_id=None, run_id=RUN,
    )
    assert page.run_id == RUN
    assert repo.last_entry is not None
    assert repo.last_entry.owner.run_id == RUN
    assert repo.last_entry.owner.dataset_version_id is None

    repo.context = VectorLayerContext(working_srid=3857, read_model_ready=False)
    with pytest.raises(VectorLayerNotReadyError):
        _read(
            service, layer_id="generated.demography", dataset_version_id=None,
            run_id=RUN,
        )
    assert repo.last_limit is not None  # no second DB feature call


def test_invalid_owner_or_non_tabular_catalog_entries_rejected_before_repository() -> None:
    repo = FakeRepository()
    service = VectorLayerQueryService(repo)
    for options in (
        {"dataset_version_id": None},
        {"run_id": RUN},
        {"layer_id": "generated.roads"},
        {"layer_id": "generated.roads", "run_id": RUN, "dataset_version_id": DATASET},
        {"layer_id": "project.boundary", "dataset_version_id": None},
        {"layer_id": "analysis.suitability", "dataset_version_id": None},
        {"layer_id": "validation.violations", "run_id": RUN, "dataset_version_id": None},
    ):
        with pytest.raises(VectorLayerQueryError):
            _read(service, **options)
    with pytest.raises(VectorLayerNotFoundError):
        _read(service, layer_id="source.fake")
    assert repo.last_entry is None


@pytest.mark.parametrize(
    "options",
    (
        {"limit": 0},
        {"limit": MAX_VECTOR_LIMIT + 1},
        {"limit": True},
        {"limit": 1.25},
        {"presentation_srid": 3857},
        {"presentation_srid": True},
        {"bbox_text": "-181,51,0,52"},
        {"bbox_text": "1,51,-1,52"},
        {"bbox_text": "nan,51,1,52"},
        {"simplify_m": -0.01},
        {"simplify_m": 100.001},
        {"simplify_m": float("nan")},
        {"simplify_m": float("inf")},
        {"simplify_m": True},
        {"after": "invalid"},
    ),
)
def test_validation_rejects_unbounded_or_ambiguous_query(options: dict[str, object]) -> None:
    repo = FakeRepository()
    with pytest.raises(VectorLayerQueryError):
        _read(VectorLayerQueryService(repo), **options)
    assert repo.last_entry is None


def test_simplification_requires_metric_working_crs_but_zero_tolerance_does_not() -> None:
    repo = FakeRepository(context=VectorLayerContext(working_srid=4326))
    service = VectorLayerQueryService(repo)
    with pytest.raises(VectorLayerQueryError, match="metre-unit"):
        _read(service, simplify_m=1)
    repo.context = None
    with pytest.raises(VectorLayerNotFoundError):
        _read(service)


def test_all_table_backed_catalog_entries_have_a_fixed_sql_adapter() -> None:
    from backend.app.application.layer_catalog import CANONICAL_LAYER_DEFINITIONS
    from backend.app.db.vector_layer_query_repository import _VECTOR_TABLES

    assert len(_VECTOR_TABLES) == 15
    assert set(_VECTOR_TABLES) == {
        definition.layer_id for definition in CANONICAL_LAYER_DEFINITIONS
        if definition.layer_id not in {
            "project.boundary", "analysis.suitability", "validation.violations",
        }
    }
    assert all(spec.table.c.id is not None for spec in _VECTOR_TABLES.values())
