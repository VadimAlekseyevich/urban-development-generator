import uuid

import pytest
from fastapi.testclient import TestClient

from backend.app.api.dependencies import get_suitability_layer_service
from backend.app.application.suitability_layers import (
    SuitabilityArtifactNotFoundError,
    SuitabilityFactorMetadata,
    SuitabilityLayerMetadata,
    SuitabilityLayerStatistics,
    SuitabilityPreview,
)
from backend.app.main import app


class FakeSuitabilityLayerService:
    def __init__(self, *, missing: bool = False) -> None:
        self.missing = missing

    def get_metadata(self, *, artifact_id: uuid.UUID) -> SuitabilityLayerMetadata:
        if self.missing:
            raise SuitabilityArtifactNotFoundError(artifact_id)
        return SuitabilityLayerMetadata(
            artifact_id=artifact_id,
            checksum="sha256:" + "a" * 64,
            size_bytes=1024,
            content_type="image/tiff",
            schema_version="suitability-artifact-v1",
            config_version="suitability-v1",
            config_fingerprint="b" * 64,
            working_srid=3857,
            working_bounds=(0.0, 0.0, 1000.0, 1000.0),
            width=10,
            height=10,
            image_coordinates_wgs84=(
                (0.0, 0.01),
                (0.01, 0.01),
                (0.01, 0.0),
                (0.0, 0.0),
            ),
            wgs84_bounds=(0.0, 0.0, 0.01, 0.01),
            statistics=SuitabilityLayerStatistics(
                total_cells=100,
                valid_cells=75,
                hard_excluded_cells=20,
                invalid_data_cells=5,
                minimum_score_threshold=0.4,
                preferred_score_threshold=0.8,
                meets_minimum_cells=50,
                preferred_cells=10,
                min_score=0.1,
                max_score=0.95,
                mean_score=0.55,
                p05_score=0.2,
                p50_score=0.56,
                p95_score=0.9,
            ),
            factors=(
                SuitabilityFactorMetadata(
                    code="landuse",
                    version="landuse-v1",
                    weight=1.0,
                    normalization="identity",
                    raw_min=None,
                    raw_max=None,
                ),
            ),
            hard_exclusion_source_codes=("boundary", "water"),
        )

    def render_preview(
        self,
        *,
        artifact_id: uuid.UUID,
        max_dimension: int,
    ) -> SuitabilityPreview:
        if self.missing:
            raise SuitabilityArtifactNotFoundError(artifact_id)
        return SuitabilityPreview(
            content=b"\x89PNG\r\n\x1a\npreview",
            width=min(max_dimension, 10),
            height=min(max_dimension, 10),
            source_checksum="sha256:" + "a" * 64,
        )


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_metadata_and_preview_endpoints_expose_map_contract() -> None:
    artifact_id = uuid.uuid4()
    service = FakeSuitabilityLayerService()
    app.dependency_overrides[get_suitability_layer_service] = lambda: service
    client = TestClient(app)

    metadata_response = client.get(f"/api/v1/suitability-artifacts/{artifact_id}")
    assert metadata_response.status_code == 200
    body = metadata_response.json()
    assert body["artifact_id"] == str(artifact_id)
    assert body["schema_version"] == "suitability-artifact-v1"
    assert body["working_srid"] == 3857
    assert body["statistics"]["valid_cells"] == 75
    assert body["factors"][0]["code"] == "landuse"
    assert body["hard_exclusion_source_codes"] == ["boundary", "water"]

    preview_response = client.get(
        f"/api/v1/suitability-artifacts/{artifact_id}/preview.png",
        params={"max_dimension": 512},
    )
    assert preview_response.status_code == 200
    assert preview_response.headers["content-type"] == "image/png"
    assert preview_response.headers["cache-control"].endswith("immutable")
    assert preview_response.headers["x-image-width"] == "10"
    assert preview_response.content.startswith(b"\x89PNG")


def test_suitability_endpoint_maps_missing_artifact_to_404() -> None:
    artifact_id = uuid.uuid4()
    app.dependency_overrides[get_suitability_layer_service] = lambda: (
        FakeSuitabilityLayerService(missing=True)
    )

    response = TestClient(app).get(f"/api/v1/suitability-artifacts/{artifact_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Suitability artifact not found"


def test_preview_endpoint_enforces_dimension_bounds_before_service_call() -> None:
    artifact_id = uuid.uuid4()
    app.dependency_overrides[get_suitability_layer_service] = lambda: (
        FakeSuitabilityLayerService()
    )

    response = TestClient(app).get(
        f"/api/v1/suitability-artifacts/{artifact_id}/preview.png",
        params={"max_dimension": 64},
    )
    assert response.status_code == 422
