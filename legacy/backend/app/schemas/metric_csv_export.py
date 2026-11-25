"""Transport model for bounded canonical raw-metric CSV export."""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from backend.app.application.metrics_csv_exports import (
    MAX_METRIC_CSV_RUNS,
    MIN_METRIC_CSV_RUNS,
)


class MetricCsvExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: tuple[uuid.UUID, ...] = Field(
        min_length=MIN_METRIC_CSV_RUNS,
        max_length=MAX_METRIC_CSV_RUNS,
    )
