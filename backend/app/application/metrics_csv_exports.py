"""Bounded synchronous export of persisted canonical raw metrics to stable CSV."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from backend.app.application.metric_dashboard import (
    MetricDashboardDataError,
    MetricDashboardRunRecord,
    MetricDashboardUnavailableError,
    decode_persisted_evaluation,
)
from core.urban_generator.domain.benchmarking import CANONICAL_METRIC_REGISTRY

MIN_METRIC_CSV_RUNS = 1
MAX_METRIC_CSV_RUNS = 10
METRIC_CSV_SCHEMA_VERSION = "raw-metrics-csv-v1"
METRIC_CSV_MEDIA_TYPE = "text/csv"


class MetricCsvExportQueryError(ValueError):
    """The requested raw-metric export is not well formed."""


class MetricCsvExportProjectNotFoundError(LookupError):
    def __init__(self, project_id: uuid.UUID) -> None:
        super().__init__(f"project {project_id} was not found")


class MetricCsvExportRunNotFoundError(LookupError):
    """A requested run is missing or belongs to another project."""


class MetricCsvExportUnavailableError(RuntimeError):
    """A requested run is not an immutable successful run with evaluation data."""


class MetricCsvExportDataError(RuntimeError):
    """Persisted canonical metric data is malformed."""


class MetricCsvExportRepository(Protocol):
    def project_exists(self, *, project_id: uuid.UUID) -> bool: ...

    def get_runs(
        self,
        *,
        project_id: uuid.UUID,
        run_ids: tuple[uuid.UUID, ...],
    ) -> Sequence[MetricDashboardRunRecord]: ...


@dataclass(frozen=True, slots=True)
class MetricCsvExportResult:
    schema_version: str
    project_id: uuid.UUID
    run_ids: tuple[uuid.UUID, ...]
    filename: str
    content: bytes


class MetricCsvExportService:
    """Render persisted raw metrics without GIS or score recomputation."""

    def __init__(self, repository: MetricCsvExportRepository) -> None:
        self._repository = repository

    def export(
        self,
        *,
        project_id: uuid.UUID,
        run_ids: tuple[uuid.UUID, ...],
    ) -> MetricCsvExportResult:
        _require_request(project_id=project_id, run_ids=run_ids)

        records = self._repository.get_runs(project_id=project_id, run_ids=run_ids)
        by_id = {record.id: record for record in records}
        if len(by_id) != len(run_ids):
            if not self._repository.project_exists(project_id=project_id):
                raise MetricCsvExportProjectNotFoundError(project_id)
            raise MetricCsvExportRunNotFoundError(
                "one or more requested runs are missing from the project"
            )

        ordered = tuple(by_id[run_id] for run_id in run_ids)
        if any(record.status != "succeeded" for record in ordered):
            raise MetricCsvExportUnavailableError(
                "raw-metric CSV export requires immutable successful generation runs"
            )

        evaluations = []
        for record in ordered:
            try:
                evaluations.append(decode_persisted_evaluation(record))
            except MetricDashboardUnavailableError as exc:
                raise MetricCsvExportUnavailableError(
                    f"run {record.id} has no persisted evaluation"
                ) from exc
            except MetricDashboardDataError as exc:
                raise MetricCsvExportDataError(
                    f"run {record.id} has invalid persisted evaluation"
                ) from exc

        values_by_run = tuple(
            {item.metric_id: item.raw_value for item in evaluation.metrics}
            for evaluation in evaluations
        )
        content = _render_csv(
            project_id=project_id,
            run_ids=run_ids,
            values_by_run=values_by_run,
        )
        return MetricCsvExportResult(
            schema_version=METRIC_CSV_SCHEMA_VERSION,
            project_id=project_id,
            run_ids=run_ids,
            filename=_filename(run_ids),
            content=content,
        )


def _require_request(
    *,
    project_id: uuid.UUID,
    run_ids: tuple[uuid.UUID, ...],
) -> None:
    if not isinstance(project_id, uuid.UUID):
        raise MetricCsvExportQueryError("project_id must be UUID")
    if (
        not isinstance(run_ids, tuple)
        or not MIN_METRIC_CSV_RUNS <= len(run_ids) <= MAX_METRIC_CSV_RUNS
        or any(not isinstance(run_id, uuid.UUID) for run_id in run_ids)
        or len(set(run_ids)) != len(run_ids)
    ):
        raise MetricCsvExportQueryError(
            f"run_ids must contain {MIN_METRIC_CSV_RUNS}–{MAX_METRIC_CSV_RUNS} unique UUIDs"
        )


def _render_csv(
    *,
    project_id: uuid.UUID,
    run_ids: tuple[uuid.UUID, ...],
    values_by_run: tuple[dict[object, float | None], ...],
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        (
            "schema_version",
            "project_id",
            "run_id",
            "run_order",
            "metric_id",
            "unit",
            "scope",
            "direction",
            "source",
            "value_kind",
            "definition_version",
            "metric_present",
            "raw_value",
        )
    )

    for definition in CANONICAL_METRIC_REGISTRY.definitions:
        if not any(definition.metric_id in values for values in values_by_run):
            continue
        for run_order, (run_id, values) in enumerate(
            zip(run_ids, values_by_run, strict=True),
            start=1,
        ):
            present = definition.metric_id in values
            raw_value = values.get(definition.metric_id)
            writer.writerow(
                (
                    METRIC_CSV_SCHEMA_VERSION,
                    str(project_id),
                    str(run_id),
                    run_order,
                    definition.metric_id.value,
                    definition.unit,
                    definition.scope.value,
                    definition.direction.value,
                    definition.source.value,
                    definition.value_kind.value,
                    definition.version,
                    "true" if present else "false",
                    "" if raw_value is None else raw_value,
                )
            )

    return output.getvalue().encode("utf-8")


def _filename(run_ids: tuple[uuid.UUID, ...]) -> str:
    if len(run_ids) == 1:
        return f"raw-metrics-{run_ids[0]}.csv"
    return f"raw-metrics-compare-{len(run_ids)}-runs.csv"
