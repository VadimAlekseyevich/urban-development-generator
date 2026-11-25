import uuid

import pytest

from backend.app.application.road_layers import (
    GeneratedRoadFeature,
    RoadDiagnosticEdge,
    RoadLayerProjectNotFoundError,
    RoadLayerQueryService,
    RoadLayerRunNotFoundError,
    RoadRunContext,
    RoadRunSummary,
)
from backend.app.application.source_layers import SourceLayerBbox


class FakeRoadRepository:
    def __init__(self) -> None:
        self.project_id = uuid.uuid4()
        self.run_id = uuid.uuid4()

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        return project_id == self.project_id

    def list_runs(self, *, project_id: uuid.UUID) -> list[RoadRunSummary]:
        return []

    def get_run_context(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> RoadRunContext | None:
        if project_id != self.project_id or run_id != self.run_id:
            return None
        return RoadRunContext(
            project_id=project_id,
            run_id=run_id,
            working_srid=3857,
            status="running",
        )

    def list_generated_roads(
        self,
        *,
        run_id: uuid.UUID,
        working_srid: int,
        bbox: SourceLayerBbox,
        limit: int,
    ) -> list[GeneratedRoadFeature]:
        return []

    def list_diagnostic_edges(self, *, run_id: uuid.UUID) -> list[RoadDiagnosticEdge]:
        return [
            RoadDiagnosticEdge("road-a", "n1", "n2", 100.0, "local", "growth_generated"),
            RoadDiagnosticEdge("road-a", "n2", "n3", 150.0, "collector", "growth_generated"),
            RoadDiagnosticEdge("road-b", "n4", "n5", 50.0, "local", "baseline_generated"),
        ]


def test_road_diagnostics_are_stable_and_graph_based() -> None:
    repository = FakeRoadRepository()
    service = RoadLayerQueryService(repository)

    result = service.get_diagnostics(
        project_id=repository.project_id,
        run_id=repository.run_id,
    )

    assert result.edge_count == 3
    assert result.road_count == 2
    assert result.node_count == 5
    assert result.component_count == 2
    assert result.dead_end_node_count == 4
    assert result.dead_end_ratio == pytest.approx(0.8)
    assert result.total_length_m == 300.0
    assert result.class_counts == {"collector": 1, "local": 2}
    assert result.origin_counts == {"baseline_generated": 1, "growth_generated": 2}


def test_road_service_enforces_project_and_run_scope() -> None:
    repository = FakeRoadRepository()
    service = RoadLayerQueryService(repository)

    with pytest.raises(RoadLayerProjectNotFoundError):
        service.list_runs(project_id=uuid.uuid4())

    with pytest.raises(RoadLayerRunNotFoundError):
        service.get_diagnostics(
            project_id=repository.project_id,
            run_id=uuid.uuid4(),
        )
