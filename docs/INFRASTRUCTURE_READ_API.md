# Infrastructure read API

> **Status: Implemented through UG-AI-039 / S10-T12**

S10-T12 exposes run-scoped infrastructure state for map/API consumers without moving authoritative
computation into the frontend.

## Run-scoped fixed/generated view

A selected `GenerationRun` defines both halves of the infrastructure view:

- **existing** facilities are rows from `source_facilities` whose `dataset_version_id` belongs
  to the run's immutable `generation_run_dataset_versions` set;
- **generated** facilities are rows from `generated_infrastructure` with the selected `run_id`.

The API never reads every source facility in a project and never guesses which dataset version was
active. An unlinked source dataset version is invisible to that run.

Every GeoJSON feature carries explicit `origin = existing | generated` both as a typed top-level
field and in properties. Consumers therefore do not infer fixed/generated ownership from ids,
classes or geometry.

## Endpoints

- `GET /api/v1/projects/{project_id}/infrastructure-runs`
  lists project runs with existing/generated facility counts.
- `GET /api/v1/projects/{project_id}/infrastructure-runs/{run_id}/facilities/geojson`
  returns a bounded EPSG:4326 FeatureCollection for a required viewport bbox.

The viewport endpoint follows the existing generated-layer contract: bbox is supplied as
`west,south,east,north` in EPSG:4326, transformed to the run working SRID for PostGIS filtering,
and output geometry is transformed back to EPSG:4326.

Default limit is 1500 and hard API limit is 5000. The repository reads at most the requested bound
from each origin, merges deterministically with existing facilities before generated facilities,
and the application service requests one extra row to set `truncated`.

## Properties

Existing features expose normalized source semantics:

- source dataset-version id and source feature id;
- facility class and name;
- optional source capacity;
- normalized source attributes.

Generated features expose the S10-T10 typed persistence contract:

- candidate id and infrastructure type code;
- category and capacity;
- greedy acceptance index;
- site/host geometry kind, host reference or site area;
- network snapshot/node/snap distance provenance;
- generated provenance attributes.

No routing, feasibility, placement or metrics are recomputed on reads.

## Ownership boundary

The application layer owns request validation, project/run lookup semantics, bbox/limit policy and
truncation. The SQLAlchemy repository owns PostGIS transforms, spatial predicates and persistence
joins. FastAPI controllers only map typed results/errors to HTTP schemas/status codes.

## Explicit non-goals

UG-AI-039 does not:

- implement infrastructure map controls or styling;
- add frontend run selection/error/truncation UX;
- recompute infrastructure metrics;
- expose candidate alternatives or demand/accessibility detail beyond persisted facilities.

Ordered follow-up:

- UG-AI-040 — add frontend infrastructure panel/layers;
- UG-AI-041 — add UI error/truncation/run-selection states while keeping computation authoritative
  on the backend.
