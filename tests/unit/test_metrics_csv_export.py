"""Pure contract tests for bounded canonical raw-metric CSV export."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime

import pytest

from backend.app.application.metric_dashboard import MetricDashboardRunRecord
from backend.app.application.metrics_csv_exports import (
    METRIC_CSV_SCHEMA_VERSION,
    MetricCsvExportDataError,
    MetricCsvExportProjectNotFoundError,
    MetricCsvExportQueryError,
    MetricCsvExportRunNotFoundError,
    MetricCsvExportService,
    MetricCsvExportUnavailableError,
)
from core.urban_generator.domain.benchmarking import RawMetricId


def _metric(metric_id: RawMetricId, raw_value: float | None) -> dict[str, object]:
    return {
        "metric_id": metric_id.value,
        "raw_value": raw_value,
        "normalized_value": 0.5 if raw_value is not None else None,
        "normalization_policy_version": "1",
        "configured_weight": 1.0,
        "normalized_weight": 1.0,
        "contribution": 0.5 if raw_value is not None else 0.0,
        "was_clamped": False,
        "was_missing": raw_value is None,
    }


def _record(
    run_id: uuid.UUID,
    *,
    status: str = "succeeded",
    metrics: list[dict[str, object]] | None = None,
    malformed: bool = False,
) -> MetricDashboardRunRecord:
    evaluation: object
    if malformed:
        evaluation = {"schema_version": "unsupported"}
    else:
        evaluation = {
            "schema_version": "1",
            "composite_score": 0.5,
            "score_config": {"id": "test", "version": "1"},
            "normalization_profile": {"id": "test", "version": "1"},
            "metrics": metrics
            or [_metric(RawMetricId.LAND_DEVELOPED_AREA_M2, 10.0)],
        }
    return MetricDashboardRunRecord(
        id=run_id,
        project_id=PROJECT_ID,
        status=status,
        mode="EXPANSION",
        seed=1,
        working_srid=32637,
        metrics_json={"evaluation": evaluation},
        created_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )


PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class FakeRepository:
    def __init__(
        self,
        records: tuple[MetricDashboardRunRecord, ...] = (),
        *,
        project_exists: bool = True,
    ) -> None:
        self.records = records
        self.exists = project_exists
        self.reads = 0

    def get_runs(self, *, project_id, run_ids):
        self.reads += 1
        return list(self.records)

    def project_exists(self, *, project_id):
        return self.exists


def test_export_is_registry_ordered_and_preserves_requested_run_order() -> None:
    first = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    second = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    repository = FakeRepository(
        (
            _record(
                second,
                metrics=[_metric(RawMetricId.LAND_DEVELOPED_AREA_M2, 20.0)],
            ),
            _record(
                first,
                metrics=[
                    _metric(RawMetricId.LAND_DEVELOPED_AREA_M2, 10.0),
                    _metric(RawMetricId.ROADS_CONNECTED_COMPONENTS, 2.0),
                ],
            ),
        )
    )

    result = MetricCsvExportService(repository).export(
        project_id=PROJECT_ID,
        run_ids=(first, second),
    )

    assert result.schema_version == METRIC_CSV_SCHEMA_VERSION
    assert result.run_ids == (first, second)
    assert result.filename == "raw-metrics-compare-2-runs.csv"

    rows = list(csv.DictReader(io.StringIO(result.content.decode("utf-8"))))
    assert [(row["metric_id"], row["run_id"]) for row in rows] == [
        ("land.developed_area_m2", str(first)),
        ("land.developed_area_m2", str(second)),
        ("roads.connected_components", str(first)),
        ("roads.connected_components", str(second)),
    ]
    assert [row["run_order"] for row in rows] == ["1", "2", "1", "2"]
    assert [row["raw_value"] for row in rows] == ["10.0", "20.0", "2.0", ""]
    assert [row["metric_present"] for row in rows] == ["true", "true", "true", "false"]
    assert rows[0]["unit"] == "m2"
    assert rows[0]["scope"] == "LAND"
    assert rows[2]["direction"] == "LOWER_IS_BETTER"
    assert all(row["schema_version"] == METRIC_CSV_SCHEMA_VERSION for row in rows)


def test_single_run_export_does_not_require_validation_data() -> None:
    run_id = uuid.uuid4()
    record = _record(run_id)
    result = MetricCsvExportService(FakeRepository((record,))).export(
        project_id=PROJECT_ID,
        run_ids=(run_id,),
    )
    assert result.filename == f"raw-metrics-{run_id}.csv"
    rows = list(csv.DictReader(io.StringIO(result.content.decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["run_id"] == str(run_id)


def test_invalid_run_selection_fails_before_repository_read() -> None:
    repository = FakeRepository()
    service = MetricCsvExportService(repository)
    run_id = uuid.uuid4()

    for run_ids in (
        (),
        (run_id, run_id),
        tuple(uuid.uuid4() for _ in range(11)),
        ("not-a-uuid",),
    ):
        with pytest.raises(MetricCsvExportQueryError, match="unique UUIDs"):
            service.export(project_id=PROJECT_ID, run_ids=run_ids)  # type: ignore[arg-type]

    assert repository.reads == 0


def test_scope_availability_and_data_errors_are_explicit() -> None:
    run_id = uuid.uuid4()

    with pytest.raises(MetricCsvExportProjectNotFoundError):
        MetricCsvExportService(FakeRepository(project_exists=False)).export(
            project_id=PROJECT_ID,
            run_ids=(run_id,),
        )
    with pytest.raises(MetricCsvExportRunNotFoundError):
        MetricCsvExportService(FakeRepository()).export(
            project_id=PROJECT_ID,
            run_ids=(run_id,),
        )
    with pytest.raises(MetricCsvExportUnavailableError, match="successful"):
        MetricCsvExportService(
            FakeRepository((_record(run_id, status="running"),))
        ).export(project_id=PROJECT_ID, run_ids=(run_id,))
    unavailable = MetricDashboardRunRecord(
        id=run_id,
        project_id=PROJECT_ID,
        status="succeeded",
        mode="EXPANSION",
        seed=1,
        working_srid=32637,
        metrics_json=None,
        created_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    with pytest.raises(MetricCsvExportUnavailableError, match="persisted evaluation"):
        MetricCsvExportService(FakeRepository((unavailable,))).export(
            project_id=PROJECT_ID,
            run_ids=(run_id,),
        )
    with pytest.raises(MetricCsvExportDataError, match="invalid persisted evaluation"):
        MetricCsvExportService(FakeRepository((_record(run_id, malformed=True),))).export(
            project_id=PROJECT_ID,
            run_ids=(run_id,),
        )
