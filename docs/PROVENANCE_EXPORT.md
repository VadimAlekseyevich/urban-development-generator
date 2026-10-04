# Canonical Provenance JSON Export

## Scope

S13-T10 exposes the existing S12 canonical successful-run provenance manifest as a project-scoped
JSON download. It does **not** define a second provenance schema, recompute GIS results, rehash
source blobs, or synthesize a new configuration model.

- endpoint: `GET /api/v1/projects/{project_id}/exports/provenance/{run_id}`;
- only immutable `succeeded` generation runs are exportable;
- response bytes are exactly `SqlAlchemyProvenanceManifestService.build(...).content`;
- schema version remains provenance manifest `"1"`;
- checksum remains the manifest's canonical `sha256:<hex>` over those exact bytes.

The export is synchronous because it is bounded metadata assembled from persisted run, dataset,
stage artifact and evaluation records. Heavy spatial exports remain asynchronous GeoJSON/GeoPackage
jobs.

## Reproducibility fields

The downloaded document already contains all S13-T10 inputs without duplication:

- `run.id`, `run.project_id`, `run.mode`, `run.seed`, `run.working_srid` and
  `run.rerun_source_id`;
- `code.commit_sha`;
- `config.schema_version`, canonical config checksum and normalized `config.value`;
- sorted dataset/version refs with immutable source checksums and referenced source artifacts;
- sorted stage version/input/config/output fingerprints and referenced stage artifacts;
- persisted canonical evaluation identity and raw metrics.

The transport layer additionally verifies that the manifest run/project identity matches the URL,
that seed/working CRS remain present and typed, that normalized config metadata exists, and that
every dataset entry still carries `dataset_version_id`. This is a drift guard around the canonical
S12 manifest, not a replacement validator.

## Response

Successful response:

- `Content-Type: application/json`;
- `Content-Disposition: attachment; filename="provenance-<run_id>.json"`;
- `X-Provenance-Schema: 1`;
- `X-Provenance-Checksum: sha256:<64 lowercase hex>`.

Failure semantics:

- `404`: project or run is absent, or the run belongs to another project;
- `409`: the run is not yet an immutable successful run;
- `422`: malformed UUID path parameters;
- `500`: a successful run's persisted canonical provenance is incomplete or inconsistent.

The export never changes run state and never writes an Artifact. Durable export artifacts are not
needed for this bounded deterministic document; callers can verify the returned checksum directly.
