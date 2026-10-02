# Canonical Raw-Metrics CSV Export

## Scope

S13-T09 exposes one bounded synchronous CSV export for persisted canonical raw metrics.

- endpoint: `POST /api/v1/projects/{project_id}/exports/metrics.csv`;
- request: `{"run_ids": ["..."]}`;
- cardinality: **1–10 unique runs** from the same project;
- only immutable `succeeded` runs are exportable;
- values are decoded from the existing persisted S11 evaluation envelope;
- no GIS, validation, normalization, score, ranking or delta is recomputed;
- no second metric registry or export-specific metric identifiers are introduced.

The export is synchronous because the bounded payload is at most the canonical metric registry multiplied
by ten runs. Heavy spatial exports remain asynchronous GeoJSON/GeoPackage jobs.

## CSV contract

Schema version: `raw-metrics-csv-v1`.

Columns are stable and long-form:

| Column | Meaning |
| --- | --- |
| `schema_version` | CSV contract version |
| `project_id` | owning project |
| `run_id` | generation run |
| `run_order` | 1-based order from the request |
| `metric_id` | canonical `RawMetricId` |
| `unit` | canonical registry unit |
| `scope` | canonical metric scope |
| `direction` | canonical interpretation direction |
| `source` | canonical producer family |
| `value_kind` | scalar/distribution registry metadata |
| `definition_version` | canonical metric-definition version |
| `metric_present` | whether the run's persisted evaluation contains this metric |
| `raw_value` | persisted raw scalar value; blank when unavailable |

Rows are deterministic: canonical registry order first, then request run order. A metric is emitted when at
least one requested run contains it. This keeps comparison rows adjacent while distinguishing an absent
metric (`metric_present=false`) from a persisted metric whose raw value is missing.

## Failure semantics

- `404`: project/run missing or run outside the project;
- `409`: run is not successful or has no persisted evaluation;
- `422`: invalid/duplicate/out-of-range run selection;
- `500`: persisted evaluation violates the canonical decoder contract.

The response is UTF-8 `text/csv`, includes `Content-Disposition`, and publishes the schema version in
`X-Metrics-Csv-Schema`.
