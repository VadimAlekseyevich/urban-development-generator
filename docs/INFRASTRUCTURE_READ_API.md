# Infrastructure read API

> **Status: S10-T12 read/UI contract implemented through UG-AI-040**

S10-T12 exposes run-scoped infrastructure state for map/API consumers without moving authoritative
computation into the frontend.

## Run-scoped fixed/generated view

A selected `GenerationRun` defines both halves of the facility view:

- **existing** facilities are rows from `source_facilities` whose `dataset_version_id` belongs
  to the run's immutable `generation_run_dataset_versions` set;
- **generated** facilities are rows from `generated_infrastructure` with the selected `run_id`.

The API never reads every source facility in a project and never guesses which dataset version was
active. An unlinked source dataset version is invisible to that run.

Every facility GeoJSON feature carries explicit `origin = existing | generated`. Consumers do not
infer fixed/generated ownership from ids, classes or geometry.

## Authoritative S10 presentation read-model

UG-AI-040 materializes already-computed T03/T07/T08/T11 outputs into existing run-scoped JSON
extension points. It does not introduce a second infrastructure domain model and performs no
routing during reads.

`SqlAlchemyInfrastructureUiReadModelWriter` writes, before a run reaches `succeeded`:

- `GeneratedBlock.attributes_json.infrastructure` — gross, served and final unmet demand,
  aggregate coverage ratio and per-infrastructure-type demand rows;
- `GeneratedInfrastructure.attributes_json.accessibility` — T07 reachability summary for an
  accepted generated facility: reachable demand count plus nearest/farthest cached network
  distance and authoritative max network distance;
- `GenerationRun.metrics_json.infrastructure` — canonical T11 `RawMetricId` values,
  diagnostics and existing/generated facility accessibility summaries.

The writer validates T03/T07/T08 alignment through the existing `InfrastructureMetricsBuilder`,
locks the run-scoped rows it mutates, preserves other JSON namespaces, is retry-safe for identical
inputs and rejects a successful run before persistence mutation.

Existing source facilities remain immutable. Their T07 accessibility summaries live only in the
run-level read-model and are joined by stable source identity in the client.

## Endpoints

- `GET /api/v1/projects/{project_id}/infrastructure-runs`
  lists project runs with existing/generated facility counts.
- `GET /api/v1/projects/{project_id}/infrastructure-runs/{run_id}/facilities/geojson`
  returns a bounded EPSG:4326 FeatureCollection for a required viewport bbox. Optional
  `origin=existing|generated` gives each origin its own bound.
- `GET /api/v1/projects/{project_id}/infrastructure-runs/{run_id}/metrics`
  returns the persisted S10 T11 raw metrics, diagnostics and facility reachability summaries.
- `GET /api/v1/projects/{project_id}/infrastructure-runs/{run_id}/demand/geojson`
  returns bounded generated-block geometry with persisted final demand/coverage properties.

The two read-model endpoints return HTTP 409 when the run exists but its S10 presentation
read-model has not yet been materialized. A run/project mismatch remains HTTP 404.

Spatial endpoints accept `bbox=west,south,east,north` in EPSG:4326, transform the envelope to the
run working SRID for PostGIS filtering, and transform output geometry back to EPSG:4326. Default
limit is 1500 and hard API limit is 5000.

## Frontend contract

The S10 Infrastructure panel:

- selects a persisted infrastructure run;
- reads fixed and generated facilities independently so one origin cannot starve the other under
  truncation;
- styles generated accepted sites/host anchors by infrastructure category;
- renders final unmet demand as a block choropleth;
- shows canonical population coverage, unmet demand, p50/p90 network distance and capacity
  utilization from the persisted T11 read-model;
- shows selected-facility network snap provenance and persisted T07 reachability summary.

The browser never calls `NetworkBackend`, reconstructs candidate×demand matrices, reruns greedy
placement or derives canonical metrics from map features.

## UI state semantics

UG-AI-041 keeps delivery failures and readiness states explicit instead of hiding them behind
client-side fallback computation:

- **run list** has independent idle/loading/ready/error state and an explicit refresh action;
- selected `infrastructure_run_id` is persisted in the URL. A stale/missing requested run is
  replaced deterministically with an available project run and the URL is corrected;
- **read model** has independent loading/ready/not-ready/error state. HTTP 409 is rendered as
  `not-ready`: fixed/generated facilities remain available while demand/metrics stay disabled;
- **viewport** uses independent per-layer reads. One failed layer does not discard successful
  fixed/generated/demand layers; the panel reports partial success and offers a viewport retry;
- truncation is reported by exact layer name rather than as one ambiguous panel-level flag;
- retries only repeat reads. They never execute snapping, routing, placement or metrics in React.

These states preserve the backend as the only authority while making incomplete/stale UI data
visible to the operator.

## Explicit non-goals

This read contract does not expose every rejected/unaccepted candidate alternative or a full
candidate×demand accessibility matrix. Those bounded core structures remain execution inputs, not
a second browser-side authoritative state.

UG-AI-041 hardens explicit error/truncation/run-selection states; later workspace generalization remains S13 work.
