import uuid
from datetime import UTC, datetime

import pytest

from backend.app.application.metric_dashboard import (
    MetricDashboardDataError,
    MetricDashboardProjectNotFoundError,
    MetricDashboardQueryService,
    MetricDashboardRunRecord,
    MetricDashboardUnavailableError,
)
from backend.app.db.run_metrics_writer import composite_score_payload
from core.urban_generator.domain.benchmarking import (
    MetricDirection,
    MetricScope,
    RawMetricId,
)
from core.urban_generator.metrics.score import (
    CompositeScoreMetricResult,
    CompositeScoreResult,
)


def _score_result() -> CompositeScoreResult:
    return CompositeScoreResult(
        score=0.75,
        score_config_id="dashboard.default",
        score_config_version="1",
        normalization_profile_id="dashboard.profile",
        normalization_profile_version="1",
        metrics=(
            CompositeScoreMetricResult(
                metric_id=RawMetricId.LAND_DEVELOPED_AREA_M2,
                raw_value=125000.0,
                normalized_value=0.8,
                normalization_policy_version="1",
                configured_weight=2.0,
                normalized_weight=0.5,
                contribution=0.4,
                was_clamped=False,
                was_missing=False,
            ),
            CompositeScoreMetricResult(
                metric_id=RawMetricId.ROADS_CONNECTED_COMPONENTS,
                raw_value=2.0,
                normalized_value=0.7,
                normalization_policy_version="1",
                configured_weight=2.0,
                normalized_weight=0.5,
                contribution=0.35,
                was_clamped=False,
                was_missing=False,
            ),
        ),
    )


class FakeMetricDashboardRepository:
    def __init__(self) -> None:
        self.project_id = uuid.uuid4()
        self.run_id = uuid.uuid4()
        self.metrics_json: dict[str, object] | None = {
            "evaluation": composite_score_payload(_score_result())
        }

    def project_exists(self, *, project_id: uuid.UUID) -> bool:
        return project_id == self.project_id

    def list_metric_runs(
        self,
        *,
        project_id: uuid.UUID,
        limit: int,
    ) -> list[MetricDashboardRunRecord]:
        if project_id != self.project_id:
            return []
        return [self._record()][:limit]

    def get_run(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> MetricDashboardRunRecord | None:
        if project_id != self.project_id or run_id != self.run_id:
            return None
        return self._record()

    def _record(self) -> MetricDashboardRunRecord:
        return MetricDashboardRunRecord(
            id=self.run_id,
            project_id=self.project_id,
            status="succeeded",
            mode="EXPANSION",
            seed=60,
            working_srid=32637,
            metrics_json=self.metrics_json,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            finished_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        )


def test_metric_dashboard_projects_persisted_score_with_registry_metadata() -> None:
    repository = FakeMetricDashboardRepository()
    service = MetricDashboardQueryService(repository)

    runs = service.list_runs(project_id=repository.project_id)
    assert runs.truncated is False
    assert len(runs.runs) == 1
    assert runs.runs[0].composite_score == pytest.approx(0.75)
    assert runs.runs[0].metric_count == 2

    dashboard = service.get_dashboard(
        project_id=repository.project_id,
        run_id=repository.run_id,
    )
    assert dashboard.composite_score == pytest.approx(0.75)
    land, roads = dashboard.metrics
    assert land.metric_id is RawMetricId.LAND_DEVELOPED_AREA_M2
    assert land.unit == "m2"
    assert land.scope is MetricScope.LAND
    assert land.direction is MetricDirection.TARGET
    assert land.raw_value == pytest.approx(125000.0)
    assert land.normalized_value == pytest.approx(0.8)
    assert land.normalized_weight == pytest.approx(0.5)
    assert land.contribution == pytest.approx(0.4)
    assert roads.metric_id is RawMetricId.ROADS_CONNECTED_COMPONENTS
    assert roads.unit == "count"
    assert roads.direction is MetricDirection.LOWER_IS_BETTER


def test_metric_dashboard_distinguishes_missing_evaluation_from_bad_data() -> None:
    repository = FakeMetricDashboardRepository()
    service = MetricDashboardQueryService(repository)

    repository.metrics_json = {"demography": {"total_population": 10}}
    with pytest.raises(MetricDashboardUnavailableError, match="evaluation"):
        service.get_dashboard(
            project_id=repository.project_id,
            run_id=repository.run_id,
        )

    malformed = composite_score_payload(_score_result())
    malformed["schema_version"] = "999"
    repository.metrics_json = {"evaluation": malformed}
    with pytest.raises(MetricDashboardDataError, match="schema_version"):
        service.get_dashboard(
            project_id=repository.project_id,
            run_id=repository.run_id,
        )


def test_metric_dashboard_rejects_unknown_project_and_bad_limit() -> None:
    repository = FakeMetricDashboardRepository()
    service = MetricDashboardQueryService(repository)

    with pytest.raises(MetricDashboardProjectNotFoundError):
        service.list_runs(project_id=uuid.uuid4())

    with pytest.raises(ValueError, match="run limit"):
        service.list_runs(project_id=repository.project_id, limit=0)
