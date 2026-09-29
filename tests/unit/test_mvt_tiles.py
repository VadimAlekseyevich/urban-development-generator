"""S13-T03 MVT contract: exact owner, XYZ, limits and cache-safe errors."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from backend.app.application.layer_catalog import (
    CANONICAL_LAYER_DEFINITIONS,
    LayerCatalogEntry,
)
from backend.app.application.mvt_tiles import (
    MVT_DEFAULT_FEATURE_LIMIT,
    MVT_MAX_FEATURE_LIMIT,
    MVT_MAX_TILE_BYTES,
    MVT_MAX_ZOOM,
    MVT_MEDIA_TYPE,
    MVT_IMMUTABLE_CACHE_CONTROL,
    MVT_VOLATILE_CACHE_CONTROL,
    matches_if_none_match,
    MVT_SCHEMA_VERSION,
    MvtTileQueryService,
    MvtTileTooLargeError,
)
from backend.app.application.vector_layers import (
    VectorLayerContext,
    VectorLayerNotFoundError,
    VectorLayerNotReadyError,
    VectorLayerQueryError,
)
from backend.app.db.vector_layer_query_repository import _VECTOR_TABLES

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
VERSION = uuid.UUID("22222222-2222-4222-8222-222222222222")
RUN = uuid.UUID("33333333-3333-4333-8333-333333333333")


@dataclass
class FakeMvtRepository:
    context: VectorLayerContext | None = VectorLayerContext(working_srid=3857)
    payload: bytes = b"mvt"
    candidates: int = 1
    entries: list[LayerCatalogEntry] = field(default_factory=list)
    calls: list[tuple[int, int, int, int, int]] = field(default_factory=list)

    def get_context(self, entry: LayerCatalogEntry) -> VectorLayerContext | None:
        self.entries.append(entry)
        return self.context

    def encode_tile(
        self,
        *,
        entry: LayerCatalogEntry,
        working_srid: int,
        z: int,
        x: int,
        y: int,
        feature_limit: int,
    ) -> tuple[bytes, int]:
        assert self.entries[-1] == entry
        self.calls.append((working_srid, z, x, y, feature_limit))
        return self.payload, self.candidates


def _read(service: MvtTileQueryService, **changes: object) -> object:
    request: dict[str, object] = {
        "project_id": PROJECT,
        "layer_id": "source.roads",
        "dataset_version_id": VERSION,
        "run_id": None,
        "z": 10,
        "x": 511,
        "y": 340,
    }
    request.update(changes)
    return service.get_tile(**request)  # type: ignore[arg-type]


def test_bound_source_and_run_tiles_return_exact_metadata() -> None:
    repo = FakeMvtRepository(candidates=MVT_DEFAULT_FEATURE_LIMIT + 1)
    service = MvtTileQueryService(repo)
    tile = _read(service)
    assert tile.schema_version == MVT_SCHEMA_VERSION == "mvt-tile-v1"
    assert MVT_MEDIA_TYPE == "application/vnd.mapbox-vector-tile"
    assert tile.layer_id == "source.roads"
    assert tile.bytes == b"mvt"
    assert tile.truncated is True
    assert tile.candidate_count == MVT_DEFAULT_FEATURE_LIMIT + 1
    assert tile.feature_limit == MVT_DEFAULT_FEATURE_LIMIT
    assert repo.calls == [(3857, 10, 511, 340, MVT_DEFAULT_FEATURE_LIMIT)]
    assert repo.entries[-1].owner.project_id == PROJECT
    assert repo.entries[-1].owner.dataset_version_id == VERSION
    assert repo.entries[-1].owner.run_id is None

    repo.candidates = 1
    run_tile = _read(
        service,
        layer_id="generated.roads",
        dataset_version_id=None,
        run_id=RUN,
        feature_limit=2,
    )
    assert run_tile.truncated is False
    assert repo.entries[-1].owner.run_id == RUN
    assert repo.entries[-1].owner.dataset_version_id is None


def test_catalog_table_coverage_and_non_tabular_exclusions() -> None:
    expected = {
        item.layer_id
        for item in CANONICAL_LAYER_DEFINITIONS
        if item.layer_id not in {
            "analysis.suitability", "project.boundary", "validation.violations",
        }
    }
    assert len(expected) == 15
    assert set(_VECTOR_TABLES) == expected


@pytest.mark.parametrize(
    "changes",
    (
        {"layer_id": "source.imaginary"},
        {"layer_id": "generated.roads"},
        {"layer_id": "generated.roads", "run_id": RUN},
        {"layer_id": "generated.roads", "run_id": RUN, "dataset_version_id": VERSION},
        {"dataset_version_id": None},
        {"run_id": RUN},
        {"layer_id": "validation.violations", "dataset_version_id": None, "run_id": RUN},
        {"layer_id": "analysis.suitability"},
        {"layer_id": "project.boundary"},
        {"z": -1},
        {"z": MVT_MAX_ZOOM + 1},
        {"z": True},
        {"x": -1},
        {"y": -1},
        {"x": 1024},
        {"y": 1024},
        {"x": 1.5},
        {"y": "340"},
        {"feature_limit": 0},
        {"feature_limit": MVT_MAX_FEATURE_LIMIT + 1},
        {"feature_limit": True},
        {"feature_limit": 1.5},
    ),
)
def test_invalid_requests_are_rejected_before_repository(
    changes: dict[str, object],
) -> None:
    repo = FakeMvtRepository()
    service = MvtTileQueryService(repo)
    if changes.get("layer_id") == "source.imaginary":
        with pytest.raises(VectorLayerNotFoundError):
            _read(service, **changes)
    else:
        with pytest.raises(VectorLayerQueryError):
            _read(service, **changes)
    assert repo.entries == [] and repo.calls == []


def test_zoom_extremes_and_overflow_tile_bytes_and_candidate_invariants() -> None:
    repo = FakeMvtRepository(payload=b"", candidates=0)
    service = MvtTileQueryService(repo)
    assert _read(service, z=0, x=0, y=0).bytes == b""
    assert _read(
        service, z=MVT_MAX_ZOOM,
        x=(1 << MVT_MAX_ZOOM) - 1, y=(1 << MVT_MAX_ZOOM) - 1,
    ).truncated is False
    repo.payload = b"x" * (MVT_MAX_TILE_BYTES + 1)
    with pytest.raises(MvtTileTooLargeError):
        _read(service)
    repo.payload = b""
    repo.candidates = MVT_DEFAULT_FEATURE_LIMIT + 2
    with pytest.raises(ValueError, match="unbounded"):
        _read(service)


def test_missing_owner_and_unpublished_derived_read_model() -> None:
    repo = FakeMvtRepository(context=None)
    service = MvtTileQueryService(repo)
    with pytest.raises(VectorLayerNotFoundError):
        _read(service)
    assert repo.calls == []
    repo.context = VectorLayerContext(working_srid=3857, read_model_ready=False)
    with pytest.raises(VectorLayerNotReadyError):
        _read(
            service, layer_id="generated.demography",
            dataset_version_id=None, run_id=RUN,
        )
    assert repo.calls == []


def test_published_tile_has_deterministic_owner_and_render_qualified_strong_etag() -> None:
    repo = FakeMvtRepository(
        context=VectorLayerContext(working_srid=3857, immutable=True),
        payload=b"tile-payload",
        candidates=3,
    )
    service = MvtTileQueryService(repo)
    tile = _read(service)
    assert tile.cache_control == MVT_IMMUTABLE_CACHE_CONTROL
    assert tile.etag is not None
    assert tile.etag.startswith('"') and tile.etag.endswith('"')
    assert len(tile.etag) == 66  # 64 hex bytes and two quotation marks
    assert _read(service).etag == tile.etag

    assert _read(service, x=510).etag != tile.etag
    assert _read(service, feature_limit=20).etag != tile.etag
    assert _read(service, dataset_version_id=uuid.UUID(int=44)).etag != tile.etag
    assert _read(
        service, layer_id="generated.roads",
        dataset_version_id=None, run_id=RUN,
    ).etag != tile.etag
    repo.candidates = 4
    assert _read(service).etag != tile.etag
    repo.candidates = 3
    repo.payload = b"new-rendering"
    assert _read(service).etag != tile.etag


def test_http_if_none_match_uses_weak_get_comparison_without_partial_matches() -> None:
    repo = FakeMvtRepository(
        context=VectorLayerContext(working_srid=3857, immutable=True),
    )
    etag = _read(MvtTileQueryService(repo)).etag
    assert etag is not None
    for valid in (etag, "W/" + etag, ' "unrelated", ' + etag, "*"):
        assert matches_if_none_match(valid, etag)
    for invalid in (
        None, "", "W/", '"unrelated"', etag[:-1], etag + "wrong",
        "prefix" + etag, '"abc" + ' + etag, 'W/ "broken"',
    ):
        assert not matches_if_none_match(invalid, etag)
    assert not matches_if_none_match(etag, None)
    assert not matches_if_none_match("*", None)


def test_volatile_tile_never_advertises_etag_or_immutable_caching() -> None:
    repo = FakeMvtRepository(
        context=VectorLayerContext(working_srid=3857, immutable=False),
    )
    tile = _read(MvtTileQueryService(repo))
    assert tile.cache_control == MVT_VOLATILE_CACHE_CONTROL
    assert tile.etag is None
    assert not matches_if_none_match("*", tile.etag)


def test_tile_exceeding_byte_budget_is_rejected_before_cache_validator_creation() -> None:
    repo = FakeMvtRepository(
        context=VectorLayerContext(working_srid=3857, immutable=True),
        payload=b"x" * (MVT_MAX_TILE_BYTES + 1),
    )
    with pytest.raises(MvtTileTooLargeError):
        _read(MvtTileQueryService(repo))
